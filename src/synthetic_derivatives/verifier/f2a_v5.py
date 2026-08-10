"""Semantic V0/V1/V2/V3 orchestration for the public F2A v5 trajectory."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

import duckdb

from synthetic_derivatives.verifier.f2a_database import (
    V5_OUTPUT_CONTRACT_ID,
    V5_PUBLIC_SCHEMA_VERSION,
    canonical_logical_checksum,
    iter_market_slices,
    load_option_observations,
    load_public_contracts,
    load_public_manifest,
    load_underlying_histories,
)
from synthetic_derivatives.verifier.f2a_model_signal import (
    MODEL_SIGNAL_VARIANT_ID,
    scan_model_signals,
)
from synthetic_derivatives.verifier.f2a_stage1 import fit_underlying_path
from synthetic_derivatives.verifier.f2a_stage2 import (
    evaluate_option_series,
    localize_mutations,
    stage2_rows_by_id,
)


FORBIDDEN_PUBLIC_KEYS = {
    "seed",
    "sampling_seed",
    "mutation_seed",
    "parameter_generator_seed",
    "physical_dynamics",
    "pricing_dynamics",
    "drift_node_values",
    "diffusion_node_values",
    "clean_quotes",
    "quote_noise_realization",
    "requested_signature",
    "private_truth_signature",
    "mutation_lineage",
    "canonical_answer",
    "fp_fn_flags",
}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _assert_finite(value: Any, path: str = "submission") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_finite(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains a non-JSON value")


def _scan_forbidden_keys(value: Any, path: str = "public") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"public artifact leaks forbidden field {path}.{key}")
            _scan_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden_keys(item, f"{path}[{index}]")


def precheck_public_artifact(database: str | Path) -> dict[str, Any]:
    """V0: identity, checksum, domains, and recursive public/private separation."""

    path = Path(database)
    if not path.is_file() or path.suffix.casefold() != ".duckdb":
        raise ValueError("F2A v5 requires a materialized public DuckDB")
    manifest = load_public_manifest(path)
    for key, expected in (
        ("schema_version", V5_PUBLIC_SCHEMA_VERSION),
        ("sampling_contract_id", "underlying-cluster-sha256-rank-v1"),
        ("variant_id", MODEL_SIGNAL_VARIANT_ID),
        ("output_contract_id", V5_OUTPUT_CONTRACT_ID),
        ("status", "FROZEN"),
        ("snapshot_revision", 1),
    ):
        if manifest.get(key) != expected:
            raise ValueError(f"public manifest {key} does not match the v5 contract")
    _scan_forbidden_keys(manifest)
    actual_checksum = canonical_logical_checksum(path)
    if actual_checksum != manifest.get("public_logical_checksum"):
        raise ValueError("public logical checksum does not match canonical rows")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        market_objects = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'market'
            """
        ).fetchone()[0]
        if market_objects:
            raise ValueError("public v5 child must not contain private market tables")
        user_tables = {
            (str(schema), str(table))
            for schema, table in connection.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                """
            ).fetchall()
        }
        expected_tables = {
            ("metadata", "public_task"),
            ("solver_visible", "underlying_daily"),
            ("solver_visible", "f2a_option_quotes"),
            ("solver_visible", "option_contracts"),
            ("solver_visible", "pricing_inputs"),
            ("solver_visible", "physical_node_locations"),
            ("solver_visible", "f2a_contracts"),
        }
        if user_tables != expected_tables:
            raise ValueError("public v5 child contains missing or unexpected tables")
        expected_columns = {
            "metadata.public_task": (
                "task_id", "sample_id", "variant_id", "output_contract_id",
                "snapshot_id", "snapshot_revision", "status", "parent_snapshot_id",
                "parent_snapshot_revision", "parent_logical_checksum",
                "public_logical_checksum", "public_manifest",
            ),
            "solver_visible.underlying_daily": (
                "snapshot_id", "date", "underlying_id", "spot_open", "spot_high",
                "spot_low", "spot_close", "adjusted_close", "volume", "dividend",
                "corporate_action",
            ),
            "solver_visible.f2a_option_quotes": (
                "snapshot_id", "date", "underlying_id", "option_id", "call_put",
                "strike", "expiry", "exercise_style", "settlement_type",
                "contract_multiplier", "bid", "ask", "volume", "open_interest",
            ),
            "solver_visible.option_contracts": (
                "snapshot_id", "option_id", "underlying_id", "call_put", "strike",
                "expiry", "exercise_style", "settlement_type", "contract_multiplier",
            ),
            "solver_visible.pricing_inputs": (
                "snapshot_id", "valuation_date", "underlying_id", "currency",
                "discount_curve", "risk_free_rate", "dividend_curve", "dividend_yield",
                "borrow_or_carry_rate", "calendar", "day_count",
            ),
            "solver_visible.physical_node_locations": (
                "underlying_id", "function_role", "node_index", "day_offset",
                "time_origin",
            ),
            "solver_visible.f2a_contracts": ("contract_kind", "contract"),
        }
        for table, expected in expected_columns.items():
            actual = tuple(row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall())
            if actual != expected:
                raise ValueError(f"public v5 table columns are not frozen for {table}")
        task_row_count = connection.execute(
            "SELECT count(*) FROM metadata.public_task"
        ).fetchone()[0]
        if task_row_count != 1:
            raise ValueError("public v5 child must contain exactly one task record")
        task_identity = connection.execute(
            """
            SELECT task_id, sample_id, variant_id, output_contract_id,
                   snapshot_id, snapshot_revision, status, parent_snapshot_id,
                   parent_snapshot_revision, parent_logical_checksum,
                   public_logical_checksum
            FROM metadata.public_task
            """
        ).fetchone()
        identity_names = (
            "task_id", "sample_id", "variant_id", "output_contract_id",
            "child_snapshot_id", "snapshot_revision", "status", "parent_snapshot_id",
            "parent_snapshot_revision", "parent_logical_checksum",
            "public_logical_checksum",
        )
        if any(
            manifest.get(name) != value
            for name, value in zip(identity_names, task_identity)
        ):
            raise ValueError("public task row and embedded manifest identities disagree")
        contracts = connection.execute(
            "SELECT contract_kind, contract FROM solver_visible.f2a_contracts"
        ).fetchall()
        for kind, raw in contracts:
            parsed = json.loads(raw)
            _scan_forbidden_keys(parsed, f"contract.{kind}")
            _assert_finite(parsed, f"contract.{kind}")
        if {kind for kind, _ in contracts} != {
            "physical_fitting_contract",
            "bsm_inversion_contract",
            "linked_diffusion_validation_contract",
            "model_signal_contract",
        }:
            raise ValueError("public v5.1 child has an incomplete estimator contract set")
        selected = tuple(manifest.get("selected_underlyings", []))
        if len(selected) != 8 or len(set(selected)) != 8 or selected != tuple(sorted(selected)):
            raise ValueError("public v5 task must contain exactly eight underlyings")

        actual_underlyings = tuple(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT underlying_id FROM solver_visible.underlying_daily ORDER BY underlying_id"
            ).fetchall()
        )
        if actual_underlyings != selected:
            raise ValueError("public history underlyings do not match the manifest sample")
        missing_dates = manifest.get("missing_dates_by_underlying", {})
        if not isinstance(missing_dates, Mapping) or set(missing_dates) != set(selected):
            raise ValueError("public manifest has an incomplete missing-date audit")
        if any(missing_dates[item] for item in selected):
            raise ValueError("public v5 task has an incomplete sampled history")

        expected_counts = manifest.get("table_row_counts", {})
        for table in (
            "solver_visible.underlying_daily",
            "solver_visible.f2a_option_quotes",
            "solver_visible.option_contracts",
            "solver_visible.pricing_inputs",
            "solver_visible.physical_node_locations",
        ):
            actual = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if expected_counts.get(table) != actual:
                raise ValueError(f"public manifest row count is stale for {table}")

        invalid_spots = connection.execute(
            """
            SELECT count(*) FROM solver_visible.underlying_daily
            WHERE NOT isfinite(spot_open) OR NOT isfinite(spot_high)
               OR NOT isfinite(spot_low) OR NOT isfinite(spot_close)
               OR NOT isfinite(adjusted_close) OR spot_low <= 0
               OR spot_low > least(spot_open, spot_close)
               OR greatest(spot_open, spot_close) > spot_high
            """
        ).fetchone()[0]
        if invalid_spots:
            raise ValueError("public underlying history violates finite positive OHLC semantics")

        pricing_contexts = connection.execute(
            """
            SELECT DISTINCT currency, CAST(discount_curve AS VARCHAR),
                            risk_free_rate, CAST(dividend_curve AS VARCHAR),
                            dividend_yield, borrow_or_carry_rate,
                            calendar, day_count
            FROM solver_visible.pricing_inputs
            """
        ).fetchall()
        for row in pricing_contexts:
            currency, raw_discount, rate, raw_dividend, dividend, carry, calendar, day_count = row
            numeric_values = (float(rate), float(dividend), float(carry))
            if (
                str(currency) != "USD"
                or numeric_values != (0.03, 0.01, 0.03 - 0.01)
                or not all(math.isfinite(value) for value in numeric_values)
                or str(calendar) != "WeekendsOnly"
                or str(day_count) != "Actual365Fixed"
            ):
                raise ValueError("public pricing input is outside the frozen common-Q context")
            try:
                discount_curve = json.loads(str(raw_discount))
                dividend_curve = json.loads(str(raw_dividend))
            except (json.JSONDecodeError, TypeError) as error:
                raise ValueError("public pricing curve JSON is invalid") from error
            if discount_curve != {"rate": float(rate), "type": "flat_continuous"}:
                raise ValueError("public discount curve disagrees with its scalar rate")
            if dividend_curve != {
                "type": "flat_continuous",
                "yield": float(dividend),
            }:
                raise ValueError("public dividend curve disagrees with its scalar yield")

        invalid_quotes = connection.execute(
            """
            SELECT count(*) FROM solver_visible.f2a_option_quotes
            WHERE NOT isfinite(bid) OR NOT isfinite(ask)
               OR NOT isfinite(strike) OR NOT isfinite(contract_multiplier)
               OR bid < 0 OR ask < bid OR strike <= 0 OR contract_multiplier <= 0
               OR expiry <= date OR exercise_style <> 'european'
               OR settlement_type <> 'cash'
               OR bid <> round(bid, 2) OR ask <> round(ask, 2)
            """
        ).fetchone()[0]
        if invalid_quotes:
            raise ValueError("public option quote is outside the frozen v5 domain or tick grid")

        duplicate_quote_rows = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT date, underlying_id, option_id, count(*) AS n
                FROM solver_visible.f2a_option_quotes
                GROUP BY date, underlying_id, option_id HAVING n <> 1
            )
            """
        ).fetchone()[0]
        if duplicate_quote_rows:
            raise ValueError("public option valuation row IDs are not unique")
        orphan_quotes = connection.execute(
            """
            SELECT count(*)
            FROM solver_visible.f2a_option_quotes AS quote
            LEFT JOIN solver_visible.option_contracts AS contract
              USING (snapshot_id, option_id, underlying_id)
            LEFT JOIN solver_visible.underlying_daily AS underlying
              USING (snapshot_id, date, underlying_id)
            LEFT JOIN solver_visible.pricing_inputs AS pricing
              ON pricing.snapshot_id = quote.snapshot_id
             AND pricing.valuation_date = quote.date
             AND pricing.underlying_id = quote.underlying_id
            WHERE contract.option_id IS NULL OR underlying.underlying_id IS NULL
               OR pricing.underlying_id IS NULL
               OR contract.call_put <> quote.call_put
               OR contract.strike <> quote.strike
               OR contract.expiry <> quote.expiry
               OR contract.exercise_style <> quote.exercise_style
               OR contract.settlement_type <> quote.settlement_type
               OR contract.contract_multiplier <> quote.contract_multiplier
            """
        ).fetchone()[0]
        if orphan_quotes:
            raise ValueError("public quotes have orphaned or inconsistent references")
        incomplete_slices = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT history.date, history.underlying_id,
                       count(quote.option_id) AS quote_count,
                       count(DISTINCT pricing.underlying_id) AS pricing_count
                FROM solver_visible.underlying_daily AS history
                LEFT JOIN solver_visible.f2a_option_quotes AS quote
                  USING (snapshot_id, date, underlying_id)
                LEFT JOIN solver_visible.pricing_inputs AS pricing
                  ON pricing.snapshot_id = history.snapshot_id
                 AND pricing.valuation_date = history.date
                 AND pricing.underlying_id = history.underlying_id
                GROUP BY history.date, history.underlying_id
                HAVING quote_count = 0 OR pricing_count <> 1
            )
            """
        ).fetchone()[0]
        if incomplete_slices:
            raise ValueError("public history contains a slice without quotes or pricing inputs")
        incomplete_pairs = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT date, underlying_id, expiry, strike, contract_multiplier,
                       count(DISTINCT call_put) AS side_count
                FROM solver_visible.f2a_option_quotes
                GROUP BY date, underlying_id, expiry, strike, contract_multiplier
                HAVING side_count <> 2
            )
            """
        ).fetchone()[0]
        if incomplete_pairs:
            raise ValueError("public option grid does not contain complete call/put pairs")

        node_rows = connection.execute(
            """
            SELECT underlying_id, function_role, node_index, day_offset, time_origin
            FROM solver_visible.physical_node_locations
            ORDER BY underlying_id, function_role, node_index
            """
        ).fetchall()
        node_grids: dict[str, dict[str, list[tuple[int, float, str]]]] = {}
        for underlying_id, role, index, offset, origin in node_rows:
            if role not in {"drift", "diffusion"} or not math.isfinite(float(offset)):
                raise ValueError("public physical-node location is invalid")
            node_grids.setdefault(str(underlying_id), {}).setdefault(str(role), []).append(
                (int(index), float(offset), str(origin))
            )
        if set(node_grids) != set(selected):
            raise ValueError("public node grids do not cover the sampled underlyings")
        for underlying_id, roles in node_grids.items():
            if set(roles) != {"drift", "diffusion"} or len(roles["drift"]) != 3:
                raise ValueError(f"{underlying_id} does not expose one shared three-node grid")
            if roles["drift"] != roles["diffusion"]:
                raise ValueError(f"{underlying_id} drift/diffusion node grids are not shared")
            if [item[0] for item in roles["drift"]] != [0, 1, 2]:
                raise ValueError(f"{underlying_id} node indices are not canonical")
            offsets = [item[1] for item in roles["drift"]]
            if any(right <= left for left, right in zip(offsets, offsets[1:])):
                raise ValueError(f"{underlying_id} node offsets are not strictly increasing")
            origin = date.fromisoformat(roles["drift"][0][2])
            final_date = connection.execute(
                """
                SELECT max(date) FROM solver_visible.underlying_daily
                WHERE underlying_id = ?
                """,
                [underlying_id],
            ).fetchone()[0]
            if offsets[-1] > (final_date - origin).days:
                raise ValueError(f"{underlying_id} has a scored node outside the public horizon")
    finally:
        connection.close()
    return manifest


