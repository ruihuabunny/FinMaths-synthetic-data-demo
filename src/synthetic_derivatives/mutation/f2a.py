"""Pure immutable mutation grammar for executable F2A v4.

This module does not write DuckDB/JSON artifacts and does not classify oracle
truth.  It only resolves deterministic public targets and returns a mutated
in-memory market slice plus a complete mutation record.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Literal

from synthetic_derivatives.verifier.f2a_oracle import PublicMarketSlice


MutationKind = Literal[
    "clean_control",
    "single_option_quote_shift",
    "call_put_pair_equal_shift",
    "underlying_spot_shift",
]

OPTION_TICK = Decimal("0.01")
UNDERLYING_TICK = Decimal("0.01")


@dataclass(frozen=True)
class MutationSpec:
    operator_id: str
    kind: MutationKind
    delta_ticks: int = 0
    option_ids: tuple[str, ...] = ()
    valuation_date: str = ""
    underlying_id: str = ""

    def __post_init__(self) -> None:
        if self.kind == "clean_control":
            if self.delta_ticks != 0 or self.option_ids:
                raise ValueError("clean control cannot contain a mutation")
            return
        if self.delta_ticks == 0:
            raise ValueError("a mutation requires a nonzero integer tick delta")
        if self.kind == "single_option_quote_shift" and len(self.option_ids) != 1:
            raise ValueError("single-option mutation requires one option id")
        if self.kind == "call_put_pair_equal_shift" and len(self.option_ids) != 2:
            raise ValueError("grouped mutation requires two option ids")
        if self.kind == "underlying_spot_shift" and self.option_ids:
            raise ValueError("spot mutation cannot contain option ids")

    def stable_identity(self) -> str:
        payload = json.dumps(
            {
                "operator_id": self.operator_id,
                "kind": self.kind,
                "delta_ticks": self.delta_ticks,
                "option_ids": self.option_ids,
                "valuation_date": self.valuation_date,
                "underlying_id": self.underlying_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "f2a-mutation-" + sha256(payload.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class AppliedMutation:
    market_slice: PublicMarketSlice
    record: dict[str, Any] | None


def clean_control(market: PublicMarketSlice) -> MutationSpec:
    return MutationSpec(
        operator_id="clean_control_v2",
        kind="clean_control",
        valuation_date=market.valuation_date,
        underlying_id=market.underlying_id,
    )


def single_option_spec(
    market: PublicMarketSlice,
    option_id: str,
    delta_ticks: int,
) -> MutationSpec:
    matches = [quote for quote in market.quotes if quote.option_id == option_id]
    if len(matches) != 1:
        raise ValueError("single-option selector must resolve exactly one row")
    return MutationSpec(
        operator_id="mutate_option_price_point_v3",
        kind="single_option_quote_shift",
        delta_ticks=delta_ticks,
        option_ids=(option_id,),
        valuation_date=market.valuation_date,
        underlying_id=market.underlying_id,
    )


def call_put_pair_spec(
    market: PublicMarketSlice,
    *,
    expiry: str,
    strike: Decimal,
    multiplier: float,
    delta_ticks: int,
) -> MutationSpec:
    targets = sorted(
        (
            quote
            for quote in market.quotes
            if quote.expiry == expiry
            and quote.strike == strike
            and quote.multiplier == multiplier
        ),
        key=lambda quote: 0 if quote.call_put == "call" else 1,
    )
    if len(targets) != 2 or [quote.call_put for quote in targets] != ["call", "put"]:
        raise ValueError("grouped selector requires one matching call/put pair")
    return MutationSpec(
        operator_id="mutate_call_put_pair_equal_shift_v2",
        kind="call_put_pair_equal_shift",
        delta_ticks=delta_ticks,
        option_ids=tuple(quote.option_id for quote in targets),
        valuation_date=market.valuation_date,
        underlying_id=market.underlying_id,
    )


def underlying_spot_spec(
    market: PublicMarketSlice,
    delta_ticks: int,
) -> MutationSpec:
    return MutationSpec(
        operator_id="mutate_underlying_spot_point_v3",
        kind="underlying_spot_shift",
        delta_ticks=delta_ticks,
        valuation_date=market.valuation_date,
        underlying_id=market.underlying_id,
    )


def apply_mutation(market: PublicMarketSlice, spec: MutationSpec) -> AppliedMutation:
    """Apply one logical mutation atomically after all public domain checks."""

    if (
        spec.valuation_date != market.valuation_date
        or spec.underlying_id != market.underlying_id
    ):
        raise ValueError("mutation selector does not match the market slice")
    if spec.kind == "clean_control":
        return AppliedMutation(market, None)

    if spec.kind == "underlying_spot_shift":
        before_decimal = Decimal(str(market.spot))
        after_decimal = before_decimal + UNDERLYING_TICK * spec.delta_ticks
        if after_decimal <= 0:
            raise ValueError("mutated spot must remain strictly positive")
        after = float(after_decimal)
        return AppliedMutation(
            replace(market, spot=after),
            {
                "kind": spec.kind,
                "operator_id": spec.operator_id,
                "delta_ticks": spec.delta_ticks,
                "tick_unit": "underlying_ticks",
                "logical_mutation_groups": 1,
                "logical_quote_points_changed": 0,
                "physical_quote_points_changed": 0,
                "physical_spot_points_changed": 1,
                "spot_mutation": {
                    "valuation_date": market.valuation_date,
                    "underlying_id": market.underlying_id,
                    "before": market.spot,
                    "after": after,
                },
            },
        )

    target_ids = set(spec.option_ids)
    targets = [quote for quote in market.quotes if quote.option_id in target_ids]
    if len(targets) != len(target_ids):
        raise ValueError("option mutation target is not unique and complete")
    if spec.kind == "call_put_pair_equal_shift":
        if {quote.call_put for quote in targets} != {"call", "put"}:
            raise ValueError("grouped mutation must contain one call and one put")
        invariant_fields = {
            (
                quote.valuation_date,
                quote.underlying_id,
                quote.expiry,
                quote.strike,
                quote.multiplier,
                quote.currency,
                quote.exercise_style,
                quote.settlement_type,
            )
            for quote in targets
        }
        if len(invariant_fields) != 1:
            raise ValueError("grouped call/put contracts must share all conventions")

    delta = OPTION_TICK * spec.delta_ticks
    records: list[dict[str, Any]] = []
    replacements = {}
    for quote in targets:
        before_bid = Decimal(str(quote.bid))
        before_ask = Decimal(str(quote.ask))
        after_bid = before_bid + delta
        after_ask = before_ask + delta
        if after_bid < 0 or after_ask < after_bid:
            raise ValueError("mutated option quotes must satisfy 0 <= bid <= ask")
        replacement = replace(quote, bid=float(after_bid), ask=float(after_ask))
        replacements[quote.option_id] = replacement
        records.append(
            {
                "option_id": quote.option_id,
                "valuation_date": quote.valuation_date,
                "underlying_id": quote.underlying_id,
                "expiry": quote.expiry,
                "strike": float(quote.strike),
                "call_put": quote.call_put,
                "contract_multiplier": quote.multiplier,
                "bid_before": quote.bid,
                "bid_after": replacement.bid,
                "ask_before": quote.ask,
                "ask_after": replacement.ask,
            }
        )
    mutated_quotes = tuple(
        replacements.get(quote.option_id, quote) for quote in market.quotes
    )
    records.sort(key=lambda item: 0 if item["call_put"] == "call" else 1)
    grouped = spec.kind == "call_put_pair_equal_shift"
    return AppliedMutation(
        replace(market, quotes=mutated_quotes),
        {
            "kind": spec.kind,
            "operator_id": spec.operator_id,
            "delta_ticks": spec.delta_ticks,
            "tick_unit": "option_ticks",
            "logical_mutation_groups": 1,
            "logical_quote_points_changed": 2 if grouped else 1,
            "physical_quote_points_changed": 2 if grouped else 1,
            "physical_spot_points_changed": 0,
            "group_semantics": (
                "same-strike-same-expiry-call-put-equal-shift" if grouped else None
            ),
            "quote_mutations": records,
        },
    )


def enumerate_specs(
    market: PublicMarketSlice,
    *,
    absolute_tick_grid: tuple[int, ...],
    sign_order: tuple[int, ...] = (-1, 1),
) -> tuple[MutationSpec, ...]:
    """Return the frozen deterministic target/sign/tick search order."""

    if any(tick <= 0 for tick in absolute_tick_grid):
        raise ValueError("absolute tick grid must contain positive integers")
    specs: list[MutationSpec] = [clean_control(market)]
    for quote in market.quotes:
        for sign in sign_order:
            for tick in absolute_tick_grid:
                specs.append(single_option_spec(market, quote.option_id, sign * tick))
    pair_keys = sorted(
        {
            (quote.expiry, quote.strike, quote.multiplier)
            for quote in market.quotes
        }
    )
    for expiry, strike, multiplier in pair_keys:
        for sign in sign_order:
            for tick in absolute_tick_grid:
                specs.append(
                    call_put_pair_spec(
                        market,
                        expiry=expiry,
                        strike=strike,
                        multiplier=multiplier,
                        delta_ticks=sign * tick,
                    )
                )
    for sign in sign_order:
        for tick in absolute_tick_grid:
            specs.append(underlying_spot_spec(market, sign * tick))
    return tuple(specs)
