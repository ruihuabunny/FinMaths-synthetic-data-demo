"""Compatibility façade assembling P-path, observation, and metadata components."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from synthetic_derivatives.authoring.canonicalization import canonical_json
from synthetic_derivatives.authoring.config_models import GeneratorConfig, UnderlyingConfig
from synthetic_derivatives.authoring.generator_common import QuantLibGeneratorBase
from synthetic_derivatives.authoring.pricing_metadata import PricingMetadataBuilder
from synthetic_derivatives.authoring.row_contracts import (
    IntradayBridgeSpecRow,
    PricingMetadataRow,
    UnderlyingDailyRow,
    UnderlyingDependenceRow,
    UnderlyingMasterRow,
    UnderlyingVolumeModelRow,
)
from synthetic_derivatives.authoring.underlying_observations import (
    UnderlyingObservationGenerator,
)
from synthetic_derivatives.authoring.underlying_path import UnderlyingPathGenerator


class UnderlyingDailyGenerator(QuantLibGeneratorBase):
    """Assemble typed underlying rows while preserving the historic import path."""

    def __init__(self, config: GeneratorConfig):
        super().__init__(config)
        self.path = UnderlyingPathGenerator(self)
        self.observations = UnderlyingObservationGenerator(self, self.path)
        self.metadata_builder = PricingMetadataBuilder(self)

    def underlying_master_row(
        self, underlying: UnderlyingConfig, run_id: str
    ) -> UnderlyingMasterRow:
        return UnderlyingMasterRow(
            self.config.snapshot_id,
            underlying.underlying_id,
            self.config.currency,
            "synthetic_equity",
            self.quantize_underlying_price(underlying.initial_spot),
            underlying.physical_drift,
            underlying.physical_volatility,
            underlying.risk_free_rate,
            underlying.dividend_yield,
            underlying.base_implied_volatility,
            self.config.generator_config_id,
            run_id,
        )

    def underlying_dependence_rows(
        self, run_id: str
    ) -> tuple[UnderlyingDependenceRow, ...]:
        rows: list[UnderlyingDependenceRow] = []
        for specification in self.config.underlying_dependence_specs:
            rows.append(
                UnderlyingDependenceRow(
                    self.config.snapshot_id,
                    specification.dependence_spec_id,
                    specification.measure,
                    specification.source_dependence_spec_id,
                    specification.mapping_id,
                    specification.mapping_type,
                    specification.risk_neutral_measure_id,
                    specification.numeraire_id,
                    specification.rate_path_id,
                    canonical_json(specification.driver_order),
                    specification.formulation,
                    canonical_json(specification.factor_loading_matrix),
                    canonical_json(specification.idiosyncratic_diagonal),
                    canonical_json(specification.correlation_matrix),
                    specification.matrix_dtype,
                    specification.factorization_method,
                    specification.factorization_order,
                    specification.time_grid,
                    specification.regime_id,
                    self.config.generator_config_id,
                    run_id,
                )
            )
        return tuple(rows)

    def intraday_bridge_spec_rows(
        self, run_id: str
    ) -> tuple[IntradayBridgeSpecRow, ...]:
        bridge = self.config.intraday_bridge
        if bridge is None:
            return ()
        return (
            IntradayBridgeSpecRow(
                self.config.snapshot_id,
                bridge.bridge_spec_id,
                bridge.method,
                bridge.steps,
                bridge.grid,
                bridge.variance_clock,
                bridge.endpoint_policy,
                bridge.extrema_policy,
                bridge.cross_asset_policy,
                bridge.stream_namespace,
                self.config.generator_config_id,
                run_id,
            ),
        )

    def underlying_volume_model_rows(
        self, run_id: str
    ) -> tuple[UnderlyingVolumeModelRow, ...]:
        model = self.config.volume_model
        if model is None:
            return ()
        rows: list[UnderlyingVolumeModelRow] = []
        for underlying in self.config.underlyings:
            if underlying.base_volume is None or underlying.volume_log_stddev is None:
                raise ValueError(
                    "config-1.8 underlying volume parameters are required"
                )
            rows.append(
                UnderlyingVolumeModelRow(
                    self.config.snapshot_id,
                    model.volume_spec_id,
                    underlying.underlying_id,
                    model.measure,
                    model.method,
                    underlying.base_volume,
                    underlying.volume_log_stddev,
                    model.rounding,
                    model.overflow_policy,
                    model.dependence_policy,
                    model.stream_namespace,
                    self.config.generator_config_id,
                    run_id,
                )
            )
        return tuple(rows)

    def underlying_daily_row(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date,
        previous_close: Decimal,
        run_id: str,
    ) -> UnderlyingDailyRow:
        transition = self.path.close_transition(
            underlying, market_date, previous_date, previous_close
        )
        if self.config.intraday_bridge is None:
            high, low = self.observations.legacy_extrema(
                underlying,
                market_date,
                transition.open_price,
                transition.close_price,
                transition.effective_volatility,
                transition.year_fraction,
            )
        else:
            bridge_prices = self.observations.intraday_bridge_prices(
                underlying,
                previous_date,
                market_date,
                transition.open_price,
                transition.close_price,
            )
            high = max(bridge_prices)
            low = min(bridge_prices)
        volume = self.observations.underlying_volume(underlying, market_date)
        return UnderlyingDailyRow(
            self.config.snapshot_id,
            market_date,
            underlying.underlying_id,
            transition.open_price,
            high,
            low,
            transition.close_price,
            transition.close_price,
            volume,
            Decimal("0.00000000"),
            "none",
            run_id,
        )

    def initial_underlying_daily_row(
        self, underlying: UnderlyingConfig, run_id: str
    ) -> UnderlyingDailyRow:
        initial_spot = self.quantize_underlying_price(underlying.initial_spot)
        volume = self.observations.underlying_volume(
            underlying, self.config.start_date
        )
        return UnderlyingDailyRow(
            self.config.snapshot_id,
            self.config.start_date,
            underlying.underlying_id,
            initial_spot,
            initial_spot,
            initial_spot,
            initial_spot,
            initial_spot,
            volume,
            Decimal("0.00000000"),
            "none",
            run_id,
        )

    def pricing_metadata_row(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date | None,
        run_id: str,
    ) -> PricingMetadataRow:
        return self.metadata_builder.build(
            underlying, market_date, previous_date, run_id
        )

    def physical_interval_parameters(
        self,
        underlying: UnderlyingConfig,
        previous_date: date,
        market_date: date,
    ) -> tuple[float, float]:
        return self.path.physical_interval_parameters(
            underlying, previous_date, market_date
        )

    def integrated_log_moments(
        self,
        underlying: UnderlyingConfig,
        start_day_offset: float,
        end_day_offset: float,
    ) -> tuple[float, float]:
        return self.path.integrated_log_moments(
            underlying, start_day_offset, end_day_offset
        )

    def intraday_bridge_prices(
        self,
        underlying: UnderlyingConfig,
        previous_date: date,
        market_date: date,
        open_price: Decimal,
        close_price: Decimal,
    ) -> tuple[Decimal, ...]:
        return self.observations.intraday_bridge_prices(
            underlying, previous_date, market_date, open_price, close_price
        )

    def underlying_volume(
        self, underlying: UnderlyingConfig, market_date: date
    ) -> int:
        return self.observations.underlying_volume(underlying, market_date)

    def underlying_close_shock(
        self, underlying_id: str, market_date: date
    ) -> float:
        return self.path.underlying_close_shock(underlying_id, market_date)
