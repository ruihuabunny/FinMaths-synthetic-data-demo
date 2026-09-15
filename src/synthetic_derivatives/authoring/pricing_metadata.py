"""P/Q provenance payload construction and canonical serialization."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from synthetic_derivatives.authoring.canonicalization import canonical_json
from synthetic_derivatives.authoring.config_models import UnderlyingConfig
from synthetic_derivatives.authoring.row_contracts import PricingMetadataRow


class PricingMetadataBuilder:
    """Build metadata rows without owning path draws or persistence."""

    def __init__(self, generator: Any) -> None:
        self._generator = generator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._generator, name)

    def build(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date | None,
        run_id: str,
    ) -> PricingMetadataRow:
        """Build per-underlying metadata for the realized market-date slice.

        The row records both P path provenance and the Q inputs later consumed
        by ``OptionDailyGenerator``.  It describes the pricing boundary but does
        not itself generate a derivative quote.
        """

        valuation_timestamp = datetime.combine(
            market_date,
            time.fromisoformat(self.config.valuation_time_utc),
            tzinfo=timezone.utc,
        )
        physical_dynamics_value: dict[str, Any] = {
            "measure": "P",
            "process": self.config.physical_process,
            "state_role": (
                "initial_condition" if previous_date is None else "interval_transition"
            ),
            "drift": underlying.physical_drift,
            "volatility": underlying.physical_volatility,
            "increment_partition": "sha256(snapshot_id,seed,purpose,entity,date)",
            "state_precision_contract": "published_decimal_close_is_restart_state",
        }
        if self.config.intraday_bridge is None:
            physical_dynamics_value["ohlc_model"] = "separate_synthetic_range-v1"
        else:
            volume_model = self.config.volume_model
            if volume_model is None:
                raise ValueError("config-1.8 volume model is required")
            physical_dynamics_value.update(
                {
                    "ohlc_model_id": self.config.intraday_bridge.bridge_spec_id,
                    "volume_model_id": volume_model.volume_spec_id,
                }
            )
        has_market_price_increments = (
            self.config.underlying_minimum_price_increment != self.price_quantum
            or self.config.option_minimum_price_increment != self.price_quantum
        )
        if has_market_price_increments:
            physical_dynamics_value.update(
                {
                    "state_precision_contract": (
                        "published_minimum_price_increment_close_is_restart_state"
                    ),
                    "underlying_minimum_price_increment": str(
                        self.config.underlying_minimum_price_increment
                    ),
                }
            )
        if self.config.underlying_simulation is not None:
            simulation = self.config.underlying_simulation
            physical_dynamics_value.update(
                {
                    "dependence_spec_id": simulation.dependence_spec_id,
                    "driver_id": underlying.underlying_id,
                    "driver_order": list(simulation.driver_order),
                    "shock_formulation": "Lambda*factor+sqrt(D)*idiosyncratic",
                    "increment_partition": (
                        "sha256(snapshot_id,seed,measure,dependence_spec_id,"
                        "stream_type,stream_id,date)"
                    ),
                }
            )
        if not (
            underlying.physical_drift_function.is_constant
            and underlying.physical_volatility_function.is_constant
        ):
            if previous_date is None:
                effective_drift = underlying.physical_drift_function.value_at(0.0)
                effective_volatility = (
                    underlying.physical_volatility_function.value_at(0.0)
                )
            else:
                effective_drift, effective_volatility = (
                    self.physical_interval_parameters(
                        underlying, previous_date, market_date
                    )
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
                    "interval_reduction": {
                        "drift": (
                            "point_value_at_initial_condition"
                            if previous_date is None
                            else "integral_arithmetic_mean"
                        ),
                        "volatility": (
                            "point_value_at_initial_condition"
                            if previous_date is None
                            else "integrated_variance_root_mean_square"
                        ),
                    },
                }
            )
            if previous_date is not None:
                physical_dynamics_value.update(
                    {
                        "interval_start": previous_date.isoformat(),
                        "interval_end": market_date.isoformat(),
                    }
                )
        physical_dynamics = canonical_json(physical_dynamics_value)
        if self.config.q_pricing is None:
            pricing_dynamics_value = {
                "measure": "Q",
                "process": "QuantLib.BlackScholesMertonProcess",
                "base_implied_volatility": underlying.base_implied_volatility,
                "smile": self.config.smile,
            }
        else:
            pricing_dynamics_value = {
                "measure": "Q",
                "process": "QuantLib.BlackScholesMertonProcess",
                "risk_neutral_drift": "risk_free_rate-dividend_yield",
                "q_pricing": self.config.q_pricing.as_dict(),
                "volatility_function": (
                    underlying.physical_volatility_function.as_dict()
                ),
                "volatility_measure_change": (
                    "same deterministic diffusion coefficient under Girsanov"
                ),
            }
            q_dependence = self.config.q_underlying_dependence
            if q_dependence is not None:
                pricing_dynamics_value["underlying_dependence"] = {
                    "dependence_spec_id": q_dependence.dependence_spec_id,
                    "source_dependence_spec_id": (
                        q_dependence.source_dependence_spec_id
                    ),
                    "mapping_id": q_dependence.mapping_id,
                    "driver_id": underlying.underlying_id,
                    "driver_order": list(q_dependence.driver_order),
                }
        if has_market_price_increments:
            pricing_dynamics_value["option_minimum_price_increment"] = str(
                self.config.option_minimum_price_increment
            )
        pricing_dynamics = canonical_json(pricing_dynamics_value)
        discount_curve = canonical_json(
            {"type": "flat_continuous", "rate": underlying.risk_free_rate}
        )
        dividend_curve = canonical_json(
            {"type": "flat_continuous", "yield": underlying.dividend_yield}
        )
        input_precision_value: dict[str, Any] = {
            "dtype": "float64",
            "market_quote_decimal_places": self.config.quote_decimal_places,
        }
        canonicalization_value: dict[str, Any] = {
            "decimal_places": self.config.quote_decimal_places,
            "rounding": "ROUND_HALF_EVEN",
            "encoding": "UTF-8",
        }
        if has_market_price_increments:
            price_increments = {
                "underlying": str(self.config.underlying_minimum_price_increment),
                "option": str(self.config.option_minimum_price_increment),
            }
            input_precision_value["minimum_price_increments"] = price_increments
            canonicalization_value["minimum_price_increments"] = price_increments
        input_precision = canonical_json(input_precision_value)
        canonicalization = canonical_json(canonicalization_value)
        return PricingMetadataRow(
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
            run_id,
        )
