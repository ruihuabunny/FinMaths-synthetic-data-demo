"""Independent Solver-visible implementation of the public F2A v4 formulas.

This module intentionally imports neither authoring nor verifier code and uses
no pricing library or prebuilt arbitrage scanner.  A trusted adapter supplies
the already-loaded public-child mapping.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import combinations
from math import exp, gcd, isfinite
from typing import Any, Mapping


FAMILY_ORDER = ("cross-sectional", "cross-asset", "calendar")
ETA = 1e-8
OPTION_FEE = 0.50
UNDERLYING_COST = 0.0005
VARIANT_ID = "bsm-arbitrage-finding-f2a-v4"
OUTPUT_CONTRACT_ID = "arbitrage-opportunity-type-trajectory-v5"


def _option_ask(ask: float, multiplier: float) -> float:
    return multiplier * ask + OPTION_FEE


def _option_bid(bid: float, multiplier: float) -> float:
    return multiplier * bid - OPTION_FEE


def _spot_ask(spot: float, integrated_dividend_yield: float) -> float:
    return (
        spot
        * (1.0 + UNDERLYING_COST)
        / (1.0 - UNDERLYING_COST)
        * exp(-integrated_dividend_yield / (1.0 + UNDERLYING_COST))
    )


def _spot_bid(spot: float, integrated_dividend_yield: float) -> float:
    return (
        spot
        * (1.0 - UNDERLYING_COST)
        / (1.0 + UNDERLYING_COST)
        * exp(-integrated_dividend_yield / (1.0 - UNDERLYING_COST))
    )


def _spot_outflow(
    exposure: float,
    spot: float,
    integrated_dividend_yield: float,
) -> float:
    if exposure >= 0.0:
        return exposure * _spot_ask(spot, integrated_dividend_yield)
    return exposure * _spot_bid(spot, integrated_dividend_yield)


def _convexity_positions(
    lower: Decimal,
    middle: Decimal,
    upper: Decimal,
) -> tuple[int, int, int]:
    gaps = (upper - middle, upper - lower, middle - lower)
    scale = max(-gap.as_tuple().exponent for gap in gaps)
    integers = tuple(int(gap * Decimal(10) ** scale) for gap in gaps)
    divisor = gcd(gcd(integers[0], integers[1]), integers[2])
    return tuple(value // divisor for value in integers)  # type: ignore[return-value]


def _validate_slice(data: Mapping[str, Any]) -> None:
    spot = float(data["spot"])
    if not isfinite(spot) or spot <= 0.0:
        raise ValueError("public spot must be finite and strictly positive")
    for quote in data["quotes"]:
        values = (
            float(quote["bid"]),
            float(quote["ask"]),
            float(quote["contract_multiplier"]),
            float(quote["strike"]),
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("public quote values must be finite")
        bid, ask, multiplier, strike = values
        if bid < 0.0 or ask < bid or multiplier <= 0.0 or strike <= 0.0:
            raise ValueError("public quote is outside the F2A domain")


def _slice_bits(data: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    _validate_slice(data)
    spot = float(data["spot"])
    contexts = {
        expiry: (
            float(raw["discount_factor"]),
            float(raw["integrated_dividend_yield"]),
        )
        for expiry, raw in data["expiry_inputs"].items()
    }
    buckets: dict[tuple[str, float], dict[Decimal, dict[str, Mapping[str, Any]]]] = {}
    for quote in data["quotes"]:
        expiry = str(quote["expiry"])
        multiplier = float(quote["contract_multiplier"])
        strike = Decimal(str(quote["strike"]))
        buckets.setdefault((expiry, multiplier), {}).setdefault(strike, {})[
            str(quote["call_put"])
        ] = quote

    cross_sectional = False
    cross_asset = False
    for expiry, multiplier in sorted(buckets):
        discount, integrated_dividend = contexts[expiry]
        pairs = buckets[(expiry, multiplier)]
        strikes = sorted(pairs)
        for strike in strikes:
            pair = pairs[strike]
            if set(pair) != {"call", "put"}:
                raise ValueError("complete call/put pairs are required")
            call = pair["call"]
            put = pair["put"]
            call_ask = _option_ask(float(call["ask"]), multiplier)
            call_bid = _option_bid(float(call["bid"]), multiplier)
            put_ask = _option_ask(float(put["ask"]), multiplier)
            put_bid = _option_bid(float(put["bid"]), multiplier)
            underlying_ask = multiplier * _spot_ask(spot, integrated_dividend)
            underlying_bid = multiplier * _spot_bid(spot, integrated_dividend)
            bond = multiplier * float(strike) * discount
            nonconstant = (
                call_bid - underlying_ask,
                underlying_bid - bond - call_ask,
                put_bid - bond,
                bond - put_ask - underlying_ask,
            )
            zero_payoff = (
                call_bid - put_ask - underlying_ask + bond,
                underlying_bid - bond - call_ask + put_bid,
            )
            if any(value >= 0.0 for value in nonconstant) or any(
                value > 0.0 for value in zero_payoff
            ):
                cross_asset = True

        for lower_strike, upper_strike in combinations(strikes, 2):
            lower = pairs[lower_strike]
            upper = pairs[upper_strike]
            surpluses = (
                _option_bid(float(upper["call"]["bid"]), multiplier)
                - _option_ask(float(lower["call"]["ask"]), multiplier),
                _option_bid(float(lower["put"]["bid"]), multiplier)
                - _option_ask(float(upper["put"]["ask"]), multiplier),
            )
            if any(value >= 0.0 for value in surpluses):
                cross_sectional = True
        for lower, middle, upper in combinations(strikes, 3):
            a, b, c = _convexity_positions(lower, middle, upper)
            for call_put in ("call", "put"):
                surplus = (
                    b * _option_bid(float(pairs[middle][call_put]["bid"]), multiplier)
                    - a * _option_ask(float(pairs[lower][call_put]["ask"]), multiplier)
                    - c * _option_ask(float(pairs[upper][call_put]["ask"]), multiplier)
                )
                if surplus >= 0.0:
                    cross_sectional = True

    calendar = False
    calls = {
        (
            str(quote["expiry"]),
            Decimal(str(quote["strike"])),
            float(quote["contract_multiplier"]),
        ): quote
        for quote in data["quotes"]
        if quote["call_put"] == "call"
        and quote.get("exercise_style", "european") == "european"
        and quote.get("settlement_type", "cash") == "cash"
    }
    expiries = sorted({key[0] for key in calls})
    for early, late in combinations(expiries, 2):
        early_discount, early_dividend = contexts[early]
        late_discount, late_dividend = contexts[late]
        funding = early_discount / late_discount
        if funding < 1.0:
            continue
        beta = _spot_bid(1.0, late_dividend - early_dividend)
        if beta <= 0.0 or beta > 1.0:
            continue
        early_keys = {
            (strike, multiplier)
            for expiry, strike, multiplier in calls
            if expiry == early
        }
        late_keys = {
            (strike, multiplier)
            for expiry, strike, multiplier in calls
            if expiry == late
        }
        for strike, multiplier in sorted(early_keys & late_keys):
            early_call = calls[(early, strike, multiplier)]
            late_call = calls[(late, strike, multiplier)]
            same_contract = all(
                early_call.get(key) == late_call.get(key)
                for key in (
                    "underlying_id",
                    "currency",
                    "exercise_style",
                    "settlement_type",
                )
            )
            if not same_contract:
                continue
            delta_0 = multiplier * (1.0 - beta + ETA)
            surplus = (
                _option_bid(float(early_call["bid"]), multiplier)
                - _option_ask(float(late_call["ask"]), multiplier)
                - _spot_outflow(delta_0, spot, early_dividend)
            )
            structural_certificate = (
                funding >= 1.0
                and 0.0 < beta <= 1.0
                and ETA > 0.0
                and multiplier > 0.0
                and float(strike) > 0.0
            )
            if structural_certificate and surplus >= 0.0:
                calendar = True
    return cross_sectional, cross_asset, calendar


def solve_public_child(public_child: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact ORM projection from a trusted public-child mapping."""

    bits = [False, False, False]
    for market_slice in sorted(
        public_child["slices"],
        key=lambda item: (item["valuation_date"], item["underlying_id"]),
    ):
        for index, active in enumerate(_slice_bits(market_slice)):
            bits[index] = bits[index] or active
    arbitrage_type = [
        family for family, active in zip(FAMILY_ORDER, bits) if active
    ]
    return {
        "arbitrage_opportunity": bool(arbitrage_type),
        "arbitrage_type": arbitrage_type,
    }


def build_submission(public_child: Mapping[str, Any]) -> dict[str, Any]:
    orm = solve_public_child(public_child)
    return {
        "task_id": public_child["task_id"],
        "snapshot_id": public_child["snapshot_id"],
        "snapshot_revision": public_child["snapshot_revision"],
        "variant_id": VARIANT_ID,
        "output_contract_id": OUTPUT_CONTRACT_ID,
        "trajectory": {
            "Problem": "Classify executable arbitrage in the frozen F2A v4 catalogue.",
            "Context": "Directional option quotes, fees, curves, spot, and underlying costs.",
            "Assumptions": "Finite-date semi-static v4 contract with signed cash account.",
            "Skills": "Exact cashflow construction and deterministic candidate enumeration.",
            "Evidence": "All X/U/T candidates were recomputed from the public child.",
            "Intermediate Reasoning": "Applied candidate-specific open or closed setup boundaries.",
            "Verification": "Calendar used the pathwise stock-flip certificate, not raw maturity ordering.",
            "Confidence": 1.0,
            "Outcome": {"orm_answer": orm},
        },
    }
