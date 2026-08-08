"""Authoring-side F2A v4 scan, reachability audit, and child materializer.

The authoring classifier is deliberately independent of the trusted verifier's
family-bit routine.  The two paths share immutable public inputs and primitive
cashflow definitions only.  A child is written as FROZEN only after the trusted
oracle independently reproduces the authoring ORM answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
import json
from math import isfinite
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from synthetic_derivatives.mutation.f2a import (
    MutationSpec,
    apply_mutation,
    enumerate_specs,
)
from synthetic_derivatives.verifier.f2a_contract import (
    option_ask_amount,
    option_bid_amount,
    scan_single_expiry_families,
    terminal_spot_bid,
    terminal_spot_outflow,
)
from synthetic_derivatives.verifier.f2a_oracle import (
    CALENDAR_EXPOSURE_BUFFER_RATIO,
    CALENDAR_FAMILY_ID,
    CANONICAL_FAMILY_ORDER,
    PublicChild,
    PublicMarketSlice,
    continuous_curve_inputs,
    market_slice_from_dict,
    public_child_from_dict,
    scan_market_slice,
    scan_public_child,
)
from synthetic_derivatives.verifier.f2a_database import (
    extract_subset_db as _extract_v5_subset_db,
    iter_market_slices as _iter_v5_market_slices,
    sample_underlyings as _sample_v5_underlyings,
)


V4_VARIANT_ID = "bsm-arbitrage-finding-f2a-v4"
V4_CATALOGUE_ID = "bsm-f2a-candidate-catalogue-v5"
V4_ENGINE_ID = "f2a-complete-mutation-v4"
V4_EXECUTION_ID = "us-options-underlying-5bps-options-flat-050-v4"
V4_OUTPUT_ID = "arbitrage-opportunity-type-trajectory-v5"


def sample_underlyings(
    universe: Iterable[str],
    *,
    sample_size: int = 8,
    seed: int,
) -> tuple[str, ...]:
    """F2A v5 repeated-cluster sampler; v4 materialization is unchanged."""

    return _sample_v5_underlyings(
        tuple(universe),
        sample_size=sample_size,
        seed=seed,
    )


def extract_subset_db(
    parent_db: str | Path,
    output_db: str | Path,
    underlying_ids: tuple[str, ...],
    *,
    sampling_seed: int = 0,
):
    """Create the v5 public-only DuckDB projection from a qualified parent."""

    return _extract_v5_subset_db(
        parent_db,
        output_db,
        underlying_ids,
        sampling_seed=sampling_seed,
    )


def iter_market_slices(
    db_path: str | Path,
    underlying_ids: tuple[str, ...],
):
    """Stream v5 database slices without changing the v4 single-slice loader."""

    return _iter_v5_market_slices(db_path, underlying_ids)


@dataclass(frozen=True)
class AuthoringCandidate:
    candidate_id: str
    family: str
    initial_surplus_usd: float
    setup_boundary_kind: str
    terminal_nonnegative: bool
    strict_gain_open_set: bool
    is_arbitrage: bool
    details: Mapping[str, Any]


@dataclass(frozen=True)
class AuthoringScan:
    candidates: tuple[AuthoringCandidate, ...]

    @property
    def family_bits(self) -> tuple[bool, bool, bool]:
        bits: list[bool] = []
        for family in CANONICAL_FAMILY_ORDER:
            active = False
            for candidate in self.candidates:
                if candidate.family == family and candidate.is_arbitrage:
                    active = True
                    break
            bits.append(active)
        return tuple(bits)  # type: ignore[return-value]

    @property
    def realized_signature(self) -> str:
        return "".join("1" if bit else "0" for bit in self.family_bits)

    def orm_answer(self) -> dict[str, Any]:
        types = [
            family
            for family, active in zip(CANONICAL_FAMILY_ORDER, self.family_bits)
            if active
        ]
        return {
            "arbitrage_opportunity": bool(types),
            "arbitrage_type": types,
        }


def _authoring_candidate(
    *,
    candidate_id: str,
    family: str,
    surplus: float,
    boundary: str,
    terminal_nonnegative: bool,
    strict_gain: bool,
    details: Mapping[str, Any],
) -> AuthoringCandidate:
    if not isfinite(surplus):
        raise ValueError("candidate surplus must be finite")
    if boundary == "open":
        active = terminal_nonnegative and surplus > 0.0
    elif boundary == "closed":
        active = terminal_nonnegative and surplus >= 0.0 and strict_gain
    else:
        raise ValueError("unknown candidate boundary")
    return AuthoringCandidate(
        candidate_id=candidate_id,
        family=family,
        initial_surplus_usd=surplus,
        setup_boundary_kind=boundary,
        terminal_nonnegative=terminal_nonnegative,
        strict_gain_open_set=strict_gain,
        is_arbitrage=active,
        details=details,
    )


def authoring_scan_market_slice(
    market: PublicMarketSlice,
    *,
    fee_per_contract_per_side: float = 0.50,
    proportional_cost: float = 0.0005,
    exposure_buffer_ratio: float = CALENDAR_EXPOSURE_BUFFER_RATIO,
) -> AuthoringScan:
    """Independently classify X/U/T from public cashflows."""

    raw = scan_single_expiry_families(
        quotes=market.quotes,
        spot=market.spot,
        expiry_inputs=dict(market.expiry_inputs),
        fee_per_contract_per_side=fee_per_contract_per_side,
        proportional_cost=proportional_cost,
    )
    buckets: dict[tuple[str, float], dict[Any, dict[str, Any]]] = {}
    for quote in market.quotes:
        buckets.setdefault((quote.expiry, quote.multiplier), {}).setdefault(
            quote.strike, {}
        )[quote.call_put] = quote
    cross_sectional_ids: list[str] = []
    cross_asset_bound_ids: list[str] = []
    cross_asset_parity_ids: list[str] = []
    for expiry, multiplier in sorted(buckets):
        pairs = buckets[(expiry, multiplier)]
        strikes = sorted(pairs)
        for strike in strikes:
            for name in ("call-upper", "call-lower", "put-upper", "put-lower"):
                cross_asset_bound_ids.append(
                    f"cross-asset-bound|{market.underlying_id}|"
                    f"{market.valuation_date}|{expiry}|{strike}|{multiplier:g}|{name}"
                )
            for direction in ("long-call", "short-call"):
                cross_asset_parity_ids.append(
                    f"cross-asset-parity|{market.underlying_id}|"
                    f"{market.valuation_date}|{expiry}|{strike}|{multiplier:g}|{direction}"
                )
        for lower_strike, upper_strike in combinations(strikes, 2):
            for call_put in ("call", "put"):
                cross_sectional_ids.append(
                    f"cross-sectional-monotonicity|{market.underlying_id}|"
                    f"{market.valuation_date}|{expiry}|{call_put}|{lower_strike}|"
                    f"{upper_strike}|{multiplier:g}"
                )
        for lower_strike, middle_strike, upper_strike in combinations(strikes, 3):
            for call_put in ("call", "put"):
                cross_sectional_ids.append(
                    f"cross-sectional-convexity|{market.underlying_id}|"
                    f"{market.valuation_date}|{expiry}|{call_put}|{lower_strike}|"
                    f"{middle_strike}|{upper_strike}|{multiplier:g}"
                )
    if (
        len(cross_sectional_ids) != len(raw.cross_sectional_surpluses)
        or len(cross_asset_bound_ids)
        != len(raw.cross_asset_nonconstant_surpluses)
        or len(cross_asset_parity_ids)
        != len(raw.cross_asset_zero_payoff_surpluses)
    ):
        raise ValueError("authoring candidate identity enumeration is inconsistent")
    candidates: list[AuthoringCandidate] = []
    for candidate_id, surplus in zip(
        cross_sectional_ids, raw.cross_sectional_surpluses
    ):
        candidates.append(
            _authoring_candidate(
                candidate_id=candidate_id,
                family="cross-sectional",
                surplus=surplus,
                boundary="closed",
                terminal_nonnegative=True,
                strict_gain=True,
                details={},
            )
        )
    for candidate_id, surplus in zip(
        cross_asset_bound_ids, raw.cross_asset_nonconstant_surpluses
    ):
        candidates.append(
            _authoring_candidate(
                candidate_id=candidate_id,
                family="cross-asset",
                surplus=surplus,
                boundary="closed",
                terminal_nonnegative=True,
                strict_gain=True,
                details={},
            )
        )
    for candidate_id, surplus in zip(
        cross_asset_parity_ids, raw.cross_asset_zero_payoff_surpluses
    ):
        candidates.append(
            _authoring_candidate(
                candidate_id=candidate_id,
                family="cross-asset",
                surplus=surplus,
                boundary="open",
                terminal_nonnegative=True,
                strict_gain=False,
                details={},
            )
        )

    calls = {
        (quote.expiry, quote.strike, quote.multiplier): quote
        for quote in market.quotes
        if quote.call_put == "call"
        and quote.exercise_style == "european"
        and quote.settlement_type == "cash"
    }
    expiries = sorted({expiry for expiry, _, _ in calls})
    for early_index, early_expiry in enumerate(expiries):
        for late_expiry in expiries[early_index + 1 :]:
            early_context = market.expiry_inputs[early_expiry]
            late_context = market.expiry_inputs[late_expiry]
            funding_factor = (
                early_context.discount_factor / late_context.discount_factor
            )
            if funding_factor < 1.0:
                continue
            dividend_12 = (
                late_context.integrated_dividend_yield
                - early_context.integrated_dividend_yield
            )
            beta = terminal_spot_bid(1.0, dividend_12, proportional_cost)
            if beta <= 0.0 or beta > 1.0:
                continue
            common_keys = sorted(
                {
                    (strike, multiplier)
                    for expiry, strike, multiplier in calls
                    if expiry == early_expiry
                }
                & {
                    (strike, multiplier)
                    for expiry, strike, multiplier in calls
                    if expiry == late_expiry
                }
            )
            for strike, multiplier in common_keys:
                early = calls[(early_expiry, strike, multiplier)]
                late = calls[(late_expiry, strike, multiplier)]
                if (
                    early.underlying_id != late.underlying_id
                    or early.currency != late.currency
                    or early.exercise_style != late.exercise_style
                    or early.settlement_type != late.settlement_type
                ):
                    continue
                delta_0 = multiplier * (
                    1.0 - beta + exposure_buffer_ratio
                )
                early_bid_amount = option_bid_amount(
                    early.bid, multiplier, fee_per_contract_per_side
                )
                late_ask_amount = option_ask_amount(
                    late.ask, multiplier, fee_per_contract_per_side
                )
                first_outflow = terminal_spot_outflow(
                    delta_0,
                    market.spot,
                    early_context.integrated_dividend_yield,
                    proportional_cost,
                )
                surplus = early_bid_amount - late_ask_amount - first_outflow
                low_slope = funding_factor * delta_0
                high_t1_x_coefficient = (
                    delta_0 - multiplier + multiplier * beta
                )
                high_slope = funding_factor * high_t1_x_coefficient
                boundary_slack = (
                    funding_factor
                    * (
                        multiplier * float(strike)
                        + high_t1_x_coefficient * float(strike)
                    )
                    - multiplier * float(strike)
                )
                terminal = {
                    "beta_positive": beta > 0.0,
                    "beta_at_most_one": beta <= 1.0,
                    "funding_factor_at_least_one": funding_factor >= 1.0,
                    "left_boundary_nonnegative": low_slope * float(strike) >= 0.0,
                    "actual_boundary_nonnegative": boundary_slack >= 0.0,
                    "low_x_cell_nonnegative": low_slope >= 0.0,
                    "high_x_low_y_cell_nonnegative": boundary_slack >= 0.0,
                    "high_x_high_y_cell_nonnegative": boundary_slack >= 0.0,
                    "strict_gain_open_set": boundary_slack > 0.0,
                    "unsimplified_ledger_replayed": (
                        delta_0
                        == multiplier * (1.0 - beta + exposure_buffer_ratio)
                        and high_t1_x_coefficient > 0.0
                    ),
                    "minimum_boundary_slack_usd": boundary_slack,
                    "minimum_unbounded_ray_slope": min(low_slope, high_slope, 0.0),
                }
                terminal_nonnegative = all(
                    bool(terminal[key])
                    for key in (
                        "beta_positive",
                        "beta_at_most_one",
                        "funding_factor_at_least_one",
                        "left_boundary_nonnegative",
                        "actual_boundary_nonnegative",
                        "low_x_cell_nonnegative",
                        "high_x_low_y_cell_nonnegative",
                        "high_x_high_y_cell_nonnegative",
                        "unsimplified_ledger_replayed",
                    )
                )
                candidate_id = (
                    f"calendar-call-stock-flip-v1|{market.underlying_id}|"
                    f"{market.valuation_date}|{early_expiry}|{late_expiry}|"
                    f"{strike}|{multiplier:g}"
                )
                candidates.append(
                    _authoring_candidate(
                        candidate_id=candidate_id,
                        family="calendar",
                        surplus=surplus,
                        boundary="closed",
                        terminal_nonnegative=terminal_nonnegative,
                        strict_gain=bool(terminal["strict_gain_open_set"]),
                        details={
                            "template_id": CALENDAR_FAMILY_ID,
                            "underlying_id": market.underlying_id,
                            "valuation_date": market.valuation_date,
                            "T1": early_expiry,
                            "T2": late_expiry,
                            "strike": float(strike),
                            "contract_multiplier": multiplier,
                            "early_option_id": early.option_id,
                            "late_option_id": late.option_id,
                            "early_position": -1,
                            "late_position": 1,
                            "early_bid_amount_after_fee": early_bid_amount,
                            "late_ask_amount_after_fee": late_ask_amount,
                            "first_segment_underlying_outflow": first_outflow,
                            "beta_12": beta,
                            "funding_factor_12": funding_factor,
                            "exposure_buffer_ratio": exposure_buffer_ratio,
                            "delta_0": delta_0,
                            "delta_1_low": 0.0,
                            "delta_1_high": -multiplier,
                            "terminal_certificate": terminal,
                        },
                    )
                )
    family_rank = {family: index for index, family in enumerate(CANONICAL_FAMILY_ORDER)}

    def candidate_order(item: AuthoringCandidate) -> tuple[Any, ...]:
        if item.family == "calendar":
            return (
                family_rank[item.family],
                item.details["valuation_date"],
                item.details["underlying_id"],
                item.details["T1"],
                item.details["T2"],
                item.details["strike"],
                item.details["contract_multiplier"],
                item.details["early_option_id"],
                item.details["late_option_id"],
            )
        return (family_rank[item.family], item.candidate_id)

    candidates.sort(key=candidate_order)
    return AuthoringScan(tuple(candidates))


def _representative_evidence(scan: AuthoringScan) -> tuple[dict[str, Any], ...]:
    evidence: list[dict[str, Any]] = []
    for family in CANONICAL_FAMILY_ORDER:
        family_candidates = [item for item in scan.candidates if item.family == family]
        active = [item for item in family_candidates if item.is_arbitrage]
        if active:
            selected = active[0]
            selection_role = "first_active"
        else:
            selected = min(
                family_candidates,
                key=lambda item: (abs(item.initial_surplus_usd), item.candidate_id),
            )
            selection_role = "nearest_inactive"
        record: dict[str, Any] = {
            "candidate_id": selected.candidate_id,
            "family": selected.family,
            "template_id": selected.details.get("template_id", "single-expiry-v4"),
            "initial_surplus_usd": selected.initial_surplus_usd,
            "setup_boundary_kind": selected.setup_boundary_kind,
            "terminal_nonnegative": selected.terminal_nonnegative,
            "strict_gain_open_set": selected.strict_gain_open_set,
            "is_arbitrage": selected.is_arbitrage,
            "selection_role": selection_role,
            "active_candidate_count": len(active),
        }
        record.update(selected.details)
        evidence.append(record)
    return tuple(evidence)


def _summary_for_audit(scan: AuthoringScan) -> dict[str, Any]:
    evidence = _representative_evidence(scan)
    return {
        "realized_signature": scan.realized_signature,
        "active_candidates": [
            {
                "candidate_id": item["candidate_id"],
                "family": item["family"],
                "guard_distance_usd": item["initial_surplus_usd"],
                "active_candidate_count": item["active_candidate_count"],
            }
            for item in evidence
            if item["is_arbitrage"]
        ],
        "nearest_inactive_candidates": [
            {
                "candidate_id": item["candidate_id"],
                "family": item["family"],
                "guard_distance_usd": -item["initial_surplus_usd"],
            }
            for item in evidence
            if not item["is_arbitrage"]
        ],
    }


def _spec_target(spec: MutationSpec) -> dict[str, Any]:
    return {
        "option_ids": list(spec.option_ids),
        "valuation_date": spec.valuation_date,
        "underlying_id": spec.underlying_id,
    }


def _integer_windows(
    market: PublicMarketSlice,
    winning_spec: MutationSpec,
    *,
    minimum_tick: int,
    maximum_tick: int,
) -> tuple[list[dict[str, int | str]], tuple[int, int]]:
    signatures: list[tuple[int, str]] = []
    valid_ticks: list[int] = []
    sign = -1 if winning_spec.delta_ticks < 0 else 1
    for absolute_tick in range(minimum_tick, maximum_tick + 1):
        candidate = MutationSpec(
            operator_id=winning_spec.operator_id,
            kind=winning_spec.kind,
            delta_ticks=sign * absolute_tick,
            option_ids=winning_spec.option_ids,
            valuation_date=winning_spec.valuation_date,
            underlying_id=winning_spec.underlying_id,
        )
        try:
            mutated = apply_mutation(market, candidate).market_slice
        except ValueError:
            continue
        valid_ticks.append(absolute_tick)
        signatures.append(
            (absolute_tick, authoring_scan_market_slice(mutated).realized_signature)
        )
    windows: list[dict[str, int | str]] = []
    if signatures:
        start_tick, current_signature = signatures[0]
        previous_tick = start_tick
        for tick, signature in signatures[1:]:
            if signature != current_signature or tick != previous_tick + 1:
                windows.append(
                    {
                        "signature": current_signature,
                        "minimum_absolute_tick": start_tick,
                        "maximum_absolute_tick": previous_tick,
                    }
                )
                start_tick = tick
                current_signature = signature
            previous_tick = tick
        windows.append(
            {
                "signature": current_signature,
                "minimum_absolute_tick": start_tick,
                "maximum_absolute_tick": previous_tick,
            }
        )
    domain = (min(valid_ticks), max(valid_ticks)) if valid_ticks else (0, 0)
    return windows, domain


def run_reachability_audit(
    market: PublicMarketSlice,
    *,
    absolute_tick_grid: tuple[int, ...] = (
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        128,
        256,
        512,
        1024,
        2048,
        4096,
    ),
) -> dict[str, Any]:
    """Mutate real quotes and run the full authoring oracle for all signatures."""

    requested_order = ("000", "100", "010", "001", "110", "101", "011", "111")
    allowed_kinds = {
        "000": ("clean_control",),
        "100": ("call_put_pair_equal_shift",),
        "010": ("single_option_quote_shift", "underlying_spot_shift"),
        "001": ("call_put_pair_equal_shift", "underlying_spot_shift"),
        "110": ("single_option_quote_shift",),
        "101": ("call_put_pair_equal_shift",),
        "011": ("single_option_quote_shift", "underlying_spot_shift"),
        "111": ("single_option_quote_shift",),
    }
    specs = enumerate_specs(market, absolute_tick_grid=absolute_tick_grid)
    results: dict[str, Any] = {}
    for requested in requested_order:
        winner: tuple[MutationSpec, AuthoringScan] | None = None
        for spec in specs:
            if spec.kind not in allowed_kinds[requested]:
                continue
            try:
                mutated = apply_mutation(market, spec).market_slice
            except ValueError:
                continue
            scan = authoring_scan_market_slice(mutated)
            if scan.realized_signature == requested:
                winner = (spec, scan)
                break
        if winner is None:
            results[requested] = {
                "reachable": False,
                "operator": None,
                "target_ids": [],
                "sign": None,
                "exact_integer_tick_intervals": [],
                "active_candidates": [],
                "nearest_inactive_candidates": [],
                "domain_gate_interval": None,
                "diagnostic": (
                    "NO_REAL_MUTATION_IN_FROZEN_TARGET_SIGN_TICK_GRID_REALIZED_SIGNATURE"
                ),
            }
            continue
        spec, scan = winner
        trusted_scan = scan_market_slice(apply_mutation(market, spec).market_slice)
        if trusted_scan.realized_signature != requested:
            raise ValueError(
                "trusted verifier disagrees with the authoring reachability winner"
            )
        if spec.kind == "clean_control":
            windows = [
                {
                    "signature": "000",
                    "minimum_absolute_tick": 0,
                    "maximum_absolute_tick": 0,
                }
            ]
            domain_interval = [0, 0]
        else:
            windows, domain = _integer_windows(
                market,
                spec,
                minimum_tick=1,
                maximum_tick=max(absolute_tick_grid),
            )
            windows = [window for window in windows if window["signature"] == requested]
            domain_interval = list(domain)
        summary = _summary_for_audit(scan)
        results[requested] = {
            "reachable": True,
            "operator": spec.operator_id,
            "target_ids": list(spec.option_ids),
            "sign": 0 if spec.delta_ticks == 0 else (-1 if spec.delta_ticks < 0 else 1),
            "selected_delta_ticks": spec.delta_ticks,
            "trusted_verifier_signature": trusted_scan.realized_signature,
            "exact_integer_tick_intervals": windows,
            "active_candidates": summary["active_candidates"],
            "nearest_inactive_candidates": summary["nearest_inactive_candidates"],
            "domain_gate_interval": domain_interval,
            "diagnostic": None,
        }
    return {
        "audit_id": "f2a-calendar-enabled-reachability-v4",
        "status": "COMPLETE",
        "execution_profile_id": V4_EXECUTION_ID,
        "requested_signature_order": list(requested_order),
        "results": results,
    }


def load_frozen_parent_fixture(
    fixture_path: str | Path,
    manifest_path: str | Path,
) -> PublicChild:
    fixture = Path(fixture_path)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest["status"] != "FROZEN" or manifest["snapshot_revision"] != 1:
        raise ValueError("F2A parent fixture must be FROZEN revision 1")
    payload = fixture.read_bytes()
    if sha256(payload).hexdigest() != manifest["sha256"]:
        raise ValueError("F2A parent fixture integrity metadata does not match")
    parent = public_child_from_dict(json.loads(payload))
    if parent.snapshot_id != manifest["snapshot_id"]:
        raise ValueError("F2A parent fixture identity does not match its manifest")
    if parent.status != "FROZEN":
        raise ValueError("F2A parent fixture is not frozen")
    return parent


def load_frozen_parent_duckdb(
    database_path: str | Path,
    manifest_path: str | Path,
    *,
    valuation_date: str,
    underlying_id: str,
    task_id: str = "f2a-v4-production-parent-selection",
) -> PublicChild:
    """Select one complete production slice from the explicit parent read-only.

    This adapter never falls back to the CI fixture or another repository
    database.  It projects only the fields required by the v4 public contract.
    """

    database = Path(database_path)
    manifest_file = Path(manifest_path)
    if not database.is_file() or not manifest_file.is_file():
        raise FileNotFoundError(
            "explicit frozen F2A parent DuckDB and manifest are required"
        )
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    expected_snapshot_id = "DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2"
    manifest_revision = manifest.get(
        "snapshot_revision",
        manifest.get("current_revision", manifest.get("revision")),
    )
    if (
        manifest.get("snapshot_id") != expected_snapshot_id
        or manifest.get("status") != "FROZEN"
        or manifest_revision != 1
    ):
        raise ValueError("production F2A parent manifest identity is not FROZEN r1")

    import duckdb

    connection = duckdb.connect(str(database), read_only=True)
    try:
        metadata = connection.execute(
            """
            SELECT snapshot_id, status, current_revision
            FROM metadata.snapshots
            WHERE snapshot_id = ?
            """,
            [expected_snapshot_id],
        ).fetchall()
        if metadata != [(expected_snapshot_id, "FROZEN", 1)]:
            raise ValueError("production F2A parent database identity is not FROZEN r1")
        underlying_rows = connection.execute(
            """
            SELECT CAST(date AS VARCHAR), underlying_id, CAST(spot_close AS DOUBLE)
            FROM solver_visible.underlying_daily
            WHERE snapshot_id = ? AND date = CAST(? AS DATE) AND underlying_id = ?
            """,
            [expected_snapshot_id, valuation_date, underlying_id],
        ).fetchall()
        if len(underlying_rows) != 1:
            raise ValueError("production selector did not resolve one underlying row")
        pricing_rows = connection.execute(
            """
            SELECT currency, CAST(risk_free_rate AS DOUBLE),
                   CAST(dividend_yield AS DOUBLE)
            FROM solver_visible.pricing_metadata
            WHERE snapshot_id = ?
              AND CAST(valuation_timestamp AT TIME ZONE 'UTC' AS DATE) = CAST(? AS DATE)
              AND underlying_id = ?
            """,
            [expected_snapshot_id, valuation_date, underlying_id],
        ).fetchall()
        if len(pricing_rows) != 1:
            raise ValueError("production selector did not resolve one pricing context")
        option_rows = connection.execute(
            """
            SELECT option_id, CAST(expiry AS VARCHAR), call_put,
                   CAST(strike AS DOUBLE), CAST(bid AS DOUBLE), CAST(ask AS DOUBLE),
                   CAST(contract_multiplier AS DOUBLE), exercise_style, settlement_type
            FROM solver_visible.option_daily
            WHERE snapshot_id = ? AND date = CAST(? AS DATE) AND underlying_id = ?
            ORDER BY expiry, strike, call_put, option_id
            """,
            [expected_snapshot_id, valuation_date, underlying_id],
        ).fetchall()
    finally:
        connection.close()

    if len(option_rows) != 56:
        raise ValueError("production F2A v4 selection requires a complete 56-row chain")
    currency, rate, dividend_yield = pricing_rows[0]
    expiries = tuple(sorted({row[1] for row in option_rows}))
    if len(expiries) != 4:
        raise ValueError("production F2A v4 selection requires four live expiries")
    public_slice = market_slice_from_dict(
        {
            "snapshot_id": expected_snapshot_id,
            "snapshot_revision": 1,
            "valuation_date": valuation_date,
            "underlying_id": underlying_id,
            "spot": underlying_rows[0][2],
            "currency": currency,
            "expiry_inputs": {
                expiry: {
                    "discount_factor": context.discount_factor,
                    "integrated_dividend_yield": context.integrated_dividend_yield,
                }
                for expiry, context in continuous_curve_inputs(
                    valuation_date,
                    expiries,
                    risk_free_rate=rate,
                    dividend_yield=dividend_yield,
                ).items()
            },
            "quotes": [
                {
                    "option_id": row[0],
                    "expiry": row[1],
                    "call_put": row[2],
                    "strike": row[3],
                    "bid": row[4],
                    "ask": row[5],
                    "contract_multiplier": row[6],
                    "exercise_style": row[7],
                    "settlement_type": row[8],
                    "valuation_date": valuation_date,
                    "underlying_id": underlying_id,
                    "currency": currency,
                }
                for row in option_rows
            ],
        }
    )
    return PublicChild(
        task_id=task_id,
        snapshot_id=expected_snapshot_id,
        snapshot_revision=1,
        status="FROZEN",
        slices=(public_slice,),
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def materialize_verified_child(
    *,
    parent: PublicChild,
    spec: MutationSpec,
    requested_signature: str,
    task_id: str,
    output_path: str | Path,
    lineage_path: str | Path,
) -> tuple[PublicChild, dict[str, Any]]:
    """Materialize one public child and freeze it after independent verification."""

    if parent.status != "FROZEN" or len(parent.slices) != 1:
        raise ValueError("v4 fixture materialization requires one frozen parent slice")
    applied = apply_mutation(parent.slices[0], spec)
    authoring = authoring_scan_market_slice(applied.market_slice)
    if authoring.realized_signature != requested_signature:
        raise ValueError("requested signature was not realized by the public mutation")
    child_identity_payload = (
        f"{parent.snapshot_id}|{task_id}|{spec.stable_identity()}"
    ).encode("utf-8")
    child_snapshot_id = "F2A-CHILD-v4-" + sha256(child_identity_payload).hexdigest()[:20]
    child_slice = PublicMarketSlice(
        snapshot_id=child_snapshot_id,
        snapshot_revision=1,
        valuation_date=applied.market_slice.valuation_date,
        underlying_id=applied.market_slice.underlying_id,
        spot=applied.market_slice.spot,
        currency=applied.market_slice.currency,
        quotes=applied.market_slice.quotes,
        expiry_inputs=applied.market_slice.expiry_inputs,
    )
    draft = PublicChild(
        task_id=task_id,
        snapshot_id=child_snapshot_id,
        snapshot_revision=1,
        status="DRAFT",
        slices=(child_slice,),
    )
    trusted = scan_public_child(draft)
    if trusted.orm_answer() != authoring.orm_answer():
        raise ValueError("independent trusted oracle disagrees with authoring scan")
    frozen = PublicChild(
        task_id=draft.task_id,
        snapshot_id=draft.snapshot_id,
        snapshot_revision=draft.snapshot_revision,
        status="FROZEN",
        slices=draft.slices,
    )
    lineage = {
        "parent_task_id": parent.task_id,
        "child_task_id": task_id,
        "parent_snapshot_id": parent.snapshot_id,
        "parent_snapshot_revision": parent.snapshot_revision,
        "child_snapshot_id": frozen.snapshot_id,
        "child_snapshot_revision": frozen.snapshot_revision,
        "variant_id": V4_VARIANT_ID,
        "output_contract_id": V4_OUTPUT_ID,
        "operator": spec.operator_id,
        "engine_id": V4_ENGINE_ID,
        "mutation": applied.record,
        "execution_contract_id": V4_EXECUTION_ID,
        "candidate_catalogue_id": V4_CATALOGUE_ID,
        "requested_signature": requested_signature,
        "realized_signature": authoring.realized_signature,
        "oracle_result": trusted.orm_answer(),
        "authoring_guard_evidence": {
            "candidate_evidence": list(_representative_evidence(authoring)),
        },
    }
    _atomic_json(Path(lineage_path), lineage)
    # Publish the public frozen child last, after private lineage exists.
    _atomic_json(Path(output_path), frozen.to_dict())
    return frozen, lineage


def find_spec_for_signature(
    market: PublicMarketSlice,
    requested_signature: str,
    *,
    absolute_tick_grid: Iterable[int] = (
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        128,
        256,
        512,
        1024,
        2048,
        4096,
    ),
) -> MutationSpec:
    for spec in enumerate_specs(
        market,
        absolute_tick_grid=tuple(absolute_tick_grid),
    ):
        try:
            mutated = apply_mutation(market, spec).market_slice
        except ValueError:
            continue
        if authoring_scan_market_slice(mutated).realized_signature == requested_signature:
            return spec
    raise ValueError(f"signature {requested_signature} is unreachable on the frozen grid")
