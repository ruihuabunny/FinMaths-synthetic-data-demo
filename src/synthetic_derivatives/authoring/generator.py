"""Deterministic QuantLib market-state and option-quote generation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

import QuantLib as ql

from synthetic_derivatives.authoring.config import (
    GeneratorConfig,
    OptionTemplate,
    UnderlyingConfig,
    option_id,
)


PINNED_QUANTLIB_VERSION = "1.39"
PRICE_QUANTUM = Decimal("0.00000001")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def logical_row_hash(values: list[Any]) -> str:
    encoded = canonical_json([_json_value(value) for value in values]).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def quantize_price(value: float | Decimal) -> Decimal:
    result = Decimal(str(value)).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return abs(result) if result == 0 else result


def ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def py_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


class QuantLibGenerator:
    def __init__(self, config: GeneratorConfig):
        if ql.__version__ != PINNED_QUANTLIB_VERSION:
            raise RuntimeError(
                f"QuantLib version mismatch: expected {PINNED_QUANTLIB_VERSION}, got {ql.__version__}"
            )
        if config.calendar != "WeekendsOnly" or config.day_count != "Actual365Fixed":
            raise ValueError("v1 supports WeekendsOnly and Actual365Fixed only")
        self.config = config
        self.calendar = ql.WeekendsOnly()
        self.day_count = ql.Actual365Fixed()

    def business_dates(self, start: date, count: int) -> list[date]:
        if count < 1:
            raise ValueError("business-day count must be positive")
        current = self.calendar.adjust(ql_date(start), ql.Following)
        result: list[date] = []
        while len(result) < count:
            if self.calendar.isBusinessDay(current):
                result.append(py_date(current))
            current = current + 1
        return result

    def business_dates_between(self, start: date, end: date) -> list[date]:
        if end < start:
            raise ValueError("end date must not be before start date")
        result: list[date] = []
        current = ql_date(start)
        final = ql_date(end)
        while current <= final:
            if self.calendar.isBusinessDay(current):
                result.append(py_date(current))
            current = current + 1
        return result

    def next_business_dates(self, last_date: date, count: int) -> list[date]:
        result: list[date] = []
        current = ql_date(last_date) + 1
        while len(result) < count:
            if self.calendar.isBusinessDay(current):
                result.append(py_date(current))
            current = current + 1
        return result

    def underlying_master_row(
        self, underlying: UnderlyingConfig, run_id: str
    ) -> tuple[Any, ...]:
        logical = [
            self.config.snapshot_id,
            underlying.underlying_id,
            self.config.currency,
            "synthetic_equity",
            quantize_price(underlying.initial_spot),
            underlying.physical_drift,
            underlying.physical_volatility,
            underlying.risk_free_rate,
            underlying.dividend_yield,
            underlying.base_implied_volatility,
            self.config.generator_config_id,
        ]
        definition = logical
        if not (
            underlying.physical_drift_function.is_constant
            and underlying.physical_volatility_function.is_constant
        ):
            definition = [
                *logical,
                underlying.physical_drift_function.as_dict(),
                underlying.physical_volatility_function.as_dict(),
            ]
        return (*logical, logical_row_hash(definition), run_id, run_id)

    def option_contract_row(
        self,
        underlying: UnderlyingConfig,
        template: OptionTemplate,
        run_id: str,
    ) -> tuple[Any, ...]:
        strike = quantize_price(underlying.initial_spot * template.strike_moneyness)
        expiry_unadjusted = ql_date(
            self.config.start_date + timedelta(days=template.expiry_days)
        )
        expiry = py_date(self.calendar.adjust(expiry_unadjusted, ql.Following))
        logical = [
            self.config.snapshot_id,
            option_id(underlying.underlying_id, template.template_id),
            underlying.underlying_id,
            template.template_id,
            template.call_put,
            strike,
            expiry,
            template.exercise_style,
            template.settlement_type,
            quantize_price(template.contract_multiplier),
        ]
        return (*logical, logical_row_hash(logical), run_id, run_id)

    def underlying_daily_row(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date,
        previous_close: Decimal,
        run_id: str,
    ) -> tuple[Any, ...]:
        valuation_date = ql_date(market_date)
        previous_ql_date = ql_date(previous_date)
        dt = self.day_count.yearFraction(previous_ql_date, valuation_date)
        if dt <= 0:
            raise ValueError("underlying dates must be strictly increasing")

        effective_drift, effective_volatility = self.physical_interval_parameters(
            underlying, previous_date, market_date
        )

        ql.Settings.instance().evaluationDate = valuation_date
        spot_quote = ql.QuoteHandle(ql.SimpleQuote(float(previous_close)))
        zero_dividend = ql.YieldTermStructureHandle(
            ql.FlatForward(valuation_date, 0.0, self.day_count)
        )
        physical_drift = ql.YieldTermStructureHandle(
            ql.FlatForward(valuation_date, effective_drift, self.day_count)
        )
        volatility = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(
                valuation_date,
                self.calendar,
                effective_volatility,
                self.day_count,
            )
        )
        process = ql.BlackScholesMertonProcess(
            spot_quote, zero_dividend, physical_drift, volatility
        )
        close_shock = self._gaussian("underlying-close", underlying.underlying_id, market_date)
        range_shock = abs(
            self._gaussian("underlying-range", underlying.underlying_id, market_date)
        )
        close = quantize_price(
            process.evolve(0.0, float(previous_close), dt, close_shock)
        )
        open_price = quantize_price(previous_close)
        range_fraction = effective_volatility * math.sqrt(dt) * range_shock * 0.25
        high = quantize_price(max(open_price, close) * Decimal(str(1.0 + range_fraction)))
        low = quantize_price(
            max(
                Decimal("0.00000001"),
                min(open_price, close) * Decimal(str(max(0.0, 1.0 - range_fraction))),
            )
        )
        volume_uniform = self._uniform(
            "underlying-volume", underlying.underlying_id, market_date
        )
        volume = 750_000 + int(volume_uniform * 500_000)
        logical = [
            self.config.snapshot_id,
            market_date,
            underlying.underlying_id,
            open_price,
            high,
            low,
            close,
            close,
            volume,
            Decimal("0.00000000"),
            "none",
        ]
        return (*logical, logical_row_hash(logical), run_id)

    def option_daily_row(
        self,
        underlying: UnderlyingConfig,
        contract_row: tuple[Any, ...],
        market_date: date,
        spot_close: Decimal,
        run_id: str,
    ) -> tuple[Any, ...] | None:
        (
            _, option_identifier, underlying_id, _, call_put, strike, expiry,
            exercise_style, settlement_type, multiplier, *_lineage,
        ) = contract_row
        if market_date >= expiry:
            return None

        evaluation_date = ql_date(market_date)
        expiry_date = ql_date(expiry)
        ql.Settings.instance().evaluationDate = evaluation_date
        maturity = self.day_count.yearFraction(evaluation_date, expiry_date)
        forward = float(spot_close) * math.exp(
            (underlying.risk_free_rate - underlying.dividend_yield) * maturity
        )
        log_moneyness = math.log(float(strike) / forward)
        smile = self.config.smile
        implied_volatility = max(
            smile["minimum_volatility"],
            underlying.base_implied_volatility
            + smile["skew"] * log_moneyness
            + smile["curvature"] * log_moneyness * log_moneyness
            + smile["term_slope"] * maturity,
        )
        spot_handle = ql.QuoteHandle(ql.SimpleQuote(float(spot_close)))
        risk_free_curve = ql.YieldTermStructureHandle(
            ql.FlatForward(evaluation_date, underlying.risk_free_rate, self.day_count)
        )
        dividend_curve = ql.YieldTermStructureHandle(
            ql.FlatForward(evaluation_date, underlying.dividend_yield, self.day_count)
        )
        vol_surface = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(
                evaluation_date, self.calendar, implied_volatility, self.day_count
            )
        )
        process = ql.BlackScholesMertonProcess(
            spot_handle, dividend_curve, risk_free_curve, vol_surface
        )
        option_type = ql.Option.Call if call_put == "call" else ql.Option.Put
        option = ql.VanillaOption(
            ql.PlainVanillaPayoff(option_type, float(strike)),
            ql.EuropeanExercise(expiry_date),
        )
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        mid = quantize_price(option.NPV())
        half_spread = max(
            Decimal(str(self.config.quote_model["minimum_half_spread"])),
            mid * Decimal(str(self.config.quote_model["relative_half_spread"])),
        )
        bid = quantize_price(max(Decimal("0"), mid - half_spread))
        ask = quantize_price(mid + half_spread)
        activity = self._uniform("option-activity", option_identifier, market_date)
        volume = int(25 + activity * 475)
        open_interest = int(500 + activity * 4_500)
        logical = [
            self.config.snapshot_id,
            market_date,
            underlying_id,
            option_identifier,
            call_put,
            strike,
            expiry,
            exercise_style,
            settlement_type,
            multiplier,
            bid,
            ask,
            mid,
            mid,
            volume,
            open_interest,
        ]
        return (*logical, logical_row_hash(logical), run_id)

    def pricing_metadata_row(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date,
        run_id: str,
    ) -> tuple[Any, ...]:
        valuation_timestamp = datetime.combine(
            market_date,
            time.fromisoformat(self.config.valuation_time_utc),
            tzinfo=timezone.utc,
        )
        physical_dynamics_value: dict[str, Any] = {
            "measure": "P",
            "process": self.config.physical_process,
            "drift": underlying.physical_drift,
            "volatility": underlying.physical_volatility,
            "increment_partition": "sha256(snapshot_id,seed,purpose,entity,date)",
        }
        if not (
            underlying.physical_drift_function.is_constant
            and underlying.physical_volatility_function.is_constant
        ):
            effective_drift, effective_volatility = self.physical_interval_parameters(
                underlying, previous_date, market_date
            )
            physical_dynamics_value.update(
                {
                    "drift": effective_drift,
                    "volatility": effective_volatility,
                    "drift_function": underlying.physical_drift_function.as_dict(),
                    "volatility_function": (
                        underlying.physical_volatility_function.as_dict()
                    ),
                    "time_origin": self.config.start_date.isoformat(),
                    "time_axis": "calendar_day_offset/Actual365Fixed",
                    "interval_start": previous_date.isoformat(),
                    "interval_end": market_date.isoformat(),
                    "interval_reduction": {
                        "drift": "arithmetic_mean",
                        "volatility": "root_mean_square",
                    },
                }
            )
        physical_dynamics = canonical_json(physical_dynamics_value)
        pricing_dynamics = canonical_json(
            {
                "measure": "Q",
                "process": "QuantLib.BlackScholesMertonProcess",
                "base_implied_volatility": underlying.base_implied_volatility,
                "smile": self.config.smile,
            }
        )
        discount_curve = canonical_json(
            {"type": "flat_continuous", "rate": underlying.risk_free_rate}
        )
        dividend_curve = canonical_json(
            {"type": "flat_continuous", "yield": underlying.dividend_yield}
        )
        input_precision = canonical_json(
            {"dtype": "float64", "market_quote_decimal_places": 8}
        )
        canonicalization = canonical_json(
            {
                "decimal_places": self.config.quote_decimal_places,
                "rounding": "ROUND_HALF_EVEN",
                "encoding": "UTF-8",
            }
        )
        logical = [
            self.config.snapshot_id,
            valuation_timestamp,
            market_date,
            underlying.underlying_id,
            self.config.currency,
            discount_curve,
            underlying.risk_free_rate,
            dividend_curve,
            underlying.dividend_yield,
            underlying.risk_free_rate - underlying.dividend_yield,
            self.config.calendar,
            self.config.day_count,
            physical_dynamics,
            pricing_dynamics,
            self.config.pricing_model,
            self.config.pricing_engine,
            self.config.generator_version,
            self.config.seed,
            self.config.rng,
            input_precision,
            canonicalization,
        ]
        return (*logical, logical_row_hash(logical), run_id)

    def physical_interval_parameters(
        self,
        underlying: UnderlyingConfig,
        previous_date: date,
        market_date: date,
    ) -> tuple[float, float]:
        if market_date <= previous_date:
            raise ValueError("underlying dates must be strictly increasing")
        start_day_offset = float((previous_date - self.config.start_date).days)
        end_day_offset = float((market_date - self.config.start_date).days)
        # These two reductions preserve both integrals in the exact
        # time-inhomogeneous GBM transition: integral(mu dt) and
        # integral(sigma^2 dt).
        effective_drift = underlying.physical_drift_function.interval_average(
            start_day_offset, end_day_offset
        )
        effective_volatility = (
            underlying.physical_volatility_function.interval_root_mean_square(
                start_day_offset, end_day_offset
            )
        )
        return effective_drift, effective_volatility

    def previous_business_date(self, market_date: date) -> date:
        return py_date(self.calendar.advance(ql_date(market_date), -1, ql.Days))

    def _derived_seed(self, *parts: Any) -> int:
        material = "|".join(
            [self.config.snapshot_id, str(self.config.seed), *(str(part) for part in parts)]
        ).encode("utf-8")
        seed = int.from_bytes(hashlib.sha256(material).digest()[:4], "big")
        return seed or 1

    def _gaussian(self, *parts: Any) -> float:
        uniform = ql.MersenneTwisterUniformRng(self._derived_seed(*parts))
        gaussian = ql.BoxMullerMersenneTwisterGaussianRng(uniform)
        return float(gaussian.next().value())

    def _uniform(self, *parts: Any) -> float:
        uniform = ql.MersenneTwisterUniformRng(self._derived_seed(*parts))
        return float(uniform.next().value())
