"""End-to-end authoring orchestration for F2A v5 public/private packages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Sequence

import duckdb

from synthetic_derivatives.mutation.f2a import (
    MutationSpec,
    apply_mutation,
    enumerate_specs,
)
from synthetic_derivatives.authoring.f2a_calibration import (
    calibration_report_checksum,
    validate_release_calibration_report,
)
from synthetic_derivatives.solver.f2a_v5 import solve_f2a_database
from synthetic_derivatives.verifier.f2a_database import (
    V5_PARENT_BUSINESS_DATE_COUNT,
    canonical_logical_checksum,
    extract_subset_db,
    iter_market_slices,
    load_option_observations,
    load_public_contracts,
    load_public_manifest,
    qualify_parent_db,
    sample_underlyings,
)
from synthetic_derivatives.verifier.f2a_model_signal import (
    MODEL_SIGNAL_VARIANT_ID,
    DatabaseSignalResult,
    scan_model_signal_slice,
    scan_model_signals,
)
from synthetic_derivatives.verifier.f2a_oracle import (
    PublicChild,
    PublicMarketSlice,
    scan_public_child,
)
from synthetic_derivatives.verifier.f2a_stage1 import UnderlyingFit
from synthetic_derivatives.verifier.f2a_stage2 import (
    OptionObservation,
    PricingCounterfactualContract,
    counterfactual_row,
    counterfactual_rows_by_id,
    fit_option_series,
    stable_row_id,
)
from synthetic_derivatives.verifier.f2a_v5 import build_canonical_answer


DEFAULT_TICK_GRID = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096)
NONZERO_SIGNATURES = ("001", "010", "011", "100", "101", "110", "111")
PILOT_TARGET_SIGNATURES = ("001", "010", "100", "101", "110", "111")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_private_generator_fits(
    parent_db: str | Path,
    selected_underlyings: Sequence[str],
) -> dict[str, UnderlyingFit]:
    """Read private generator nodes only inside the authoring environment."""

    connection = duckdb.connect(str(parent_db), read_only=True)
    try:
        result: dict[str, UnderlyingFit] = {}
        for underlying_id in sorted(selected_underlyings):
            row = connection.execute(
                """
                SELECT physical_dynamics
                FROM market.pricing_metadata
                WHERE underlying_id = ?
                ORDER BY valuation_date LIMIT 1
                """,
                [underlying_id],
            ).fetchone()
            if row is None:
                raise ValueError(f"private generator state is missing for {underlying_id}")
            physical = json.loads(row[0])
            drift_nodes = physical["drift_function"]["nodes"]
            diffusion_nodes = physical["volatility_function"]["nodes"]
            offsets = tuple(float(item["day_offset"]) for item in drift_nodes)
            if offsets != tuple(float(item["day_offset"]) for item in diffusion_nodes):
                raise ValueError("private drift/diffusion grids must be shared")
            size = len(offsets)
            zero = tuple(tuple(0.0 for _ in range(size)) for _ in range(size))
            full_zero = tuple(tuple(0.0 for _ in range(2 * size)) for _ in range(2 * size))
            result[underlying_id] = UnderlyingFit(
                underlying_id=underlying_id,
                node_offsets_calendar_days=offsets,
                fitted_drift_node_values=tuple(float(item["value"]) for item in drift_nodes),
                fitted_diffusion_node_values=tuple(float(item["value"]) for item in diffusion_nodes),
                observed_hessian=full_zero,
                diffusion_information_matrix=zero,
                diffusion_covariance_matrix=zero,
                diffusion_rse_by_node=tuple(0.0 for _ in range(size)),
                information_effective_sample_size_by_node=tuple(0.0 for _ in range(size)),
                objective_value=0.0,
                usable_return_count=0,
                hessian_condition_number=1.0,
                drift_active_bound_node_indices=(),
                solver_status="PRIVATE_GENERATOR_TRUTH",
                solver_evaluations=0,
            )
        return result
    finally:
        connection.close()


def _slice_counterfactual_rows(
    market: PublicMarketSlice,
    private_fit: UnderlyingFit,
    physical_contract,
    pricing_contract: PricingCounterfactualContract,
) -> dict[str, Any]:
    result = {}
    for quote in market.quotes:
        context = market.expiry_inputs[quote.expiry]
        observation = OptionObservation(
            row_id=stable_row_id(market.underlying_id, market.valuation_date, quote.option_id),
            option_contract_id=quote.option_id,
            option_id=quote.option_id,
            underlying_id=market.underlying_id,
            valuation_date=market.valuation_date,
            expiry=quote.expiry,
            option_type=quote.call_put,
            strike=float(quote.strike),
            spot=market.spot,
            bid=quote.bid,
            ask=quote.ask,
            contract_multiplier=quote.multiplier,
            integrated_rate=-math.log(context.discount_factor),
            integrated_dividend_or_carry=context.integrated_dividend_yield,
            settlement_style=quote.settlement_type,
            exercise_style=quote.exercise_style,
        )
        result[observation.row_id] = counterfactual_row(
            observation,
            physical_contract,
            private_fit,
            pricing_contract,
        )
    return result

def find_specs_for_private_signal_signatures(
    *,
    clean_slice: PublicMarketSlice,
    requested_signatures: Sequence[str],
    private_generator_fit: UnderlyingFit,
    physical_contract,
    pricing_contract: PricingCounterfactualContract,
    signal_contract,
    absolute_tick_grid: Sequence[int] = DEFAULT_TICK_GRID,
) -> dict[str, MutationSpec]:
    """Find each reachable requested signature with one shared local scan."""

    requested = tuple(requested_signatures)
    if (
        len(requested) != len(set(requested))
        or any(item not in NONZERO_SIGNATURES for item in requested)
    ):
        raise ValueError("private targets must be unique nonzero XUT signatures")
    if not requested:
        return {}
    clean_rows = _slice_counterfactual_rows(
        clean_slice,
        private_generator_fit,
        physical_contract,
        pricing_contract,
    )
    clean_result = scan_model_signal_slice(
        clean_slice,
        clean_rows,
        private_generator_fit,
        signal_contract,
    )
    candidate_indices_by_option: dict[str, set[int]] = {}
    for index, candidate in enumerate(clean_result.candidates):
        for row in candidate.rows:
            candidate_indices_by_option.setdefault(
                row.observation.option_id, set()
            ).add(index)
    target_context_cache: dict[
        tuple[str, ...], tuple[tuple[int, ...], dict[str, bool]]
    ] = {}
    found: dict[str, MutationSpec] = {}
    for spec in enumerate_specs(
        clean_slice,
        absolute_tick_grid=tuple(absolute_tick_grid),
    ):
        if spec.kind == "clean_control":
            continue
        if spec.kind != "underlying_spot_shift":
            target_key = tuple(sorted(spec.option_ids))
            if target_key not in target_context_cache:
                affected = set().union(
                    *(candidate_indices_by_option.get(option_id, set()) for option_id in target_key)
                )
                unaffected_active = {
                    family: any(
                        index not in affected
                        and candidate.family == family
                        and candidate.active
                        for index, candidate in enumerate(clean_result.candidates)
                    )
                    for family in ("X", "U", "T")
                }
                target_context_cache[target_key] = (
                    tuple(sorted(affected)),
                    unaffected_active,
                )
            affected, unaffected_active = target_context_cache[target_key]
            targets = set(target_key)
            delta = 0.01 * spec.delta_ticks
            bits = []
            for family in ("X", "U", "T"):
                active = unaffected_active[family]
                for index in affected:
                    candidate = clean_result.candidates[index]
                    if candidate.family != family:
                        continue
                    response = sum(
                        -position * row.observation.contract_multiplier * delta
                        for row, position in zip(candidate.rows, candidate.positions)
                        if row.observation.option_id in targets
                    )
                    canonical_edge = float(
                        Decimal(str(candidate.net_signal_edge + response)).quantize(
                            Decimal(1).scaleb(-signal_contract.output_precision),
                            rounding=ROUND_HALF_EVEN,
                        )
                    )
                    if canonical_edge > 0.0:
                        active = True
                        break
                bits.append(active)
            affine_signature = "".join("1" if bit else "0" for bit in bits)
            if affine_signature not in requested or affine_signature in found:
                continue
        try:
            mutated = apply_mutation(clean_slice, spec).market_slice
        except ValueError:
            continue
        rows = _slice_counterfactual_rows(
            mutated,
            private_generator_fit,
            physical_contract,
            pricing_contract,
        )
        result = scan_model_signal_slice(
            mutated,
            rows,
            private_generator_fit,
            signal_contract,
        )
        if result.signature in requested and result.signature not in found:
            found[result.signature] = spec
            if len(found) == len(requested):
                break
    return found


def find_spec_for_private_signal_signature(
    *,
    clean_slice: PublicMarketSlice,
    requested_signature: str,
    private_generator_fit: UnderlyingFit,
    physical_contract,
    pricing_contract: PricingCounterfactualContract,
    signal_contract,
    absolute_tick_grid: Sequence[int] = DEFAULT_TICK_GRID,
) -> MutationSpec:
    """Search one local slice against private generator-counterfactual truth."""

    found = find_specs_for_private_signal_signatures(
        clean_slice=clean_slice,
        requested_signatures=(requested_signature,),
        private_generator_fit=private_generator_fit,
        physical_contract=physical_contract,
        pricing_contract=pricing_contract,
        signal_contract=signal_contract,
        absolute_tick_grid=absolute_tick_grid,
    )
    if requested_signature in found:
        return found[requested_signature]
    raise ValueError(
        f"private model-signal signature {requested_signature} is unreachable on the frozen tick grid"
    )


def _match_signatures_to_unique_slices(
    targets: Sequence[str],
    eligible_keys: Mapping[str, Sequence[tuple[str, str]]],
) -> dict[str, tuple[str, str]] | None:
    """Return a deterministic maximum matching from signatures to slice keys."""

    signature_by_key: dict[tuple[str, str], str] = {}

    def augment(signature: str, seen: set[tuple[str, str]]) -> bool:
        for key in eligible_keys[signature]:
            if key in seen:
                continue
            seen.add(key)
            current = signature_by_key.get(key)
            if current is None or augment(current, seen):
                signature_by_key[key] = signature
                return True
        return False

    for signature in targets:
        if not augment(signature, set()):
            return None
    return {signature: key for key, signature in signature_by_key.items()}


def _apply_spec_to_subset(database: Path, spec: MutationSpec) -> dict[str, Any]:
    connection = duckdb.connect(str(database))
    connection.execute("BEGIN TRANSACTION")
    try:
        if spec.kind == "underlying_spot_shift":
            delta = spec.delta_ticks * 0.01
            count = connection.execute(
                """
                SELECT count(*) FROM solver_visible.underlying_daily
                WHERE date = ? AND underlying_id = ?
                """,
                [spec.valuation_date, spec.underlying_id],
            ).fetchone()[0]
            if count != 1:
                raise ValueError("spot mutation must resolve one public history row")
            connection.execute(
                """
                UPDATE solver_visible.underlying_daily
                SET spot_open = spot_open + ?, spot_high = spot_high + ?,
                    spot_low = spot_low + ?, spot_close = spot_close + ?,
                    adjusted_close = adjusted_close + ?
                WHERE date = ? AND underlying_id = ?
                """,
                [delta, delta, delta, delta, delta, spec.valuation_date, spec.underlying_id],
            )
            result = {
                "kind": spec.kind,
                "operator_id": spec.operator_id,
                "delta_ticks": spec.delta_ticks,
                "valuation_date": spec.valuation_date,
                "underlying_id": spec.underlying_id,
            }
        else:
            option_ids = tuple(spec.option_ids)
            placeholders = ",".join("?" for _ in option_ids)
            rows = connection.execute(
                f"""
                SELECT option_id, bid, ask
                FROM solver_visible.f2a_option_quotes
                WHERE date = ? AND underlying_id = ? AND option_id IN ({placeholders})
                ORDER BY option_id
                """,
                [spec.valuation_date, spec.underlying_id, *option_ids],
            ).fetchall()
            if len(rows) != len(option_ids):
                raise ValueError("option mutation did not resolve its complete public group")
            delta = Decimal("0.01") * spec.delta_ticks
            records = []
            for option_id, bid, ask in rows:
                new_bid = Decimal(str(bid)) + delta
                new_ask = Decimal(str(ask)) + delta
                if new_bid < 0 or new_ask < new_bid:
                    raise ValueError("mutated public quote is outside its domain")
                connection.execute(
                    """
                    UPDATE solver_visible.f2a_option_quotes
                    SET bid = ?, ask = ?
                    WHERE date = ? AND underlying_id = ? AND option_id = ?
                    """,
                    [new_bid, new_ask, spec.valuation_date, spec.underlying_id, option_id],
                )
                records.append(
                    {
                        "option_id": option_id,
                        "bid_before": str(bid),
                        "ask_before": str(ask),
                        "bid_after": str(new_bid),
                        "ask_after": str(new_ask),
                    }
                )
            result = {
                "kind": spec.kind,
                "operator_id": spec.operator_id,
                "delta_ticks": spec.delta_ticks,
                "valuation_date": spec.valuation_date,
                "underlying_id": spec.underlying_id,
                "quote_mutations": records,
            }
        connection.execute("COMMIT")
        return result
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def refresh_public_manifest(database: str | Path) -> dict[str, Any]:
    """Refresh the logical checksum after all child mutations, before publish."""

    path = Path(database)
    checksum = canonical_logical_checksum(path)
    connection = duckdb.connect(str(path))
    try:
        raw = connection.execute(
            "SELECT public_manifest FROM metadata.public_task"
        ).fetchone()
        if raw is None or raw[0] is None:
            raise ValueError("subset public manifest is missing")
        manifest = json.loads(raw[0])
        manifest["public_logical_checksum"] = checksum
        connection.execute(
            """
            UPDATE metadata.public_task
            SET public_logical_checksum = ?, public_manifest = ?
            """,
            [checksum, json.dumps(manifest, sort_keys=True)],
        )
        return manifest
    finally:
        connection.close()


def _finalize_public_identity(
    database: Path,
    *,
    mutation_seed: int,
    target_signatures: Sequence[str],
    mutation_lineage: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Give each materially distinct child its own deterministic identity."""

    manifest = load_public_manifest(database)
    identity_payload = {
        "variant_id": MODEL_SIGNAL_VARIANT_ID,
        "public_schema_version": manifest["schema_version"],
        "output_contract_id": manifest["output_contract_id"],
        "mutation_engine_id": "f2a-complete-mutation-v4",
        "sample_id": manifest["sample_id"],
        "parent_logical_checksum": manifest["parent_logical_checksum"],
        "mutation_seed": mutation_seed,
        "requested_signatures": list(target_signatures),
        "mutation_spec_ids": [item["mutation_spec_id"] for item in mutation_lineage],
    }
    token = sha256(
        json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    child_snapshot_id = "DERIVATIVES-F2A-V5-CHILD-" + token
    task_id = "task-f2a-v5-" + token
    connection = duckdb.connect(str(database))
    try:
        connection.execute("BEGIN TRANSACTION")
        for table in (
            "solver_visible.underlying_daily",
            "solver_visible.f2a_option_quotes",
            "solver_visible.option_contracts",
            "solver_visible.pricing_inputs",
        ):
            connection.execute(f"UPDATE {table} SET snapshot_id = ?", [child_snapshot_id])
        manifest["task_id"] = task_id
        manifest["child_snapshot_id"] = child_snapshot_id
        connection.execute(
            """
            UPDATE metadata.public_task
            SET task_id = ?, snapshot_id = ?, public_manifest = ?
            """,
            [task_id, child_snapshot_id, json.dumps(manifest, sort_keys=True)],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return refresh_public_manifest(database)


def _fit_audit(
    canonical_answer: Mapping[str, Any],
    *,
    residual_threshold: float,
    clean_max_outlier_fraction: float,
    require_quote_noise_gate: bool,
) -> dict[str, Any]:
    underlying_fits = tuple(canonical_answer["underlying_fits"])
    option_fits = tuple(canonical_answer["option_fits"])
    statuses = {
        item["underlying_id"]: item["solver_status"] for item in underlying_fits
    }
    residuals = [
        abs(float(value))
        for fit in option_fits
        for value in fit["standardized_residuals_by_row_id"].values()
    ]
    max_rse = max(
        float(value)
        for fit in underlying_fits
        for value in fit["diffusion_rse_by_node"]
    )
    stage1_passed = all(status == "CONVERGED" for status in statuses.values())
    stage2_passed = bool(option_fits) and all(
        fit["fit_status"] == "CONVERGED" for fit in option_fits
    )
    outlier_count = sum(value >= residual_threshold for value in residuals)
    outlier_fraction = outlier_count / len(residuals) if residuals else None
    quote_noise_passed = (
        outlier_fraction is not None
        and outlier_fraction <= clean_max_outlier_fraction
    )
    return {
        "underlying_count": len(underlying_fits),
        "option_series_count": len(option_fits),
        "stage1_status_by_underlying": statuses,
        "maximum_diffusion_node_rse": max_rse,
        "maximum_absolute_standardized_residual": max(residuals) if residuals else None,
        "residual_threshold": residual_threshold,
        "residual_outlier_count": outlier_count,
        "residual_observation_count": len(residuals),
        "residual_outlier_fraction": outlier_fraction,
        "clean_max_outlier_fraction": clean_max_outlier_fraction,
        "stage1_gate_passed": stage1_passed,
        "stage2_gate_passed": stage2_passed,
        "clean_quote_noise_gate_passed": quote_noise_passed,
        "quote_noise_gate_required": require_quote_noise_gate,
        "publication_fit_gate_passed": stage1_passed
        and stage2_passed
        and (quote_noise_passed or not require_quote_noise_gate),
    }


def _assert_reference_solver(database: Path, canonical_answer: Mapping[str, Any]) -> None:
    if solve_f2a_database(database) != canonical_answer:
        raise ValueError("independent public reference solver disagrees with the trusted verifier")


def _validate_release_report_for_task(
    report: Mapping[str, Any],
    *,
    parent_snapshot_id: str,
    parent_logical_checksum: str,
) -> str:
    validate_release_calibration_report(report)
    cohort = report.get("cohort_contract")
    if not isinstance(cohort, Mapping):
        raise ValueError("release calibration report lacks its cohort contract")
    expected = {
        "variant_id": MODEL_SIGNAL_VARIANT_ID,
        "parent_snapshot_id": parent_snapshot_id,
        "parent_logical_checksum": parent_logical_checksum,
        "business_date_count": V5_PARENT_BUSINESS_DATE_COUNT,
        "physical_node_count": 3,
        "node_grid_shape": "three_shared_nodes_within_public_horizon",
    }
    for key, value in expected.items():
        if cohort.get(key) != value:
            raise ValueError(f"release calibration cohort {key} does not match this task")
    return calibration_report_checksum(report)


def _private_truth_scan(
    database: Path,
    private_fits: Mapping[str, UnderlyingFit],
) -> DatabaseSignalResult:
    physical_contracts, pricing_contract, signal_contract = load_public_contracts(database)
    observations = load_option_observations(database)
    option_fits = fit_option_series(
        observations,
        physical_contracts,
        private_fits,
        pricing_contract,
    )
    rows = counterfactual_rows_by_id(option_fits)
    manifest = load_public_manifest(database)
    slices = tuple(iter_market_slices(database, tuple(manifest["selected_underlyings"])))
    return scan_model_signals(slices, rows, private_fits, signal_contract)


def _execution_audit(database: Path) -> dict[str, Any]:
    manifest = load_public_manifest(database)
    slices = tuple(iter_market_slices(database, tuple(manifest["selected_underlyings"])))
    child = PublicChild(
        task_id=manifest["task_id"],
        snapshot_id=manifest["child_snapshot_id"],
        snapshot_revision=1,
        status="FROZEN",
        slices=slices,
    )
    result = scan_public_child(child)
    return {
        "signature": result.realized_signature,
        "orm_answer": result.orm_answer(),
        "candidate_count": len(result.candidates),
        "active_candidate_ids": [
            item.candidate_id for item in result.candidates if item.is_arbitrage
        ],
        "claim_scope": "frozen-v4-catalogue-executable-audit",
    }


def _task_fp_fn_audit(
    public_answer: Mapping[str, Any],
    private_truth: DatabaseSignalResult,
) -> dict[str, Any]:
    public = {
        (item["valuation_date"], item["underlying_id"]): item["signature"]
        for item in public_answer["model_signals"]["slices"]
    }
    private = {
        (item.valuation_date, item.underlying_id): item.signature
        for item in private_truth.slices
        if item.signature != "000"
    }
    keys = sorted(
        {
            (item.valuation_date, item.underlying_id) for item in private_truth.slices
        }
    )
    rows = []
    for key in keys:
        public_signature = public.get(key, "000")
        private_signature = private.get(key, "000")
        rows.append(
            {
                "valuation_date": key[0],
                "underlying_id": key[1],
                "public_signature": public_signature,
                "private_signature": private_signature,
                "fp_families": [
                    family
                    for family, estimate, truth in zip("XUT", public_signature, private_signature)
                    if estimate == "1" and truth == "0"
                ],
                "fn_families": [
                    family
                    for family, estimate, truth in zip("XUT", public_signature, private_signature)
                    if estimate == "0" and truth == "1"
                ],
            }
        )
    public_task_signature = "".join(
        "1" if any(item["public_signature"][index] == "1" for item in rows) else "0"
        for index in range(3)
    )
    private_task_signature = "".join(
        "1" if any(item["private_signature"][index] == "1" for item in rows) else "0"
        for index in range(3)
    )
    return {
        "report_scope": "single_task_seed_diagnostic_not_cohort_release",
        "slice_count": len(rows),
        "exact_signature_count": sum(
            item["public_signature"] == item["private_signature"] for item in rows
        ),
        "any_fp": any(item["fp_families"] for item in rows),
        "any_fn": any(item["fn_families"] for item in rows),
        "public_task_signature": public_task_signature,
        "private_task_signature": private_task_signature,
        "task_hamming_error": sum(
            estimate != truth
            for estimate, truth in zip(public_task_signature, private_task_signature)
        ),
        "slice_audit": rows,
        "release_gate_role": "task_record_only_requires_separate_cohort_calibration",
    }


def _mutation_localisation_audit(
    canonical_answer: Mapping[str, Any],
    mutation_lineage: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    diagnosed_rows = {
        row_id
        for diagnosis in canonical_answer["mutation_diagnosis"]
        for row_id in diagnosis["affected_row_ids"]
    }
    records = []
    for mutation in mutation_lineage:
        expected_rows = {
            stable_row_id(
                str(mutation["underlying_id"]),
                str(mutation["valuation_date"]),
                str(item["option_id"]),
            )
            for item in mutation.get("quote_mutations", [])
        }
        if expected_rows:
            passed = expected_rows <= diagnosed_rows
            reason = "all mutated option rows exceed the frozen localisation threshold"
        else:
            prefix = f"{mutation['underlying_id']}|{mutation['valuation_date']}|"
            passed = any(row_id.startswith(prefix) for row_id in diagnosed_rows)
            reason = "spot mutation is localised through affected option counterfactual rows"
        records.append(
            {
                "mutation_spec_id": mutation["mutation_spec_id"],
                "expected_row_ids": sorted(expected_rows),
                "passed": passed,
                "rule": reason,
            }
        )
    return {
        "mutation_count": len(records),
        "records": records,
        "localisation_gate_passed": all(item["passed"] for item in records),
    }


def _signal_uncertainty_audit(
    canonical_answer: Mapping[str, Any],
    *,
    maximum_statistic_critical_value: float | None,
) -> dict[str, Any]:
    margins = [
        abs(float(signal["standardized_margin"]))
        for market_slice in canonical_answer["model_signals"]["slices"]
        for signal in market_slice["active_signals"]
    ]
    minimum = min(margins) if margins else None
    passed = (
        maximum_statistic_critical_value is not None
        and minimum is not None
        and minimum >= maximum_statistic_critical_value
    )
    return {
        "active_candidate_count": len(margins),
        "minimum_active_absolute_standardized_margin": minimum,
        "bootstrap_maximum_statistic_critical_value": maximum_statistic_critical_value,
        "active_signal_ambiguity_gate_passed": passed,
        "scope_note": (
            "inactive-candidate familywise stability is controlled by the private cohort gate"
        ),
    }


@dataclass(frozen=True)
class AuthoredTask:
    public_directory: Path
    private_directory: Path
    public_manifest: Mapping[str, Any]
    mutation_lineage: tuple[Mapping[str, Any], ...]
    release_status: str


def materialize_agent_task(
    *,
    parent_db: str | Path,
    output_root: str | Path,
    private_output_root: str | Path,
    sampling_seed: int,
    mutation_seed: int,
    target_signatures: Sequence[str] = (),
    publication_mode: str = "pilot",
    cohort_calibration_report: Mapping[str, Any] | None = None,
) -> AuthoredTask:
    """Sample, mutate, fully rescan, and atomically package one v5 task."""

    if publication_mode not in {"pilot", "release"}:
        raise ValueError("publication_mode must be pilot or release")
    if (
        isinstance(mutation_seed, bool)
        or not isinstance(mutation_seed, int)
        or mutation_seed < 0
    ):
        raise ValueError("mutation_seed must be a nonnegative integer")
    targets = tuple(target_signatures)
    if len(targets) != len(set(targets)):
        raise ValueError("requested private signatures must be unique")
    if publication_mode == "release" and targets != NONZERO_SIGNATURES:
        raise ValueError("a v5 release task must request all seven nonzero signatures")
    if publication_mode == "release" and cohort_calibration_report is None:
        raise ValueError("release publication requires a private cohort calibration report")
    qualification = qualify_parent_db(parent_db, run_estimator_gate=True)
    if not qualification.qualified:
        raise ValueError("v5 parent qualification failed: " + "; ".join(qualification.issues))
    calibration_checksum = None
    if publication_mode == "release":
        assert cohort_calibration_report is not None
        calibration_checksum = _validate_release_report_for_task(
            cohort_calibration_report,
            parent_snapshot_id=qualification.snapshot_id,
            parent_logical_checksum=canonical_logical_checksum(parent_db),
        )
    selected = sample_underlyings(
        qualification.underlying_universe,
        seed=sampling_seed,
    )
    public_root = Path(output_root)
    private_root = Path(private_output_root)
    public_root.mkdir(parents=True, exist_ok=True)
    private_root.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(
            prefix="f2a-v5-authoring-", dir=public_root
        ) as temporary,
        tempfile.TemporaryDirectory(
            prefix="f2a-v5-private-", dir=private_root
        ) as private_temporary,
    ):
        working = Path(temporary) / "market_subset.duckdb"
        subset = extract_subset_db(
            parent_db,
            working,
            selected,
            sampling_seed=sampling_seed,
        )
        private_fits = _load_private_generator_fits(parent_db, selected)
        physical_contracts, pricing_contract, signal_contract = load_public_contracts(working)
        slices = tuple(iter_market_slices(working, selected))
        clean_canonical = build_canonical_answer(working)
        _assert_reference_solver(working, clean_canonical)
        clean_private_truth = _private_truth_scan(working, private_fits)
        clean_execution = _execution_audit(working)
        clean_fit_audit = _fit_audit(
            clean_canonical,
            residual_threshold=pricing_contract.residual_threshold,
            clean_max_outlier_fraction=pricing_contract.clean_max_outlier_fraction,
            require_quote_noise_gate=True,
        )
        if publication_mode == "release" and not clean_fit_audit[
            "publication_fit_gate_passed"
        ]:
            raise ValueError("clean Stage-1/2 publication fit gate failed")
        ranked_slices = sorted(
            slices,
            key=lambda item: (
                sha256(
                    f"{mutation_seed}|{item.valuation_date}|{item.underlying_id}".encode("utf-8")
                ).digest(),
                item.valuation_date,
                item.underlying_id,
            ),
        )
        for signature in targets:
            if signature not in NONZERO_SIGNATURES:
                raise ValueError(f"unsupported requested private signature: {signature}")
        local_spec_cache: dict[tuple[str, str], dict[str, MutationSpec]] = {}
        eligible_keys: dict[str, list[tuple[str, str]]] = {
            signature: [] for signature in targets
        }
        assignment = None
        for market in ranked_slices:
            key = (market.valuation_date, market.underlying_id)
            specs = find_specs_for_private_signal_signatures(
                clean_slice=market,
                requested_signatures=targets,
                private_generator_fit=private_fits[market.underlying_id],
                physical_contract=physical_contracts[market.underlying_id],
                pricing_contract=pricing_contract,
                signal_contract=signal_contract,
            )
            local_spec_cache[key] = specs
            for signature in targets:
                if signature in specs:
                    eligible_keys[signature].append(key)
            assignment = _match_signatures_to_unique_slices(targets, eligible_keys)
            if assignment is not None:
                break
        if assignment is None:
            missing = [signature for signature in targets if not eligible_keys[signature]]
            raise ValueError(
                "could not assign unique slices for private signatures; unreachable: "
                + ",".join(missing)
            )

        lineage = []
        for signature in targets:
            key = assignment[signature]
            spec = local_spec_cache[key][signature]
            record = _apply_spec_to_subset(working, spec)
            record["requested_signature"] = signature
            record["mutation_spec_id"] = spec.stable_identity()
            lineage.append(record)
        manifest = _finalize_public_identity(
            working,
            mutation_seed=mutation_seed,
            target_signatures=targets,
            mutation_lineage=lineage,
        )
        canonical = build_canonical_answer(working)
        _assert_reference_solver(working, canonical)
        private_truth = _private_truth_scan(working, private_fits)
        execution = _execution_audit(working)
        fp_fn = _task_fp_fn_audit(canonical, private_truth)
        private_by_key = {
            (item.valuation_date, item.underlying_id): item.signature
            for item in private_truth.slices
        }
        for record in lineage:
            key = (str(record["valuation_date"]), str(record["underlying_id"]))
            realized = private_by_key[key]
            record["realized_private_signature"] = realized
            if realized != record["requested_signature"]:
                raise ValueError(
                    "final private full scan no longer realizes a requested signature"
                )
        child_fit_audit = _fit_audit(
            canonical,
            residual_threshold=pricing_contract.residual_threshold,
            clean_max_outlier_fraction=pricing_contract.clean_max_outlier_fraction,
            require_quote_noise_gate=False,
        )
        localisation_audit = _mutation_localisation_audit(canonical, lineage)
        critical_value = None
        if cohort_calibration_report is not None:
            raw_critical_value = cohort_calibration_report.get(
                "bootstrap_maximum_statistic", {}
            ).get("critical_value")
            if raw_critical_value is not None:
                critical_value = float(raw_critical_value)
        uncertainty_audit = _signal_uncertainty_audit(
            canonical,
            maximum_statistic_critical_value=critical_value,
        )
        if publication_mode == "release":
            if not child_fit_audit["publication_fit_gate_passed"]:
                raise ValueError("child Stage-1/2 publication fit gate failed")
            if not localisation_audit["localisation_gate_passed"]:
                raise ValueError("mutation-localisation publication gate failed")
            if not uncertainty_audit["active_signal_ambiguity_gate_passed"]:
                raise ValueError("signal-uncertainty publication gate failed")

        final_public = public_root / manifest["task_id"]
        final_private = private_root / manifest["task_id"].replace("task-", "private_", 1)
        if final_public.exists() or final_private.exists():
            raise FileExistsError("refusing to overwrite an existing v5 task package")
        release_status = (
            "RELEASE_READY_CALIBRATED"
            if publication_mode == "release"
            else "PILOT_REQUIRES_COHORT_CALIBRATION"
        )

        public_staging = Path(temporary) / "public_package"
        private_staging = Path(private_temporary) / "private_package"
        public_staging.mkdir()
        private_staging.mkdir()
        shutil.copy2(working, public_staging / "market_subset.duckdb")
        schema_source = Path(__file__).resolve().parents[3] / "schemas/submission-v5.schema.json"
        shutil.copy2(schema_source, public_staging / "answer_schema.json")
        _atomic_json(public_staging / "public_manifest.json", manifest)
        (public_staging / "task.md").write_text(
            "# F2A v5 full trajectory\n\n"
            "Fit the public three-node P-measure path estimator for all eight underlyings; "
            "construct linked-diffusion BSM d1/d2 counterfactuals; report deterministic "
            "residual localisation; then scan every public slice for post-cost model-based "
            "X/U/T signals. These signals are not executable-arbitrage certificates.\n",
            encoding="utf-8",
        )
        _atomic_json(private_staging / "canonical_answer.json", canonical)
        lineage_document = {
            "task_id": manifest["task_id"],
            "variant_id": MODEL_SIGNAL_VARIANT_ID,
            "parent_snapshot_id": qualification.snapshot_id,
            "child_snapshot_id": manifest["child_snapshot_id"],
            "selected_underlyings": list(selected),
            "sampling_seed": sampling_seed,
            "mutation_seed": mutation_seed,
            "requested_signatures": list(targets),
            "mutation_groups": lineage,
            "private_truth_scan": private_truth.to_dict(),
            "public_signal_scan": canonical["model_signals"],
            "execution_audit": execution,
            "fp_fn_task_audit": fp_fn,
        }
        _atomic_json(private_staging / "mutation_lineage.json", lineage_document)
        _atomic_json(
            private_staging / "clean_baseline_scan.json",
            {
                "public_model_signal_scan": clean_canonical["model_signals"],
                "private_truth_scan": clean_private_truth.to_dict(),
                "execution_audit": clean_execution,
            },
        )
        _atomic_json(private_staging / "clean_fit_audit.json", clean_fit_audit)
        _atomic_json(
            private_staging / "child_fit_audit.json",
            {
                **child_fit_audit,
                "mutation_localisation": localisation_audit,
                "signal_uncertainty": uncertainty_audit,
            },
        )
        _atomic_json(private_staging / "child_public_signal_scan.json", canonical["model_signals"])
        _atomic_json(private_staging / "child_private_truth_scan.json", private_truth.to_dict())
        _atomic_json(private_staging / "execution_audit.json", execution)
        _atomic_json(private_staging / "fp_fn_calibration_report.json", fp_fn)
        if cohort_calibration_report is not None:
            _atomic_json(
                private_staging / "cohort_calibration_report.json",
                cohort_calibration_report,
            )
        _atomic_json(
            private_staging / "generation_manifest.json",
            {
                "task_id": manifest["task_id"],
                "variant_id": MODEL_SIGNAL_VARIANT_ID,
                "parent_snapshot_id": qualification.snapshot_id,
                "child_snapshot_id": manifest["child_snapshot_id"],
                "selected_underlyings": list(selected),
                "sampling_seed": sampling_seed,
                "mutation_seed": mutation_seed,
                "requested_signatures": list(targets),
                "release_status": release_status,
                "cohort_calibration_report_checksum": calibration_checksum,
            },
        )
        os.replace(private_staging, final_private)
        os.replace(public_staging, final_public)
        return AuthoredTask(
            public_directory=final_public,
            private_directory=final_private,
            public_manifest=manifest,
            mutation_lineage=tuple(lineage),
            release_status=release_status,
        )


__all__ = [
    "AuthoredTask",
    "DEFAULT_TICK_GRID",
    "NONZERO_SIGNATURES",
    "PILOT_TARGET_SIGNATURES",
    "find_spec_for_private_signal_signature",
    "find_specs_for_private_signal_signatures",
    "materialize_agent_task",
    "refresh_public_manifest",
]
