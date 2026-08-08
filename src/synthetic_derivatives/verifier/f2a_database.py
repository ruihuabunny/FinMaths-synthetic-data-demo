"""Database-level public adapter and subset contracts for F2A v5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import duckdb

from synthetic_derivatives.verifier.f2a_model_signal import (
    MODEL_SIGNAL_VARIANT_ID,
    ModelSignalContract,
)
from synthetic_derivatives.verifier.f2a_oracle import (
    ExpiryInputs,
    PublicMarketSlice,
    market_slice_from_dict,
)
from synthetic_derivatives.verifier.f2a_stage1 import (
    PhysicalFittingContract,
    UnderlyingFit,
    fit_underlying_path,
)
from synthetic_derivatives.verifier.f2a_stage2 import (
    OptionObservation,
    PricingCounterfactualContract,
    stable_row_id,
)


V5_SAMPLE_SIZE = 8
V5_PARENT_UNDERLYING_COUNT = 22
V5_PARENT_BUSINESS_DATE_COUNT = 126
V5_PARENT_SNAPSHOT_ID = (
    "DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1"
)
V5_PUBLIC_SCHEMA_VERSION = "f2a-public-duckdb-v5.0.0"
V5_OUTPUT_CONTRACT_ID = "model-reconstruction-xut-full-trajectory-v1"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("logical checksum input must be finite")
        return format(value, ".17g")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
    return value


def _hash_rows(hasher, table: str, rows: Iterable[Sequence[Any]]) -> None:
    hasher.update(table.encode("utf-8"))
    hasher.update(b"\0")
    for row in rows:
        payload = json.dumps(
            [_canonical_value(value) for value in row],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        hasher.update(payload.encode("utf-8"))
        hasher.update(b"\n")


def _table_exists(connection: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    return bool(
        connection.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchone()[0]
    )


def canonical_logical_checksum(database: str | Path) -> str:
    """Hash canonical sorted logical public rows, never DuckDB file bytes."""

    connection = duckdb.connect(str(database), read_only=True)
    try:
        hasher = sha256()
        if _table_exists(connection, "solver_visible", "f2a_option_quotes"):
            tables = (
                ("solver_visible.underlying_daily", "SELECT * FROM solver_visible.underlying_daily ORDER BY date, underlying_id"),
                ("solver_visible.f2a_option_quotes", "SELECT * FROM solver_visible.f2a_option_quotes ORDER BY date, underlying_id, expiry, strike, call_put, option_id"),
                ("solver_visible.option_contracts", "SELECT * FROM solver_visible.option_contracts ORDER BY underlying_id, expiry, strike, call_put, option_id"),
                ("solver_visible.pricing_inputs", "SELECT * FROM solver_visible.pricing_inputs ORDER BY valuation_date, underlying_id"),
                ("solver_visible.physical_node_locations", "SELECT * FROM solver_visible.physical_node_locations ORDER BY underlying_id, function_role, node_index"),
                ("solver_visible.f2a_contracts", "SELECT * FROM solver_visible.f2a_contracts ORDER BY contract_kind"),
            )
        else:
            tables = (
                ("market.underlying_daily", "SELECT * FROM market.underlying_daily ORDER BY snapshot_id, date, underlying_id"),
                ("market.option_daily", "SELECT * FROM market.option_daily ORDER BY snapshot_id, date, underlying_id, expiry, strike, call_put, option_id"),
                ("market.option_contracts", "SELECT * FROM market.option_contracts ORDER BY snapshot_id, underlying_id, expiry, strike, call_put, option_id"),
                (
                    "market.pricing_metadata",
                    """
                    SELECT snapshot_id, CAST(valuation_timestamp AS VARCHAR),
                           valuation_date, underlying_id, currency,
                           discount_curve, risk_free_rate, dividend_curve,
                           dividend_yield, borrow_or_carry_rate, calendar,
                           day_count, physical_dynamics, pricing_dynamics,
                           pricing_model, pricing_engine, generator_version,
                           seed, rng, input_precision, canonicalization,
                           generated_run_id
                    FROM market.pricing_metadata
                    ORDER BY snapshot_id, valuation_date, underlying_id
                    """,
                ),
            )
        for table, query in tables:
            cursor = connection.execute(query)
            _hash_rows(hasher, table, cursor.fetchall())
        return hasher.hexdigest()
    finally:
        connection.close()


def sample_underlyings(
    universe: Sequence[str],
    *,
    sample_size: int = V5_SAMPLE_SIZE,
    seed: int,
) -> tuple[str, ...]:
    """Deterministic without-replacement cluster sample in canonical order."""

    canonical = tuple(sorted(str(item) for item in universe))
    if len(canonical) != len(set(canonical)):
        raise ValueError("parent underlying universe must already be deduplicated")
    if sample_size != V5_SAMPLE_SIZE:
        raise ValueError("the F2A v5 variant requires sample_size = 8")
    if len(canonical) != V5_PARENT_UNDERLYING_COUNT:
        raise ValueError("the F2A v5 sampler requires the frozen 22-underlying universe")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("sampling seed must be a nonnegative integer")
    ranked = sorted(
        canonical,
        key=lambda underlying_id: (
            sha256(f"f2a-v5|{seed}|{underlying_id}".encode("utf-8")).digest(),
            underlying_id,
        ),
    )
    return tuple(sorted(ranked[:sample_size]))


def stable_sample_id(
    *,
    parent_logical_checksum: str,
    seed: int,
    underlying_ids: Sequence[str],
    variant_id: str = MODEL_SIGNAL_VARIANT_ID,
) -> str:
    payload = json.dumps(
        {
            "variant_id": variant_id,
            "sampling_contract_id": "underlying-cluster-sha256-rank-v1",
            "parent_logical_checksum": parent_logical_checksum,
            "sampling_seed": seed,
            "underlying_ids": sorted(underlying_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "f2a-v5-sample-" + sha256(payload.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class ParentQualification:
    qualified: bool
    snapshot_id: str
    snapshot_revision: int
    snapshot_status: str
    underlying_universe: tuple[str, ...]
    observation_dates: tuple[str, ...]
    node_counts: Mapping[str, int]
    issues: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "snapshot_id": self.snapshot_id,
            "snapshot_revision": self.snapshot_revision,
            "snapshot_status": self.snapshot_status,
            "underlying_universe": list(self.underlying_universe),
            "observation_dates": list(self.observation_dates),
            "node_counts": dict(sorted(self.node_counts.items())),
            "issues": list(self.issues),
            "diagnostics": list(self.diagnostics),
        }


def _private_node_contracts(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, PhysicalFittingContract]:
    rows = connection.execute(
        """
        SELECT underlying_id, min(valuation_date), physical_dynamics, pricing_dynamics
        FROM market.pricing_metadata
        GROUP BY underlying_id, physical_dynamics, pricing_dynamics
        ORDER BY underlying_id, min(valuation_date)
        """
    ).fetchall()
    contracts: dict[str, PhysicalFittingContract] = {}
    identities: dict[str, tuple[str, tuple[float, ...]]] = {}
    common_q_identities: set[tuple[str, str, str]] = set()
    for raw_underlying_id, _, raw, raw_pricing in rows:
        underlying_id = str(raw_underlying_id)
        physical = json.loads(raw)
        drift = physical.get("drift_function", {})
        diffusion = physical.get("volatility_function", {})
        if (
            physical.get("measure") != "P"
            or physical.get("process") != "QuantLib.BlackScholesMertonProcess"
            or physical.get("time_axis") != "calendar_day_offset/Actual365Fixed"
            or drift.get("type") != "piecewise_linear"
            or diffusion.get("type") != "piecewise_linear"
            or drift.get("extrapolation") != "flat"
            or diffusion.get("extrapolation") != "flat"
        ):
            raise ValueError(f"{underlying_id} does not declare the frozen P-measure TIH-GBM law")
        drift_nodes = drift.get("nodes", [])
        diffusion_nodes = diffusion.get("nodes", [])
        drift_offsets = tuple(float(item["day_offset"]) for item in drift_nodes)
        diffusion_offsets = tuple(float(item["day_offset"]) for item in diffusion_nodes)
        if drift_offsets != diffusion_offsets:
            raise ValueError(f"{underlying_id} does not use a shared drift/diffusion grid")
        drift_values = tuple(float(item["value"]) for item in drift_nodes)
        diffusion_values = tuple(float(item["value"]) for item in diffusion_nodes)
        if (
            any(not math.isfinite(value) or not -0.05 <= value <= 0.15 for value in drift_values)
            or any(not math.isfinite(value) or not 0.05 <= value <= 0.80 for value in diffusion_values)
        ):
            raise ValueError(f"{underlying_id} has invalid private physical node values")
        pricing = json.loads(raw_pricing)
        q_pricing = pricing.get("q_pricing", {})
        pricing_nodes = pricing.get("volatility_function", {}).get("nodes", [])
        pricing_grid = tuple(
            (float(item["day_offset"]), float(item["value"]))
            for item in pricing_nodes
        )
        physical_grid = tuple(zip(diffusion_offsets, diffusion_values))
        if (
            pricing.get("measure") != "Q"
            or pricing.get("process") != "QuantLib.BlackScholesMertonProcess"
            or q_pricing.get("measure_change") != "girsanov_drift_only"
            or q_pricing.get("volatility_mapping") != "same_deterministic_diffusion"
            or "implied_volatility_solver" in q_pricing
            or pricing_grid != physical_grid
        ):
            raise ValueError(f"{underlying_id} does not declare the linked common-Q BSM law")
        common_q_identities.add(
            (
                str(q_pricing.get("risk_neutral_measure_id")),
                str(q_pricing.get("numeraire_id")),
                str(q_pricing.get("rate_path_id")),
            )
        )
        identity = str(physical["time_origin"]), drift_offsets
        if underlying_id in identities and identities[underlying_id] != identity:
            raise ValueError(f"{underlying_id} changes its physical node contract by date")
        identities[underlying_id] = identity
        contracts.setdefault(
            underlying_id,
            PhysicalFittingContract(
                time_origin=identity[0],
                node_offsets_calendar_days=identity[1],
            ),
        )
    if len(common_q_identities) != 1 or any(
        value == "None" for value in next(iter(common_q_identities), ())
    ):
        raise ValueError("parent underlyings do not share one Q, numeraire and rate path")
    return contracts


def qualify_parent_db(
    database: str | Path,
    *,
    run_estimator_gate: bool = False,
) -> ParentQualification:
    """Check whether a frozen parent can identify the public 3-node trajectory."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"F2A v5 parent database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    issues: list[str] = []
    diagnostics: list[str] = []
    try:
        snapshot_rows = connection.execute(
            """
            SELECT snapshot_id, current_revision, status
            FROM metadata.snapshots ORDER BY snapshot_id
            """
        ).fetchall()
        if len(snapshot_rows) != 1:
            raise ValueError("v5 parent must contain exactly one snapshot identity")
        snapshot_id, revision, status = snapshot_rows[0]
        if snapshot_id != V5_PARENT_SNAPSHOT_ID:
            issues.append("parent snapshot identity is not the distinct F2A v5 parent")
        if revision != 1:
            issues.append("parent snapshot revision is not 1")
        if status != "FROZEN":
            issues.append("parent snapshot is not FROZEN")
        universe = tuple(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT underlying_id FROM market.underlying_daily ORDER BY underlying_id"
            ).fetchall()
        )
        dates = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT date FROM market.underlying_daily ORDER BY date"
            ).fetchall()
        )
        if len(universe) != V5_PARENT_UNDERLYING_COUNT:
            issues.append("parent does not contain the frozen 22-underlying universe")
        if len(dates) != V5_PARENT_BUSINESS_DATE_COUNT:
            issues.append("parent does not contain exactly 126 public business dates")
        try:
            contracts = _private_node_contracts(connection)
        except (KeyError, TypeError, ValueError) as error:
            contracts = {}
            issues.append(f"physical node contract is invalid: {error}")
        node_counts = {
            underlying_id: len(contract.node_offsets_calendar_days)
            for underlying_id, contract in contracts.items()
        }
        for underlying_id in universe:
            contract = contracts.get(underlying_id)
            if contract is None:
                issues.append(f"{underlying_id} lacks a physical node contract")
                continue
            if len(contract.node_offsets_calendar_days) != 3:
                issues.append(f"{underlying_id} does not use exactly three scored nodes")
                continue
            observations = connection.execute(
                """
                SELECT date, spot_close
                FROM market.underlying_daily
                WHERE underlying_id = ?
                ORDER BY date
                """,
                [underlying_id],
            ).fetchall()
            if len(observations) != V5_PARENT_BUSINESS_DATE_COUNT:
                issues.append(f"{underlying_id} does not have the complete 126-date history")
                continue
            origin = date.fromisoformat(contract.time_origin)
            last_offset = (observations[-1][0] - origin).days
            if contract.node_offsets_calendar_days[-1] > last_offset:
                issues.append(f"{underlying_id} has a scored node outside the observation horizon")
                continue
            if run_estimator_gate:
                try:
                    fit = fit_underlying_path(
                        underlying_id,
                        [str(row[0]) for row in observations],
                        [float(row[1]) for row in observations],
                        contract,
                    )
                except ValueError as error:
                    issues.append(f"{underlying_id} Stage-1 fit failed: {error}")
                else:
                    if fit.solver_status != "CONVERGED":
                        issues.append(f"{underlying_id} Stage-1 status is {fit.solver_status}")
                    if max(fit.diffusion_rse_by_node) > contract.max_diffusion_node_rse:
                        diagnostics.append(
                            f"{underlying_id} exceeds the initial diffusion-node RSE diagnostic"
                        )
                    if fit.drift_active_bound_node_indices:
                        diagnostics.append(
                            f"{underlying_id} has expected short-horizon drift active bounds at "
                            f"{list(fit.drift_active_bound_node_indices)}"
                        )
        option_series = connection.execute(
            """
            SELECT underlying_id, option_id, count(*)
            FROM market.option_daily
            GROUP BY underlying_id, option_id
            """
        ).fetchall()
        if not option_series or max(row[2] for row in option_series) < 5:
            issues.append("parent has no stable eligible option series")
        pricing_contexts = connection.execute(
            """
            SELECT DISTINCT currency, CAST(discount_curve AS VARCHAR),
                            risk_free_rate, CAST(dividend_curve AS VARCHAR),
                            dividend_yield, borrow_or_carry_rate,
                            calendar, day_count
            FROM market.pricing_metadata
            """
        ).fetchall()
        currencies = {str(row[0]) for row in pricing_contexts}
        rates = {float(row[2]) for row in pricing_contexts}
        dividends = {float(row[4]) for row in pricing_contexts}
        carries = {float(row[5]) for row in pricing_contexts}
        calendars = {str(row[6]) for row in pricing_contexts}
        day_counts = {str(row[7]) for row in pricing_contexts}
        if (
            currencies != {"USD"}
            or rates != {0.03}
            or dividends != {0.01}
            or carries != {0.03 - 0.01}
            or not all(math.isfinite(value) for value in rates | dividends | carries)
            or calendars != {"WeekendsOnly"}
            or day_counts != {"Actual365Fixed"}
        ):
            issues.append("parent does not have one finite common-currency pricing context")
        for row in pricing_contexts:
            try:
                discount_curve = json.loads(str(row[1]))
                dividend_curve = json.loads(str(row[3]))
            except (json.JSONDecodeError, TypeError):
                issues.append("parent pricing curve JSON is invalid")
                break
            if discount_curve != {"rate": float(row[2]), "type": "flat_continuous"}:
                issues.append("parent discount curve disagrees with its scalar rate")
                break
            if dividend_curve != {
                "type": "flat_continuous",
                "yield": float(row[4]),
            }:
                issues.append("parent dividend curve disagrees with its scalar yield")
                break
        increment_rows = connection.execute(
            """
            SELECT DISTINCT json_extract_string(canonicalization, '$.minimum_price_increments.option')
            FROM market.pricing_metadata
            """
        ).fetchall()
        increments = {row[0] for row in increment_rows if row[0] is not None}
        if increments != {"0.01"}:
            issues.append("parent option quotes are not on the required 0.01 USD tick")
        underlying_increment_rows = connection.execute(
            """
            SELECT DISTINCT json_extract_string(
                canonicalization, '$.minimum_price_increments.underlying'
            )
            FROM market.pricing_metadata
            """
        ).fetchall()
        underlying_increments = {
            row[0] for row in underlying_increment_rows if row[0] is not None
        }
        if underlying_increments != {"0.01"}:
            issues.append("parent underlying observations are not on the required 0.01 USD tick")
        return ParentQualification(
            qualified=not issues,
            snapshot_id=str(snapshot_id),
            snapshot_revision=int(revision),
            snapshot_status=str(status),
            underlying_universe=universe,
            observation_dates=dates,
            node_counts=node_counts,
            issues=tuple(issues),
            diagnostics=tuple(diagnostics),
        )
    finally:
        connection.close()


