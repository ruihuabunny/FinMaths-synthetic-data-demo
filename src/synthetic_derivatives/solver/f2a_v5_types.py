"""Solver-owned public market types and low-level cost primitives for v5."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import exp, gcd
from typing import Mapping


@dataclass(frozen=True)
class OptionQuote:
    option_id: str
    expiry: str
    call_put: str
    strike: Decimal
    bid: float
    ask: float
    multiplier: float
    underlying_id: str = ""
    valuation_date: str = ""
    currency: str = "USD"
    exercise_style: str = "european"
    settlement_type: str = "cash"


@dataclass(frozen=True)
class ExpiryInputs:
    discount_factor: float
    integrated_dividend_yield: float


@dataclass(frozen=True)
class PublicMarketSlice:
    snapshot_id: str
    snapshot_revision: int
    valuation_date: str
    underlying_id: str
    spot: float
    currency: str
    quotes: tuple[OptionQuote, ...]
    expiry_inputs: Mapping[str, ExpiryInputs]


def gcd_normalized_convexity_positions(
    lower_strike: Decimal,
    middle_strike: Decimal,
    upper_strike: Decimal,
) -> tuple[int, int, int]:
    if not lower_strike < middle_strike < upper_strike:
        raise ValueError("strikes must be strictly increasing")
    gaps = (
        upper_strike - middle_strike,
        upper_strike - lower_strike,
        middle_strike - lower_strike,
    )
    scale = max(-gap.as_tuple().exponent for gap in gaps)
    integers = tuple(int(gap * (Decimal(10) ** scale)) for gap in gaps)
    divisor = gcd(gcd(integers[0], integers[1]), integers[2])
    return tuple(value // divisor for value in integers)  # type: ignore[return-value]


def terminal_spot_bid(
    spot: float,
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    shares = exp(-integrated_dividend_yield / (1.0 - proportional_cost)) / (
        1.0 + proportional_cost
    )
    return spot * (1.0 - proportional_cost) * shares


def terminal_spot_ask(
    spot: float,
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    shares = exp(-integrated_dividend_yield / (1.0 + proportional_cost)) / (
        1.0 - proportional_cost
    )
    return spot * (1.0 + proportional_cost) * shares


def terminal_spot_outflow(
    exposure: float,
    spot: float,
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    if exposure >= 0.0:
        return exposure * terminal_spot_ask(
            spot,
            integrated_dividend_yield,
            proportional_cost,
        )
    return exposure * terminal_spot_bid(
        spot,
        integrated_dividend_yield,
        proportional_cost,
    )


__all__ = [
    "ExpiryInputs",
    "OptionQuote",
    "PublicMarketSlice",
    "gcd_normalized_convexity_positions",
    "terminal_spot_bid",
    "terminal_spot_outflow",
]