def build_canonical_answer(database: str | Path) -> dict[str, Any]:
    """Recompute every scored semantic output from the public child only."""

    manifest = precheck_public_artifact(database)
    (
        physical_contracts,
        inversion_contract,
        validation_contract,
        signal_contract,
    ) = load_public_contracts(database)
    histories = load_underlying_histories(database)
    if set(histories) != set(manifest["selected_underlyings"]):
        raise ValueError("public underlying histories do not match the task universe")
    fits = {
        underlying_id: fit_underlying_path(
            underlying_id,
            histories[underlying_id][0],
            histories[underlying_id][1],
            physical_contracts[underlying_id],
        )
        for underlying_id in sorted(histories)
    }
    observations = load_option_observations(database)
    option_series_results = evaluate_option_series(
        observations,
        physical_contracts,
        fits,
        inversion_contract,
        validation_contract,
    )
    diagnoses = localize_mutations(option_series_results, validation_contract)
    row_lookup = stage2_rows_by_id(option_series_results)
    slices = tuple(
        iter_market_slices(
            database,
            tuple(manifest["selected_underlyings"]),
        )
    )
    model_signals = scan_model_signals(slices, row_lookup, fits, signal_contract)
    semantic = {
        "task_id": manifest["task_id"],
        "snapshot_id": manifest["child_snapshot_id"],
        "snapshot_revision": manifest["snapshot_revision"],
        "variant_id": MODEL_SIGNAL_VARIANT_ID,
        "output_contract_id": V5_OUTPUT_CONTRACT_ID,
        "underlying_fits": [fits[key].to_dict() for key in sorted(fits)],
        "option_series_results": [
            item.to_dict() for item in option_series_results
        ],
        "mutation_diagnosis": [item.to_dict() for item in diagnoses],
        "model_signals": model_signals.to_dict(),
        "execution_audit": {
            "required": False,
            "scope": "independent-v4-catalogue-audit-not-scored-by-v5",
        },
    }
    semantic["verifier_digest"] = sha256(canonical_json_bytes(semantic)).hexdigest()
    return semantic


