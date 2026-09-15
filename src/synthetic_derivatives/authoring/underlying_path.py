"""P-measure close transition and deterministic interval reductions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import QuantLib as ql

from synthetic_derivatives.authoring.config_models import UnderlyingConfig
from synthetic_derivatives.authoring.generator_common import ql_date


@dataclass(frozen=True)
class UnderlyingCloseTransition:
    """One published rounded-state P transition and its interval parameters."""

    open_price: Decimal
    close_price: Decimal
    year_fraction: float
    effective_drift: float
    effective_volatility: float


class UnderlyingPathGenerator:
    """Generate only P closes; it has no observation or derivative API."""

    def __init__(self, generator: Any) -> None:
        self._generator = generator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._generator, name)

    def close_transition(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date,
        previous_close: Decimal,
    ) -> UnderlyingCloseTransition:
        valuation_date = ql_date(market_date)
        previous_ql_date = ql_date(previous_date)
        dt = self.day_count.yearFraction(previous_ql_date, valuation_date)
        if dt <= 0:
            raise ValueError("underlying dates must be strictly increasing")
        previous_close = self.quantize_underlying_price(previous_close)
        effective_drift, effective_volatility = self.physical_interval_parameters(
            underlying, previous_date, market_date
        )

        ql.Settings.instance().evaluationDate = previous_ql_date
        spot_quote = ql.QuoteHandle(ql.SimpleQuote(float(previous_close)))
        zero_dividend = ql.YieldTermStructureHandle(
            ql.FlatForward(previous_ql_date, 0.0, self.day_count)
        )
        physical_drift = ql.YieldTermStructureHandle(
            ql.FlatForward(previous_ql_date, effective_drift, self.day_count)
        )
        volatility = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(
                previous_ql_date,
                self.calendar,
                effective_volatility,
                self.day_count,
            )
        )
        process = ql.BlackScholesMertonProcess(
            spot_quote, zero_dividend, physical_drift, volatility
        )
        close_shock = self.underlying_close_shock(
            underlying.underlying_id, market_date
        )
        close = self.quantize_underlying_price(
            process.evolve(0.0, float(previous_close), dt, close_shock)
        )
        return UnderlyingCloseTransition(
            open_price=self.quantize_underlying_price(previous_close),
            close_price=close,
            year_fraction=dt,
            effective_drift=effective_drift,
            effective_volatility=effective_volatility,
        )

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
        effective_drift = underlying.physical_drift_function.interval_average(
            start_day_offset, end_day_offset
        )
        effective_volatility = (
            underlying.physical_volatility_function.interval_root_mean_square(
                start_day_offset, end_day_offset
            )
        )
        return effective_drift, effective_volatility

    def integrated_log_moments(
        self,
        underlying: UnderlyingConfig,
        start_day_offset: float,
        end_day_offset: float,
    ) -> tuple[float, float]:
        width_days = end_day_offset - start_day_offset
        if width_days <= 0.0:
            raise ValueError("integrated-log-moment interval must be positive")
        drift_integral = (
            underlying.physical_drift_function.interval_average(
                start_day_offset, end_day_offset
            )
            * width_days
            / 365.0
        )
        variance_integral = (
            underlying.physical_volatility_function.interval_average(
                start_day_offset, end_day_offset, power=2
            )
            * width_days
            / 365.0
        )
        return drift_integral - 0.5 * variance_integral, variance_integral

    def underlying_close_shock(
        self, underlying_id: str, market_date: date
    ) -> float:
        simulation = self.config.underlying_simulation
        if simulation is None:
            return self._gaussian("underlying-close", underlying_id, market_date)

        driver_index = simulation.driver_index(underlying_id)
        factor_component = math.fsum(
            loading
            * self._gaussian(
                "underlying-simulation",
                simulation.measure,
                simulation.dependence_spec_id,
                "factor",
                factor_index,
                market_date,
            )
            for factor_index, loading in enumerate(
                simulation.factor_loading_matrix[driver_index]
            )
        )
        idiosyncratic_shock = self._gaussian(
            "underlying-simulation",
            simulation.measure,
            simulation.dependence_spec_id,
            "idiosyncratic",
            underlying_id,
            market_date,
        )
        return factor_component + math.sqrt(
            simulation.idiosyncratic_diagonal[driver_index]
        ) * idiosyncratic_shock
