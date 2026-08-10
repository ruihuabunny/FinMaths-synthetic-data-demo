"""Independent public-only reference Solver for the F2A v5 trajectory."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import duckdb

from synthetic_derivatives.solver.f2a_v5_model_signal import (
    MODEL_SIGNAL_VARIANT_ID,
    ModelSignalContract,
    scan_model_signals,
)
from synthetic_derivatives.solver.f2a_v5_stage1 import (
    PhysicalFittingContract,
    fit_underlying_path,
)
from synthetic_derivatives.solver.f2a_v5_stage2 import (
    BSMInversionContract,
    LinkedDiffusionValidationContract,
    OptionObservation,
    evaluate_option_series,
    localize_mutations,
    stable_row_id,
    stage2_rows_by_id,
)
from synthetic_derivatives.solver.f2a_v5_types import (
    ExpiryInputs,
    OptionQuote,
    PublicMarketSlice,
)


OUTPUT_CONTRACT_ID = "model-reconstruction-xut-full-trajectory-v2"
PUBLIC_SCHEMA_VERSION = "f2a-public-duckdb-v5.1.0"
SAMPLING_CONTRACT_ID = "underlying-cluster-sha256-rank-v1"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("public logical row is non-finite")
        return format(value, ".17g")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
    return value


def _public_checksum(connection: duckdb.DuckDBPyConnection) -> str:
    tables = (
        ("solver_visible.underlying_daily", "SELECT * FROM solver_visible.underlying_daily ORDER BY date, underlying_id"),
        ("solver_visible.f2a_option_quotes", "SELECT * FROM solver_visible.f2a_option_quotes ORDER BY date, underlying_id, expiry, strike, call_put, option_id"),
        ("solver_visible.option_contracts", "SELECT * FROM solver_visible.option_contracts ORDER BY underlying_id, expiry, strike, call_put, option_id"),
        ("solver_visible.pricing_inputs", "SELECT * FROM solver_visible.pricing_inputs ORDER BY valuation_date, underlying_id"),
        ("solver_visible.physical_node_locations", "SELECT * FROM solver_visible.physical_node_locations ORDER BY underlying_id, function_role, node_index"),
        ("solver_visible.f2a_contracts", "SELECT * FROM solver_visible.f2a_contracts ORDER BY contract_kind"),
    )
    hasher = sha256()
    for table, query in tables:
        hasher.update(table.encode("utf-8"))
        hasher.update(b"\0")
        for row in connection.execute(query).fetchall():
            payload = json.dumps(
                [_canonical_value(value) for value in row],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            hasher.update(payload.encode("utf-8"))
            hasher.update(b"\n")
    return hasher.hexdigest()


def _manifest(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT public_manifest FROM metadata.public_task"
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("public v5 task manifest is missing")
    manifest = json.loads(row[0])
    if (
        manifest.get("schema_version") != PUBLIC_SCHEMA_VERSION
        or manifest.get("sampling_contract_id") != SAMPLING_CONTRACT_ID
        or manifest.get("variant_id") != MODEL_SIGNAL_VARIANT_ID
        or manifest.get("output_contract_id") != OUTPUT_CONTRACT_ID
        or manifest.get("status") != "FROZEN"
        or manifest.get("snapshot_revision") != 1
        or len(manifest.get("selected_underlyings", [])) != 8
    ):
        raise ValueError("public task identity is not the frozen v5 contract")
    if _public_checksum(connection) != manifest.get("public_logical_checksum"):
        raise ValueError("public logical checksum mismatch")
    return manifest


def _contracts(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[
    dict[str, PhysicalFittingContract],
    BSMInversionContract,
    LinkedDiffusionValidationContract,
    ModelSignalContract,
]:
    raw = {
        row[0]: json.loads(row[1])
        for row in connection.execute(
            "SELECT contract_kind, contract FROM solver_visible.f2a_contracts"
        ).fetchall()
    }
    node_rows = connection.execute(
        """
        SELECT underlying_id, node_index, day_offset, time_origin
        FROM solver_visible.physical_node_locations
        WHERE function_role = 'diffusion'
        ORDER BY underlying_id, node_index
        """
    ).fetchall()
    offsets: dict[str, list[float]] = {}
    origins: dict[str, str] = {}
    for underlying_id, _, offset, origin in node_rows:
        offsets.setdefault(str(underlying_id), []).append(float(offset))
        origins[str(underlying_id)] = str(origin)
    physical = {
        underlying_id: PhysicalFittingContract.from_mapping(
            {
                **raw["physical_fitting_contract"],
                "time_origin": origins[underlying_id],
                "node_offsets_calendar_days": nodes,
            }
        )
        for underlying_id, nodes in offsets.items()
    }
    return (
        physical,
        BSMInversionContract.from_mapping(raw["bsm_inversion_contract"]),
        LinkedDiffusionValidationContract.from_mapping(
            raw["linked_diffusion_validation_contract"]
        ),
        ModelSignalContract.from_mapping(raw["model_signal_contract"]),
    )


def _histories(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[tuple[str, ...], tuple[float, ...]]]:
    rows = connection.execute(
        """
        SELECT underlying_id, date, spot_close
        FROM solver_visible.underlying_daily
        ORDER BY underlying_id, date
        """
    ).fetchall()
    grouped: dict[str, list[tuple[str, float]]] = {}
    for underlying_id, observation_date, close in rows:
        grouped.setdefault(str(underlying_id), []).append(
            (str(observation_date), float(close))
        )
    return {
        key: (
            tuple(item[0] for item in values),
            tuple(item[1] for item in values),
        )
        for key, values in grouped.items()
    }


def _observations(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[OptionObservation, ...]:
    rows = connection.execute(
        """
        SELECT quote.date, quote.underlying_id, quote.option_id,
               quote.call_put, quote.strike, quote.expiry,
               quote.exercise_style, quote.settlement_type,
               quote.contract_multiplier, quote.bid, quote.ask,
               underlying.spot_close, pricing.risk_free_rate,
               pricing.dividend_yield
        FROM solver_visible.f2a_option_quotes AS quote
        JOIN solver_visible.underlying_daily AS underlying
          USING (snapshot_id, date, underlying_id)
        JOIN solver_visible.pricing_inputs AS pricing
          ON pricing.snapshot_id = quote.snapshot_id
         AND pricing.valuation_date = quote.date
         AND pricing.underlying_id = quote.underlying_id
        ORDER BY quote.underlying_id, quote.option_id, quote.date
        """
    ).fetchall()
    result = []
    for row in rows:
        (
            valuation_date, underlying_id, option_id, option_type, strike,
            expiry, exercise_style, settlement_type, multiplier, bid, ask,
            spot, rate, dividend,
        ) = row
        years = (expiry - valuation_date).days / 365.0
        result.append(
            OptionObservation(
                row_id=stable_row_id(str(underlying_id), str(valuation_date), str(option_id)),
                option_contract_id=str(option_id),
                option_id=str(option_id),
                underlying_id=str(underlying_id),
                valuation_date=str(valuation_date),
                expiry=str(expiry),
                option_type=str(option_type),
                strike=float(strike),
                spot=float(spot),
                bid=float(bid),
                ask=float(ask),
                contract_multiplier=float(multiplier),
                integrated_rate=float(rate) * years,
                integrated_dividend_or_carry=float(dividend) * years,
                settlement_style=str(settlement_type),
                exercise_style=str(exercise_style),
            )
        )
    return tuple(result)


def _market_slices(
    connection: duckdb.DuckDBPyConnection,
    manifest: Mapping[str, Any],
) -> tuple[PublicMarketSlice, ...]:
    ids = tuple(sorted(manifest["selected_underlyings"]))
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        SELECT quote.date, quote.underlying_id, underlying.spot_close,
               pricing.currency, quote.option_id, quote.expiry,
               quote.call_put, quote.strike, quote.bid, quote.ask,
               quote.contract_multiplier, quote.exercise_style,
               quote.settlement_type, pricing.risk_free_rate,
               pricing.dividend_yield
        FROM solver_visible.f2a_option_quotes AS quote
        JOIN solver_visible.underlying_daily AS underlying
          USING (snapshot_id, date, underlying_id)
        JOIN solver_visible.pricing_inputs AS pricing
          ON pricing.snapshot_id = quote.snapshot_id
         AND pricing.valuation_date = quote.date
         AND pricing.underlying_id = quote.underlying_id
        WHERE quote.underlying_id IN ({placeholders})
        ORDER BY quote.date, quote.underlying_id, quote.expiry,
                 quote.strike, quote.call_put, quote.option_id
        """,
        list(ids),
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        valuation_date = str(row[0])
        underlying_id = str(row[1])
        key = valuation_date, underlying_id
        item = grouped.setdefault(
            key,
            {
                "spot": float(row[2]),
                "currency": str(row[3]),
                "expiry_inputs": {},
                "quotes": [],
            },
        )
        expiry = str(row[5])
        years = (date.fromisoformat(expiry) - date.fromisoformat(valuation_date)).days / 365.0
        item["expiry_inputs"].setdefault(
            expiry,
            ExpiryInputs(
                discount_factor=math.exp(-float(row[13]) * years),
                integrated_dividend_yield=float(row[14]) * years,
            ),
        )
        item["quotes"].append(
            OptionQuote(
                option_id=str(row[4]),
                expiry=expiry,
                call_put=str(row[6]),
                strike=Decimal(str(float(row[7]))),
                bid=float(row[8]),
                ask=float(row[9]),
                multiplier=float(row[10]),
                exercise_style=str(row[11]),
                settlement_type=str(row[12]),
                valuation_date=valuation_date,
                underlying_id=underlying_id,
                currency=str(row[3]),
            )
        )
    return tuple(
        PublicMarketSlice(
            snapshot_id=manifest["child_snapshot_id"],
            snapshot_revision=1,
            valuation_date=valuation_date,
            underlying_id=underlying_id,
            spot=item["spot"],
            currency=item["currency"],
            quotes=tuple(item["quotes"]),
            expiry_inputs=item["expiry_inputs"],
        )
        for (valuation_date, underlying_id), item in sorted(grouped.items())
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def solve_f2a_database(database: str | Path) -> dict[str, Any]:
    """Return the canonical v5 submission using no trusted-verifier imports."""

    connection = duckdb.connect(str(Path(database)), read_only=True)
    try:
        manifest = _manifest(connection)
        (
            physical_contracts,
            inversion_contract,
            validation_contract,
            signal_contract,
        ) = _contracts(connection)
        histories = _histories(connection)
        fits = {
            underlying_id: fit_underlying_path(
                underlying_id,
                histories[underlying_id][0],
                histories[underlying_id][1],
                physical_contracts[underlying_id],
            )
            for underlying_id in sorted(histories)
        }
        option_series_results = evaluate_option_series(
            _observations(connection),
            physical_contracts,
            fits,
            inversion_contract,
            validation_contract,
        )
        diagnoses = localize_mutations(option_series_results, validation_contract)
        model_signals = scan_model_signals(
            _market_slices(connection, manifest),
            stage2_rows_by_id(option_series_results),
            fits,
            signal_contract,
        )
    finally:
        connection.close()
    submission = {
        "task_id": manifest["task_id"],
        "snapshot_id": manifest["child_snapshot_id"],
        "snapshot_revision": 1,
        "variant_id": MODEL_SIGNAL_VARIANT_ID,
        "output_contract_id": OUTPUT_CONTRACT_ID,
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
    submission["verifier_digest"] = sha256(_canonical_json_bytes(submission)).hexdigest()
    return submission


__all__ = ["OUTPUT_CONTRACT_ID", "solve_f2a_database"]