def compare_semantic_layers(
    expected: Mapping[str, Any],
    submitted: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return structured V0/V1/V2/V3 exact-comparison results."""

    _assert_finite(submitted)
    identity_keys = (
        "task_id",
        "snapshot_id",
        "snapshot_revision",
        "variant_id",
        "output_contract_id",
        "verifier_digest",
    )
    layers = {
        "V0": all(submitted.get(key) == expected.get(key) for key in identity_keys),
        "V1": submitted.get("underlying_fits") == expected.get("underlying_fits"),
        "V2": (
            submitted.get("option_series_results")
            == expected.get("option_series_results")
            and submitted.get("mutation_diagnosis") == expected.get("mutation_diagnosis")
        ),
        "V3": submitted.get("model_signals") == expected.get("model_signals"),
    }
    execution_valid = submitted.get("execution_audit") == expected.get("execution_audit")
    return {
        name: {
            "accepted": accepted,
            "failure": None if accepted else f"{name} canonical output mismatch",
        }
        for name, accepted in (
            *layers.items(),
            ("V_exec_audit", execution_valid),
        )
    }


__all__ = [
    "FORBIDDEN_PUBLIC_KEYS",
    "build_canonical_answer",
    "canonical_json_bytes",
    "compare_semantic_layers",
    "precheck_public_artifact",
]
