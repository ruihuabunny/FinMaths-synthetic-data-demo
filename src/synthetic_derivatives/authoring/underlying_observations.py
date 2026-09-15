"""Legacy and config-1.8 OHLC/volume observation laws."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any

from synthetic_derivatives.authoring.config_models import UnderlyingConfig
from synthetic_derivatives.authoring.generator_common import (
    keyed_mean_preserving_lognormal_int64,
)
from synthetic_derivatives.authoring.underlying_path import UnderlyingPathGenerator


class UnderlyingObservationGenerator:
    """Generate OHLC extrema and volume conditional on published close state."""

    def __init__(self, generator: Any, path: UnderlyingPathGenerator) -> None:
        self._generator = generator
        self.path = path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._generator, name)

    def legacy_extrema(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        open_price: Decimal,
        close_price: Decimal,
        effective_volatility: float,
        year_fraction: float,
    ) -> tuple[Decimal, Decimal]:
        range_shock = abs(
            self._gaussian("underlying-range", underlying.underlying_id, market_date)
        )
        range_fraction = (
            effective_volatility * math.sqrt(year_fraction) * range_shock * 0.25
        )
        high = self.quantize_underlying_price(
            max(open_price, close_price) * Decimal(str(1.0 + range_fraction))
        )
        low = self.quantize_underlying_price(
            max(
                self.underlying_price_increment,
                min(open_price, close_price)
                * Decimal(str(max(0.0, 1.0 - range_fraction))),
            )
        )
        return high, low

    def intraday_bridge_prices(
        self,
        underlying: UnderlyingConfig,
        previous_date: date,
        market_date: date,
        open_price: Decimal,
        close_price: Decimal,
    ) -> tuple[Decimal, ...]:
        bridge = self.config.intraday_bridge
        if bridge is None:
            raise ValueError("intraday bridge requires schema_version 1.8.0")
        start_offset = float((previous_date - self.config.start_date).days)
        end_offset = float((market_date - self.config.start_date).days)
        if end_offset <= start_offset:
            raise ValueError("Brownian-bridge interval must be positive")

        mean_clock = [0.0]
        variance_clock = [0.0]
        for step in range(1, bridge.steps + 1):
            fraction = step / bridge.steps
            offset = start_offset + fraction * (end_offset - start_offset)
            mean, variance = self.path.integrated_log_moments(
                underlying, start_offset, offset
            )
            mean_clock.append(mean)
            variance_clock.append(variance)
        total_variance = variance_clock[-1]
        if total_variance <= 0.0:
            raise ValueError("Brownian-bridge total variance must be positive")

        brownian_motion = [0.0]
        for step in range(1, bridge.steps + 1):
            increment_variance = variance_clock[step] - variance_clock[step - 1]
            if increment_variance < -1e-15:
                raise ValueError(
                    "Brownian-bridge variance clock must be nondecreasing"
                )
            shock = self._gaussian(
                bridge.stream_namespace,
                bridge.bridge_spec_id,
                "increment",
                market_date,
                underlying.underlying_id,
                step,
            )
            brownian_motion.append(
                brownian_motion[-1]
                + math.sqrt(max(0.0, increment_variance)) * shock
            )

        log_open = math.log(float(open_price))
        log_close = math.log(float(close_price))
        endpoint_innovation = log_close - log_open - mean_clock[-1]
        terminal_motion = brownian_motion[-1]
        prices = [open_price]
        for step in range(1, bridge.steps):
            variance_fraction = variance_clock[step] / total_variance
            bridge_residual = brownian_motion[step] - variance_fraction * terminal_motion
            log_price = (
                log_open
                + mean_clock[step]
                + variance_fraction * endpoint_innovation
                + bridge_residual
            )
            price = self.quantize_underlying_price(math.exp(log_price))
            if price <= 0:
                raise ValueError(
                    "Brownian-bridge price must remain positive after quantization"
                )
            prices.append(price)
        prices.append(close_price)
        return tuple(prices)

    def underlying_volume(
        self, underlying: UnderlyingConfig, market_date: date
    ) -> int:
        model = self.config.volume_model
        if model is None:
            volume_uniform = self._uniform(
                "underlying-volume", underlying.underlying_id, market_date
            )
            return 750_000 + int(volume_uniform * 500_000)
        if underlying.base_volume is None or underlying.volume_log_stddev is None:
            raise ValueError("config-1.8 underlying volume parameters are required")
        gaussian = self._gaussian(
            model.stream_namespace,
            model.volume_spec_id,
            market_date,
            underlying.underlying_id,
        )
        return keyed_mean_preserving_lognormal_int64(
            underlying.base_volume,
            underlying.volume_log_stddev,
            gaussian,
        )
