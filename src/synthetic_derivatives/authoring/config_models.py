"""Immutable top-level and per-underlying generator configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from synthetic_derivatives.authoring.dependence import UnderlyingDependenceConfig
from synthetic_derivatives.authoring.deterministic_functions import DeterministicFunction
from synthetic_derivatives.authoring.observation_contracts import (
    IntradayBridgeConfig,
    VolumeModelConfig,
)
from synthetic_derivatives.authoring.option_chain import (
    BidAskNoiseConfig,
    OptionChainConfig,
    OptionTemplate,
)


@dataclass(frozen=True)
class UnderlyingConfig:
    """Validated marginal path and BSM pricing inputs for one underlying."""

    underlying_id: str
    initial_spot: Decimal
    physical_drift: float
    physical_volatility: float
    physical_drift_function: DeterministicFunction
    physical_volatility_function: DeterministicFunction
    risk_free_rate: float
    dividend_yield: float
    base_implied_volatility: float | None
    base_volume: int | None
    volume_log_stddev: float | None


@dataclass(frozen=True)
class QPricingConfig:
    """Common same-currency Q-measure pricing contract for config 1.5+."""

    risk_neutral_measure_id: str
    numeraire_id: str
    rate_path_id: str
    measure_change: str
    volatility_mapping: str
    legacy_authoring_iv_solver_present: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk_neutral_measure_id": self.risk_neutral_measure_id,
            "numeraire_id": self.numeraire_id,
            "rate_path_id": self.rate_path_id,
            "measure_change": self.measure_change,
            "volatility_mapping": self.volatility_mapping,
        }


@dataclass(frozen=True)
class GeneratorConfig:
    """Validated, immutable input contract for one authoring snapshot."""

    schema_version: str
    generator_config_id: str
    generator_version: str
    snapshot_id: str
    seed: int
    rng: str
    start_date: date
    business_days: int
    calendar: str
    day_count: str
    valuation_time_utc: str
    pricing_model: str
    pricing_engine: str
    physical_process: str
    currency: str
    quote_decimal_places: int
    underlying_minimum_price_increment: Decimal
    option_minimum_price_increment: Decimal
    underlyings: tuple[UnderlyingConfig, ...]
    option_templates: tuple[OptionTemplate, ...]
    smile: dict[str, float] | None
    q_pricing: QPricingConfig | None
    quote_model: dict[str, Any]
    bid_ask_noise: BidAskNoiseConfig | None
    underlying_dependence_specs: tuple[UnderlyingDependenceConfig, ...]
    option_chain: OptionChainConfig | None
    intraday_bridge: IntradayBridgeConfig | None
    volume_model: VolumeModelConfig | None

    @property
    def underlying_simulation(self) -> UnderlyingDependenceConfig | None:
        return next(
            (
                specification
                for specification in self.underlying_dependence_specs
                if specification.measure == "P"
            ),
            None,
        )

    @property
    def q_underlying_dependence(self) -> UnderlyingDependenceConfig | None:
        return next(
            (
                specification
                for specification in self.underlying_dependence_specs
                if specification.measure == "Q"
            ),
            None,
        )
