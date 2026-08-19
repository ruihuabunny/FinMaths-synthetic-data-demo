"""Deterministic P-measure underlying path and metadata generation."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

import QuantLib as ql

from synthetic_derivatives.authoring.config import GeneratorConfig, UnderlyingConfig
from synthetic_derivatives.authoring.generator_common import (
    QuantLibGeneratorBase,
    canonical_json,
    keyed_mean_preserving_lognormal_int64,
    ql_date,
)


class UnderlyingDailyGenerator(QuantLibGeneratorBase):
    """Generate underlying masters, P paths and per-date pricing metadata.

    This is the only generator that consumes the P dependence spec to construct
    shocks. It also serializes the explicit P/Q dependence pair as provenance,
    but never uses the Q spec to generate historical spot paths. It exposes
    realized spot rows to the pipeline; it never generates option contracts or
    option quotes.
    """

    def __init__(self, config: GeneratorConfig):
        """Initialize shared QuantLib infrastructure for underlying generation."""

        super().__init__(config)

    def underlying_master_row(
        self, underlying: UnderlyingConfig, run_id: str
    ) -> tuple[Any, ...]:
        """Build one immutable underlying master row."""

        logical = [
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
        ]
        return (*logical, run_id)

    def underlying_dependence_rows(self, run_id: str) -> tuple[tuple[Any, ...], ...]:
        """Build canonical measure-qualified dependence provenance rows."""

        rows: list[tuple[Any, ...]] = []
        for specification in self.config.underlying_dependence_specs:
            logical = [
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
            ]
            rows.append((*logical, run_id))
        return tuple(rows)

    def intraday_bridge_spec_rows(
        self, run_id: str
    ) -> tuple[tuple[Any, ...], ...]:
        """Build the private config-1.8 bridge provenance row, if any."""

        bridge = self.config.intraday_bridge
        if bridge is None:
            return ()
        logical = [
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
        ]
        return ((*logical, run_id),)

    def underlying_volume_model_rows(
        self, run_id: str
    ) -> tuple[tuple[Any, ...], ...]:
        """Build one private config-1.8 volume-model row per underlying."""

        model = self.config.volume_model
        if model is None:
            return ()
        rows: list[tuple[Any, ...]] = []
        for underlying in self.config.underlyings:
            if (
                underlying.base_volume is None
                or underlying.volume_log_stddev is None
            ):
                raise ValueError(
                    "config-1.8 underlying volume parameters are required"
                )
            logical = [
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
            ]
            rows.append((*logical, run_id))
        return tuple(rows)

    def underlying_daily_row(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date,
        previous_close: Decimal,
        run_id: str,
    ) -> tuple[Any, ...]:
        """Advance one underlying close through one P-measure Markov transition.

        For the current time-inhomogeneous GBM, the persisted, quantized
        ``previous_close`` plus the interval dates are the sufficient restart
        state.  One-shot and incremental runs therefore execute the same
        transition.  Models with latent variance/rate/regime state must extend
        the persisted state before they can use this incremental path.

        Only the close shock uses ``underlying_simulation``.  The config-1.8
        bridge is conditioned on the already-published endpoints and its volume
        law uses a separate keyed stream; derivative pricing receives neither
        observation contract.
        """

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
        # BlackScholesMertonProcess uses (risk-free - dividend) as its drift.
        # Setting q=0 and the risk-free handle to effective_drift therefore
        # realizes the configured P-measure transition without mixing in the
        # Q-measure risk-free/dividend inputs used by option pricing.
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
        # Persisted precision is part of the path law: an append run restarts
        # from this configured-precision close, exactly as a one-shot run advances
        # from the preceding in-memory row.
        open_price = self.quantize_underlying_price(previous_close)
        if self.config.intraday_bridge is None:
            range_shock = abs(
                self._gaussian(
                    "underlying-range", underlying.underlying_id, market_date
                )
            )
            range_fraction = (
                effective_volatility * math.sqrt(dt) * range_shock * 0.25
            )
            high = self.quantize_underlying_price(
                max(open_price, close) * Decimal(str(1.0 + range_fraction))
            )
            low = self.quantize_underlying_price(
                max(
                    self.underlying_price_increment,
                    min(open_price, close)
                    * Decimal(str(max(0.0, 1.0 - range_fraction))),
                )
            )
        else:
            bridge_prices = self.intraday_bridge_prices(
                underlying,
                previous_date,
                market_date,
                open_price,
                close,
            )
            high = max(bridge_prices)
            low = min(bridge_prices)
        volume = self.underlying_volume(underlying, market_date)
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
        return (*logical, run_id)

    def initial_underlying_daily_row(
        self, underlying: UnderlyingConfig, run_id: str
    ) -> tuple[Any, ...]:
        """Materialize ``S(start_date)=initial_spot`` without a fake transition."""

        initial_spot = self.quantize_underlying_price(underlying.initial_spot)
        volume = self.underlying_volume(underlying, self.config.start_date)
        logical = [
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
        ]
        return (*logical, run_id)

    def pricing_metadata_row(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        previous_date: date | None,
        run_id: str,
    ) -> tuple[Any, ...]:
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
        return (*logical, run_id)

    def physical_interval_parameters(
        self,
        underlying: UnderlyingConfig,
        previous_date: date,
        market_date: date,
    ) -> tuple[float, float]:
        """Reduce deterministic functions to exact interval GBM parameters."""

        if market_date <= previous_date:
            raise ValueError("underlying dates must be strictly increasing")
        start_day_offset = float((previous_date - self.config.start_date).days)
        end_day_offset = float((market_date - self.config.start_date).days)
        # These reductions preserve integral(mu dt) and integral(sigma^2 dt)
        # in the exact time-inhomogeneous GBM transition.
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
        """Return exact integrated log drift and variance in Actual/365 time."""

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

    def intraday_bridge_prices(
        self,
        underlying: UnderlyingConfig,
        previous_date: date,
        market_date: date,
        open_price: Decimal,
        close_price: Decimal,
    ) -> tuple[Decimal, ...]:
        """Sample the config-1.8 marginal bridge on its variance-clock grid."""

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
            mean, variance = self.integrated_log_moments(
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
            bridge_residual = (
                brownian_motion[step] - variance_fraction * terminal_motion
            )
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
        """Return legacy uniform or config-1.8 keyed-lognormal daily volume."""

        model = self.config.volume_model
        if model is None:
            volume_uniform = self._uniform(
                "underlying-volume", underlying.underlying_id, market_date
            )
            return 750_000 + int(volume_uniform * 500_000)
        if (
            underlying.base_volume is None
            or underlying.volume_log_stddev is None
        ):
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

    def underlying_close_shock(
        self, underlying_id: str, market_date: date
    ) -> float:
        """Return the canonical P-measure shock for one underlying and date.

        Shared factor draws are keyed by dependence spec, factor index and date,
        so every underlying receives the same factor realization for a time
        slice regardless of generation order.  Idiosyncratic draws are also
        keyed by underlying ID.  Consequently one-shot and append runs replay
        the same ``Lambda * eta + sqrt(D) * epsilon`` transition.

        This method is exclusively part of underlying path simulation.  Option
        pricing is conditionally a function of the realized spot and its own
        pricing inputs; it does not call this method or consume R directly.
        """

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
