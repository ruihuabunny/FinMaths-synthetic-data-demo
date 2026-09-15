"""Static option-chain contracts, deterministic expansion, and parsing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import TYPE_CHECKING, Any

from synthetic_derivatives.authoring.canonicalization import quantize_to_increment
from synthetic_derivatives.authoring.config_versions import ConfigVersionPolicy
from synthetic_derivatives.authoring.deterministic_functions import finite_float

if TYPE_CHECKING:
    from synthetic_derivatives.authoring.config_models import UnderlyingConfig


@dataclass(frozen=True)
class OptionTemplate:
    template_id: str
    call_put: str
    strike_moneyness: Decimal | None
    expiry_days: int
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal
    chain_id: str | None = None
    strike_absolute: Decimal | None = None


@dataclass(frozen=True)
class OptionLiquidityFilter:
    max_expiry_days: int
    min_moneyness: Decimal
    max_moneyness: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "listing_moneyness_band_and_max_expiry",
            "max_expiry_days": self.max_expiry_days,
            "min_moneyness": str(self.min_moneyness),
            "max_moneyness": str(self.max_moneyness),
            "boundary": "inclusive",
        }


@dataclass(frozen=True)
class BidAskNoiseConfig:
    noise_type: str
    standard_deviation: float
    minimum_multiplier: float
    maximum_multiplier: float
    stream_namespace: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.noise_type,
            "standard_deviation": self.standard_deviation,
            "minimum_multiplier": self.minimum_multiplier,
            "maximum_multiplier": self.maximum_multiplier,
            "stream_namespace": self.stream_namespace,
        }


@dataclass(frozen=True)
class OptionChainConfig:
    chain_id: str
    expiry_days: tuple[int, ...]
    moneyness_grid: tuple[Decimal, ...] | None
    strike_grid: tuple[Decimal, ...] | None
    call_put: tuple[str, ...]
    listing_rule: str
    roll_rule: str
    strike_increment: Decimal
    strike_rounding: str
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal
    liquidity_filter: OptionLiquidityFilter | None


class OptionChainBuilder:
    """Expand a validated chain specification into stable option templates."""

    def __init__(self, chain: OptionChainConfig):
        self.chain = chain

    def build_templates(self) -> tuple[OptionTemplate, ...]:
        expiry_days = self.selected_expiry_days()
        grid = self.selected_grid()
        return tuple(
            OptionTemplate(
                template_id=self._template_id(expiry, strike_input, call_put),
                call_put=call_put,
                strike_moneyness=(
                    strike_input if self.chain.moneyness_grid is not None else None
                ),
                expiry_days=expiry,
                exercise_style=self.chain.exercise_style,
                settlement_type=self.chain.settlement_type,
                contract_multiplier=self.chain.contract_multiplier,
                chain_id=self.chain.chain_id,
                strike_absolute=(
                    strike_input if self.chain.strike_grid is not None else None
                ),
            )
            for expiry in expiry_days
            for strike_input in grid
            for call_put in self.chain.call_put
        )

    def selected_expiry_days(self) -> tuple[int, ...]:
        liquidity = self.chain.liquidity_filter
        if liquidity is None:
            return self.chain.expiry_days
        return tuple(
            value
            for value in self.chain.expiry_days
            if value <= liquidity.max_expiry_days
        )

    def selected_grid(self) -> tuple[Decimal, ...]:
        grid = self.chain.moneyness_grid or self.chain.strike_grid or ()
        liquidity = self.chain.liquidity_filter
        if liquidity is None:
            return grid
        if self.chain.moneyness_grid is None:
            raise ValueError("liquidity filtering requires a moneyness_grid")
        return tuple(
            value
            for value in grid
            if liquidity.min_moneyness <= value <= liquidity.max_moneyness
        )

    def absolute_strike(
        self,
        listing_spot: Decimal,
        moneyness: Decimal | None,
        absolute_strike: Decimal | None = None,
    ) -> Decimal:
        if (moneyness is None) == (absolute_strike is None):
            raise ValueError(
                "exactly one of moneyness and absolute_strike is required"
            )
        if moneyness is not None:
            raw_strike = listing_spot * moneyness
        else:
            if absolute_strike is None:
                raise ValueError("absolute_strike is required")
            raw_strike = absolute_strike
        increment_units = raw_strike / self.chain.strike_increment
        rounded_units = increment_units.quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
        return rounded_units * self.chain.strike_increment

    def _template_id(
        self, expiry_days: int, strike_input: Decimal, call_put: str
    ) -> str:
        strike_token = format(strike_input.normalize(), "f").replace(".", "p")
        grid_prefix = "M" if self.chain.moneyness_grid is not None else "K"
        return (
            f"{self.chain.chain_id}-{call_put.upper()}-"
            f"{expiry_days:04d}D-{grid_prefix}{strike_token}"
        )


def parse_option_chain(
    raw: Any, *, schema_version: str, policy: ConfigVersionPolicy
) -> OptionChainConfig:
    """Validate and canonicalize the static option-chain contract."""

    if not isinstance(raw, dict):
        raise ValueError(f"schema_version {schema_version} requires option_chain")
    chain_id = raw.get("chain_id")
    if not isinstance(chain_id, str) or not chain_id:
        raise ValueError("option_chain.chain_id must be non-empty")

    raw_expiry_days = raw.get("expiry_days")
    if (
        not isinstance(raw_expiry_days, list)
        or not raw_expiry_days
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in raw_expiry_days
        )
    ):
        raise ValueError("option_chain.expiry_days must be a non-empty integer array")
    expiry_days = tuple(raw_expiry_days)
    if any(value <= 0 for value in expiry_days) or any(
        right <= left for left, right in zip(expiry_days, expiry_days[1:])
    ):
        raise ValueError(
            "option_chain.expiry_days must be positive and strictly increasing"
        )

    has_moneyness_grid = "moneyness_grid" in raw
    has_strike_grid = "strike_grid" in raw
    if has_moneyness_grid == has_strike_grid:
        raise ValueError(
            "option_chain requires exactly one of moneyness_grid or strike_grid"
        )
    grid_field = "moneyness_grid" if has_moneyness_grid else "strike_grid"
    raw_grid = raw.get(grid_field)
    if not isinstance(raw_grid, list) or not raw_grid:
        raise ValueError(f"option_chain.{grid_field} must be a non-empty array")
    try:
        parsed_grid = tuple(Decimal(str(value)) for value in raw_grid)
    except Exception as error:
        raise ValueError(
            f"option_chain.{grid_field} must contain finite decimals"
        ) from error
    if any(not value.is_finite() or value <= 0 for value in parsed_grid) or any(
        right <= left for left, right in zip(parsed_grid, parsed_grid[1:])
    ):
        raise ValueError(
            f"option_chain.{grid_field} must be finite, positive and strictly increasing"
        )
    moneyness_grid = parsed_grid if has_moneyness_grid else None
    strike_grid = parsed_grid if has_strike_grid else None

    raw_call_put = raw.get("call_put")
    if not isinstance(raw_call_put, list) or set(raw_call_put) != {"call", "put"}:
        raise ValueError("option_chain.call_put must contain paired call and put")
    if len(raw_call_put) != 2:
        raise ValueError("option_chain.call_put must not contain duplicates")
    call_put = ("call", "put")

    if raw.get("listing_rule") != "snapshot_start":
        raise ValueError("option_chain.listing_rule must be snapshot_start")
    if raw.get("roll_rule") != "static":
        raise ValueError("option_chain.roll_rule must be static")
    if raw.get("strike_rounding") != "ROUND_HALF_EVEN":
        raise ValueError("option_chain.strike_rounding must be ROUND_HALF_EVEN")
    try:
        strike_increment = Decimal(str(raw.get("strike_increment")))
        contract_multiplier = Decimal(str(raw.get("contract_multiplier")))
    except Exception as error:
        raise ValueError(
            "option_chain strike_increment and contract_multiplier must be decimals"
        ) from error
    if (
        not strike_increment.is_finite()
        or strike_increment <= 0
        or not contract_multiplier.is_finite()
        or contract_multiplier <= 0
    ):
        raise ValueError(
            "option_chain strike_increment and contract_multiplier must be positive"
        )
    if strike_increment.quantize(
        Decimal("0.00000001"), rounding=ROUND_HALF_EVEN
    ) != strike_increment:
        raise ValueError(
            "option_chain.strike_increment supports at most 8 decimal places"
        )
    if raw.get("exercise_style") != "european":
        raise ValueError("option_chain.exercise_style must be european")
    settlement_type = raw.get("settlement_type")
    if not isinstance(settlement_type, str) or not settlement_type:
        raise ValueError("option_chain.settlement_type must be non-empty")
    liquidity_filter = parse_liquidity_filter(
        raw.get("liquidity_filter"), schema_version=schema_version, policy=policy
    )
    if liquidity_filter is not None:
        if moneyness_grid is None:
            raise ValueError("config liquidity filtering requires moneyness_grid")
        if not any(value <= liquidity_filter.max_expiry_days for value in expiry_days):
            raise ValueError("option_chain liquidity filter selects no expiry")
        if not any(
            liquidity_filter.min_moneyness
            <= value
            <= liquidity_filter.max_moneyness
            for value in moneyness_grid
        ):
            raise ValueError("option_chain liquidity filter selects no moneyness")
    return OptionChainConfig(
        chain_id=chain_id,
        expiry_days=expiry_days,
        moneyness_grid=moneyness_grid,
        strike_grid=strike_grid,
        call_put=call_put,
        listing_rule="snapshot_start",
        roll_rule="static",
        strike_increment=strike_increment,
        strike_rounding="ROUND_HALF_EVEN",
        exercise_style="european",
        settlement_type=settlement_type,
        contract_multiplier=contract_multiplier,
        liquidity_filter=liquidity_filter,
    )


def parse_liquidity_filter(
    raw: Any, *, schema_version: str, policy: ConfigVersionPolicy
) -> OptionLiquidityFilter | None:
    if not policy.require_liquidity_and_noise:
        if raw is not None:
            raise ValueError(
                "option_chain.liquidity_filter requires schema_version 1.4.0+"
            )
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"schema_version {schema_version} requires option_chain.liquidity_filter"
        )
    if raw.get("type") != "listing_moneyness_band_and_max_expiry":
        raise ValueError(
            "option_chain.liquidity_filter.type must be "
            "listing_moneyness_band_and_max_expiry"
        )
    if raw.get("boundary", "inclusive") != "inclusive":
        raise ValueError("option_chain.liquidity_filter.boundary must be inclusive")
    max_expiry_days = raw.get("max_expiry_days")
    if (
        isinstance(max_expiry_days, bool)
        or not isinstance(max_expiry_days, int)
        or max_expiry_days <= 0
    ):
        raise ValueError(
            "option_chain.liquidity_filter.max_expiry_days must be a positive integer"
        )
    try:
        minimum = Decimal(str(raw.get("min_moneyness")))
        maximum = Decimal(str(raw.get("max_moneyness")))
    except Exception as error:
        raise ValueError(
            "option_chain liquidity moneyness bounds must be decimals"
        ) from error
    if (
        not minimum.is_finite()
        or not maximum.is_finite()
        or minimum <= 0
        or maximum <= minimum
    ):
        raise ValueError(
            "option_chain liquidity moneyness bounds must be finite, positive and increasing"
        )
    return OptionLiquidityFilter(max_expiry_days, minimum, maximum)


def parse_quote_model(
    raw: Any, *, schema_version: str, policy: ConfigVersionPolicy
) -> tuple[dict[str, Any], BidAskNoiseConfig | None]:
    if not isinstance(raw, dict):
        raise ValueError("quote_model must be an object")
    relative_half_spread = finite_float(
        raw.get("relative_half_spread"),
        field_name="quote_model.relative_half_spread",
    )
    minimum_half_spread = finite_float(
        raw.get("minimum_half_spread"),
        field_name="quote_model.minimum_half_spread",
    )
    if relative_half_spread < 0 or minimum_half_spread < 0:
        raise ValueError("quote_model half-spreads must be non-negative")

    noise_raw = raw.get("bid_ask_noise")
    if not policy.require_liquidity_and_noise:
        if noise_raw is not None:
            raise ValueError(
                "quote_model.bid_ask_noise requires schema_version 1.4.0+"
            )
        return dict(raw), None
    if not isinstance(noise_raw, dict):
        raise ValueError(
            f"schema_version {schema_version} requires quote_model.bid_ask_noise"
        )
    noise_type = noise_raw.get("type")
    if noise_type != "clipped_gaussian_half_spread_multiplier":
        raise ValueError(
            "quote_model.bid_ask_noise.type must be "
            "clipped_gaussian_half_spread_multiplier"
        )
    standard_deviation = finite_float(
        noise_raw.get("standard_deviation"),
        field_name="quote_model.bid_ask_noise.standard_deviation",
    )
    minimum_multiplier = finite_float(
        noise_raw.get("minimum_multiplier"),
        field_name="quote_model.bid_ask_noise.minimum_multiplier",
    )
    maximum_multiplier = finite_float(
        noise_raw.get("maximum_multiplier"),
        field_name="quote_model.bid_ask_noise.maximum_multiplier",
    )
    stream_namespace = noise_raw.get("stream_namespace")
    if standard_deviation < 0:
        raise ValueError("quote noise standard_deviation must be non-negative")
    if minimum_multiplier < 0 or maximum_multiplier < minimum_multiplier:
        raise ValueError(
            "quote noise multiplier bounds must be non-negative and increasing"
        )
    if not isinstance(stream_namespace, str) or not stream_namespace:
        raise ValueError("quote noise stream_namespace must be non-empty")
    noise = BidAskNoiseConfig(
        noise_type,
        standard_deviation,
        minimum_multiplier,
        maximum_multiplier,
        stream_namespace,
    )
    normalized = dict(raw)
    normalized["relative_half_spread"] = relative_half_spread
    normalized["minimum_half_spread"] = minimum_half_spread
    normalized["bid_ask_noise"] = noise.as_dict()
    return normalized, noise


def validate_chain_strikes(
    underlyings: tuple[UnderlyingConfig, ...],
    builder: OptionChainBuilder,
    underlying_minimum_price_increment: Decimal,
    quote_decimal_places: int,
) -> None:
    for underlying in underlyings:
        grid = builder.selected_grid()
        listing_spot = quantize_to_increment(
            underlying.initial_spot,
            underlying_minimum_price_increment,
            decimal_places=quote_decimal_places,
        )
        strikes = [
            builder.absolute_strike(
                listing_spot,
                strike_input if builder.chain.moneyness_grid is not None else None,
                strike_input if builder.chain.strike_grid is not None else None,
            )
            for strike_input in grid
        ]
        if any(strike <= 0 for strike in strikes) or len(strikes) != len(set(strikes)):
            raise ValueError(
                "option_chain grid collapses after strike rounding for "
                f"{underlying.underlying_id}"
            )


def option_id(underlying_id: str, template_id: str) -> str:
    return f"{underlying_id}-{template_id}"