@dataclass(frozen=True)
class SubsetManifest:
    task_id: str
    sample_id: str
    child_snapshot_id: str
    parent_snapshot_id: str
    parent_snapshot_revision: int
    parent_logical_checksum: str
    public_logical_checksum: str
    selected_underlyings: tuple[str, ...]
    sampling_seed: int
    table_row_counts: Mapping[str, int]
    missing_dates_by_underlying: Mapping[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": V5_PUBLIC_SCHEMA_VERSION,
            "task_id": self.task_id,
            "sample_id": self.sample_id,
            "sampling_contract_id": "underlying-cluster-sha256-rank-v1",
            "variant_id": MODEL_SIGNAL_VARIANT_ID,
            "output_contract_id": V5_OUTPUT_CONTRACT_ID,
            "child_snapshot_id": self.child_snapshot_id,
            "snapshot_revision": 1,
            "status": "FROZEN",
            "parent_snapshot_id": self.parent_snapshot_id,
            "parent_snapshot_revision": self.parent_snapshot_revision,
            "parent_logical_checksum": self.parent_logical_checksum,
            "public_logical_checksum": self.public_logical_checksum,
            "selected_underlyings": list(self.selected_underlyings),
            "table_row_counts": dict(sorted(self.table_row_counts.items())),
            "missing_dates_by_underlying": {
                key: list(value) for key, value in sorted(self.missing_dates_by_underlying.items())
            },
        }


def _sql_string(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def extract_subset_db(
    parent_db: str | Path,
    output_db: str | Path,
    underlying_ids: tuple[str, ...],
    *,
    sampling_seed: int,
) -> SubsetManifest:
    """Materialize a public-only child from an already-qualified parent."""

    parent_path = Path(parent_db)
    output_path = Path(output_db)
    selected = tuple(sorted(underlying_ids))
    if len(selected) != V5_SAMPLE_SIZE or len(set(selected)) != V5_SAMPLE_SIZE:
        raise ValueError("v5 subset must contain exactly eight unique underlyings")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing subset: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    parent_checksum = canonical_logical_checksum(parent_path)
    sample_id = stable_sample_id(
        parent_logical_checksum=parent_checksum,
        seed=sampling_seed,
        underlying_ids=selected,
    )
    child_snapshot_id = "DERIVATIVES-F2A-V5-CHILD-" + sample_id.rsplit("-", 1)[-1]
    task_id = "task-" + sample_id
    connection = duckdb.connect(str(output_path))
    try:
        connection.execute(f"ATTACH '{_sql_string(parent_path)}' AS parent (READ_ONLY)")
        connection.execute("CREATE SCHEMA metadata")
        connection.execute("CREATE SCHEMA solver_visible")
        placeholders = ", ".join("?" for _ in selected)
        connection.execute(
            f"""
            CREATE TABLE solver_visible.underlying_daily AS
            SELECT ?::VARCHAR AS snapshot_id, date, underlying_id, spot_open,
                   spot_high, spot_low, spot_close, adjusted_close, volume,
                   dividend, corporate_action
            FROM parent.market.underlying_daily
            WHERE underlying_id IN ({placeholders})
            ORDER BY date, underlying_id
            """,
            [child_snapshot_id, *selected],
        )
        connection.execute(
            f"""
            CREATE TABLE solver_visible.option_contracts AS
            SELECT ?::VARCHAR AS snapshot_id, option_id, underlying_id,
                   call_put, strike, expiry, exercise_style, settlement_type,
                   contract_multiplier
            FROM parent.market.option_contracts
            WHERE underlying_id IN ({placeholders})
            ORDER BY underlying_id, expiry, strike, call_put, option_id
            """,
            [child_snapshot_id, *selected],
        )
        # Mid and settlement_price are deliberately omitted.  The canonical
        # observed price is recomputed from the only public executable sides.
        connection.execute(
            f"""
            CREATE TABLE solver_visible.f2a_option_quotes AS
            SELECT ?::VARCHAR AS snapshot_id, date, underlying_id, option_id,
                   call_put, strike, expiry, exercise_style, settlement_type,
                   contract_multiplier, bid, ask, volume, open_interest
            FROM parent.market.option_daily
            WHERE underlying_id IN ({placeholders})
            ORDER BY date, underlying_id, expiry, strike, call_put, option_id
            """,
            [child_snapshot_id, *selected],
        )
        connection.execute(
            f"""
            CREATE TABLE solver_visible.pricing_inputs AS
            SELECT ?::VARCHAR AS snapshot_id, valuation_date, underlying_id,
                   currency, discount_curve, risk_free_rate, dividend_curve,
                   dividend_yield, borrow_or_carry_rate, calendar, day_count
            FROM parent.market.pricing_metadata
            WHERE underlying_id IN ({placeholders})
            ORDER BY valuation_date, underlying_id
            """,
            [child_snapshot_id, *selected],
        )
    finally:
        connection.close()

    parent_connection = duckdb.connect(str(parent_path), read_only=True)
    try:
        private_contracts = _private_node_contracts(parent_connection)
        parent_snapshot_id, parent_snapshot_revision = parent_connection.execute(
            "SELECT snapshot_id, current_revision FROM metadata.snapshots"
        ).fetchone()
    finally:
        parent_connection.close()
    # Reopen after extracting private locations so no private JSON is ever
    # materialized in the public database.
    connection = duckdb.connect(str(output_path))
    try:
        connection.execute(
            """
            CREATE TABLE solver_visible.physical_node_locations (
                underlying_id VARCHAR NOT NULL,
                function_role VARCHAR NOT NULL,
                node_index INTEGER NOT NULL,
                day_offset DOUBLE NOT NULL,
                time_origin DATE NOT NULL,
                PRIMARY KEY (underlying_id, function_role, node_index)
            )
            """
        )
        node_rows = []
        for underlying_id in selected:
            contract = private_contracts[underlying_id]
            for role in ("drift", "diffusion"):
                node_rows.extend(
                    (underlying_id, role, index, offset, contract.time_origin)
                    for index, offset in enumerate(contract.node_offsets_calendar_days)
                )
        connection.executemany(
            "INSERT INTO solver_visible.physical_node_locations VALUES (?, ?, ?, ?, ?)",
            node_rows,
        )
        connection.execute(
            """
            CREATE TABLE solver_visible.f2a_contracts (
                contract_kind VARCHAR PRIMARY KEY,
                contract JSON NOT NULL
            )
            """
        )
        physical_contract = {
            "measure": "P",
            "state_variable": "published_ex_dividend_spot_close",
            "conditioning_information": "previous_published_close_and_deterministic_node_functions",
            "time_axis": "calendar_day_offset",
            "day_count": "Actual365Fixed",
            "dynamics_form": "time_inhomogeneous_gbm",
            "drift_function_type": "piecewise_linear",
            "diffusion_function_type": "piecewise_linear",
            "default_drift_node_count": 3,
            "default_diffusion_node_count": 3,
            "shared_node_grid": True,
            "interpolation": "linear",
            "extrapolation": "flat",
            "drift_bounds": [-0.05, 0.15],
            "diffusion_bounds": [0.05, 0.80],
            "canonical_loss": "exact-tih-gbm-gaussian-node-loss-v1",
            "transition_moment_rule": "exact_integrated_tih_gbm_between_public_dates",
            "published_state_likelihood_semantics": "latent_gaussian_transition_quasi_likelihood_ignores_0p01_usd_endpoint_quantization",
            "drift_units": "inverse_Actual365Fixed_year",
            "diffusion_units": "inverse_sqrt_Actual365Fixed_year",
            "canonical_solver": "bounded-profile-nelder-mead-v1",
            "initial_drift": 0.05,
            "initial_diffusion": 0.20,
            "initialization_rule": "realized_volatility_plus_three_frozen_multistarts",
            "max_evaluations": 6000,
            "parameter_tolerance": 1e-9,
            "objective_tolerance": 1e-11,
            "tie_breaking": "objective_then_lexicographic_diffusion_nodes",
            "covariance_method": "observed-hessian-drift-profiled-schur-v1",
            "drift_active_bound_rule": "allowed_and_reported_short_horizon_nuisance",
            "max_condition_number": 1e12,
            "node_rse_diagnostic_gate": 0.25,
            "input_dtype": "binary64",
            "output_precision": 10,
        }
        pricing_contract = {
            "measure": "USD-MONEY-MARKET-Q-v1",
            "numeraire_id": "USD-MONEY-MARKET-ACCOUNT-v1",
            "currency": "USD",
            "valuation_time_utc": "16:00:00",
            "measure_change": "girsanov_drift_only",
            "candidate_model_family": "BSM",
            "formula_id": "bsm-integrated-variance-european-v1",
            "option_series_grouping_key": [
                "underlying_id", "option_id", "call_put", "strike", "expiry",
                "settlement_type", "contract_multiplier",
            ],
            "eligible_series_selection_rule": "at_least_5_live_observations_v1",
            "minimum_series_observations": 5,
            "quote_observation_rule": "bid_ask_midpoint",
            "rate_curve_integration_rule": "flat_continuous_scalar_times_actual365",
            "dividend_or_carry_integration_rule": "flat_continuous_scalar_times_actual365",
            "volatility_parameterization": "linked_stage1_piecewise_linear_diffusion",
            "volatility_measure_mapping": "same_deterministic_diffusion_coefficient",
            "linked_diffusion_variant_id": "bsm-linked-stage1-diffusion-v1",
            "integrated_variance_rule": "exact_squared_piecewise_linear_actual365_v1",
            "interpolation": "linear",
            "extrapolation": "flat",
            "row_weight": 1.0,
            "quote_noise_scale": 0.05,
            "residual_standardization_rule": "quote_noise_plus_parameter_variance",
            "clean_max_outlier_fraction": 0.01,
            "validation_statistic": "squared",
            "residual_threshold": 4.0,
            "grouping_rule": "standardized-residual-contract-date-grouping-v1",
            "price_uncertainty_method": "delta-method-full-diffusion-covariance-v1",
            "optional_iv_diagnostic": False,
            "input_dtype": "binary64",
            "output_precision": 10,
        }
        signal_contract = {
            "supported_families": ["X", "U", "T"],
            "candidate_enumeration_version": "bsm-f2a-model-signal-candidate-catalogue-v1",
            "directional_candidate_rule": "both_directions_have_stable_ids",
            "observed_quote_execution_rule": "midpoint_plus_exact_half_spread_hurdle",
            "fitted_counterfactual_rule": "linked_stage1_bsm",
            "option_fee": 0.50,
            "option_fee_units": "USD_per_contract_per_side",
            "underlying_transaction_cost": 0.0005,
            "underlying_transaction_cost_units": "fraction_of_spot_notional",
            "edge_units": "USD_per_candidate_notional",
            "contract_multiplier_rule": "every_option_leg",
            "funding_rule": "public_discount_inputs",
            "dividend_or_carry_rule": "public_integrated_carry_inputs",
            "net_edge_rule": "gross_minus_spread_fees_underlying_and_funding_hurdles",
            "strict_inequality_rule": "net_signal_edge_gt_zero",
            "activation_checkpoint": "after_half_even_output_quantization",
            "uncertainty_propagation_rule": "full_shared_diffusion_delta_method",
            "scan_wide_ambiguity_quantile": "private_release_calibration_report",
            "canonical_order": "valuation_date_underlying_family_candidate",
            "output_precision": 10,
        }
        connection.executemany(
            "INSERT INTO solver_visible.f2a_contracts VALUES (?, ?)",
            [
                ("physical_fitting_contract", json.dumps(physical_contract, sort_keys=True)),
                ("pricing_counterfactual_contract", json.dumps(pricing_contract, sort_keys=True)),
                ("model_signal_contract", json.dumps(signal_contract, sort_keys=True)),
            ],
        )
        connection.execute(
            """
            CREATE TABLE metadata.public_task (
                task_id VARCHAR PRIMARY KEY,
                sample_id VARCHAR NOT NULL,
                variant_id VARCHAR NOT NULL,
                output_contract_id VARCHAR NOT NULL,
                snapshot_id VARCHAR NOT NULL,
                snapshot_revision INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                parent_snapshot_id VARCHAR NOT NULL,
                parent_snapshot_revision INTEGER NOT NULL,
                parent_logical_checksum VARCHAR NOT NULL,
                public_logical_checksum VARCHAR,
                public_manifest JSON
            )
            """
        )
        connection.execute(
            """
            INSERT INTO metadata.public_task
            VALUES (?, ?, ?, ?, ?, 1, 'FROZEN', ?, ?, ?, NULL, NULL)
            """,
            [
                task_id,
                sample_id,
                MODEL_SIGNAL_VARIANT_ID,
                V5_OUTPUT_CONTRACT_ID,
                child_snapshot_id,
                parent_snapshot_id,
                parent_snapshot_revision,
                parent_checksum,
            ],
        )
    finally:
        connection.close()

    public_checksum = canonical_logical_checksum(output_path)
    connection = duckdb.connect(str(output_path))
    try:
        counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "solver_visible.underlying_daily",
                "solver_visible.f2a_option_quotes",
                "solver_visible.option_contracts",
                "solver_visible.pricing_inputs",
                "solver_visible.physical_node_locations",
            )
        }
        all_dates = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT date FROM solver_visible.underlying_daily"
            ).fetchall()
        }
        missing = {}
        for underlying_id in selected:
            present = {
                str(row[0])
                for row in connection.execute(
                    "SELECT date FROM solver_visible.underlying_daily WHERE underlying_id = ?",
                    [underlying_id],
                ).fetchall()
            }
            missing[underlying_id] = tuple(sorted(all_dates - present))
        manifest = SubsetManifest(
            task_id=task_id,
            sample_id=sample_id,
            child_snapshot_id=child_snapshot_id,
            parent_snapshot_id=str(parent_snapshot_id),
            parent_snapshot_revision=int(parent_snapshot_revision),
            parent_logical_checksum=parent_checksum,
            public_logical_checksum=public_checksum,
            selected_underlyings=selected,
            sampling_seed=sampling_seed,
            table_row_counts=counts,
            missing_dates_by_underlying=missing,
        )
        connection.execute(
            """
            UPDATE metadata.public_task
            SET public_logical_checksum = ?, public_manifest = ?
            WHERE task_id = ?
            """,
            [public_checksum, json.dumps(manifest.to_dict(), sort_keys=True), task_id],
        )
        return manifest
    finally:
        connection.close()


def load_public_manifest(database: str | Path) -> dict[str, Any]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        row = connection.execute(
            "SELECT public_manifest FROM metadata.public_task"
        ).fetchone()
        if row is None or row[0] is None:
            raise ValueError("public task manifest is missing")
        return json.loads(row[0])
    finally:
        connection.close()


def load_public_contracts(
    database: str | Path,
) -> tuple[
    dict[str, PhysicalFittingContract],
    PricingCounterfactualContract,
    ModelSignalContract,
]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        raw_contracts = {
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
        grouped: dict[str, list[float]] = {}
        origins: dict[str, str] = {}
        for underlying_id, _, offset, time_origin in node_rows:
            grouped.setdefault(str(underlying_id), []).append(float(offset))
            origins[str(underlying_id)] = str(time_origin)
        physical_raw = raw_contracts["physical_fitting_contract"]
        physical = {
            underlying_id: PhysicalFittingContract.from_mapping(
                {
                    **physical_raw,
                    "time_origin": origins[underlying_id],
                    "node_offsets_calendar_days": offsets,
                }
            )
            for underlying_id, offsets in grouped.items()
        }
        pricing = PricingCounterfactualContract.from_mapping(
            raw_contracts["pricing_counterfactual_contract"]
        )
        signals = ModelSignalContract.from_mapping(raw_contracts["model_signal_contract"])
        return physical, pricing, signals
    finally:
        connection.close()


def load_underlying_histories(
    database: str | Path,
    underlying_ids: Sequence[str] | None = None,
) -> dict[str, tuple[tuple[str, ...], tuple[float, ...]]]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        parameters: list[Any] = []
        predicate = ""
        if underlying_ids is not None:
            ids = tuple(sorted(underlying_ids))
            predicate = "WHERE underlying_id IN (" + ",".join("?" for _ in ids) + ")"
            parameters.extend(ids)
        rows = connection.execute(
            f"""
            SELECT underlying_id, date, spot_close
            FROM solver_visible.underlying_daily
            {predicate}
            ORDER BY underlying_id, date
            """,
            parameters,
        ).fetchall()
        grouped: dict[str, list[tuple[str, float]]] = {}
        for underlying_id, observation_date, close in rows:
            grouped.setdefault(str(underlying_id), []).append((str(observation_date), float(close)))
        return {
            underlying_id: (
                tuple(row[0] for row in history),
                tuple(row[1] for row in history),
            )
            for underlying_id, history in grouped.items()
        }
    finally:
        connection.close()


def load_option_observations(
    database: str | Path,
    underlying_ids: Sequence[str] | None = None,
) -> tuple[OptionObservation, ...]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        parameters: list[Any] = []
        predicate = ""
        if underlying_ids is not None:
            ids = tuple(sorted(underlying_ids))
            predicate = "WHERE quote.underlying_id IN (" + ",".join("?" for _ in ids) + ")"
            parameters.extend(ids)
        rows = connection.execute(
            f"""
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
            {predicate}
            ORDER BY quote.underlying_id, quote.option_id, quote.date
            """,
            parameters,
        ).fetchall()
        result = []
        for row in rows:
            (
                valuation_date, underlying_id, option_id, option_type, strike,
                expiry, exercise_style, settlement_type, multiplier, bid, ask,
                spot, rate, dividend,
            ) = row
            years = (expiry - valuation_date).days / 365.0
            row_id = stable_row_id(str(underlying_id), str(valuation_date), str(option_id))
            result.append(
                OptionObservation(
                    row_id=row_id,
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
        ids = [item.row_id for item in result]
        if len(ids) != len(set(ids)):
            raise ValueError("public option valuation row ids are not unique")
        return tuple(result)
    finally:
        connection.close()


def iter_market_slices(
    db_path: str | Path,
    underlying_ids: tuple[str, ...],
) -> Iterator[PublicMarketSlice]:
    """Stream canonical slices with DuckDB predicate pushdown and sorting."""

    ids = tuple(sorted(underlying_ids))
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("market-slice adapter requires unique underlying ids")
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        placeholders = ",".join("?" for _ in ids)
        task = connection.execute(
            """
            SELECT snapshot_id, snapshot_revision
            FROM metadata.public_task
            """
        ).fetchone()
        if task is None:
            raise ValueError("v5 public task identity is missing")
        snapshot_id, revision = str(task[0]), int(task[1])
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
        current_key: tuple[str, str] | None = None
        current: dict[str, Any] | None = None
        for row in rows:
            valuation_date, underlying_id = str(row[0]), str(row[1])
            key = (valuation_date, underlying_id)
            if key != current_key:
                if current is not None:
                    yield market_slice_from_dict(current)
                current_key = key
                current = {
                    "snapshot_id": snapshot_id,
                    "snapshot_revision": revision,
                    "valuation_date": valuation_date,
                    "underlying_id": underlying_id,
                    "spot": float(row[2]),
                    "currency": str(row[3]),
                    "expiry_inputs": {},
                    "quotes": [],
                }
            assert current is not None
            expiry = str(row[5])
            years = (date.fromisoformat(expiry) - date.fromisoformat(valuation_date)).days / 365.0
            current["expiry_inputs"].setdefault(
                expiry,
                {
                    "discount_factor": math.exp(-float(row[13]) * years),
                    "integrated_dividend_yield": float(row[14]) * years,
                },
            )
            current["quotes"].append(
                {
                    "option_id": str(row[4]),
                    "expiry": expiry,
                    "call_put": str(row[6]),
                    "strike": float(row[7]),
                    "bid": float(row[8]),
                    "ask": float(row[9]),
                    "contract_multiplier": float(row[10]),
                    "exercise_style": str(row[11]),
                    "settlement_type": str(row[12]),
                    "valuation_date": valuation_date,
                    "underlying_id": underlying_id,
                    "currency": str(row[3]),
                }
            )
        if current is not None:
            yield market_slice_from_dict(current)
    finally:
        connection.close()


__all__ = [
    "ParentQualification",
    "SubsetManifest",
    "V5_OUTPUT_CONTRACT_ID",
    "V5_PARENT_BUSINESS_DATE_COUNT",
    "V5_PARENT_SNAPSHOT_ID",
    "V5_PARENT_UNDERLYING_COUNT",
    "V5_PUBLIC_SCHEMA_VERSION",
    "V5_SAMPLE_SIZE",
    "canonical_logical_checksum",
    "extract_subset_db",
    "iter_market_slices",
    "load_option_observations",
    "load_public_contracts",
    "load_public_manifest",
    "load_underlying_histories",
    "qualify_parent_db",
    "sample_underlyings",
    "stable_sample_id",
]
