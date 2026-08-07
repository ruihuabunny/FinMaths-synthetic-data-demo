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
    ql_date,
)


class UnderlyingDailyGenerator(QuantLibGeneratorBase):
    """Generate underlying masters, P paths and per-date pricing metadata.

    This is the only generator that consumes ``underlying_simulation`` and its
    Lambda/D/R dependence contract.  It exposes realized spot rows to the
    pipeline; it never generates option contracts or option quotes.
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

    def underlying_dependence_row(self, run_id: str) -> tuple[Any, ...] | None:
        """Build the private, canonical P-measure dependence row.

        Legacy configs return ``None``.  The resulting table is authoring
        provenance and intentionally has no solver-visible view.
        """

        simulation = self.config.underlying_simulation
        if simulation is None:
            return None
        logical = [
            self.config.snapshot_id,
            simulation.dependence_spec_id,
            simulation.measure,
            canonical_json(simulation.driver_order),
            simulation.formulation,
            canonical_json(simulation.factor_loading_matrix),
            canonical_json(simulation.idiosyncratic_diagonal),
            canonical_json(simulation.correlation_matrix),
            simulation.matrix_dtype,
            simulation.factorization_method,
            simulation.factorization_order,
            simulation.time_grid,
            simulation.regime_id,
            self.config.generator_config_id,
        ]
        return (*logical, run_id)

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

        Only the close shock uses ``underlying_simulation``.  OHLC range and
        activity retain separate streams, and derivative pricing never receives
        the dependence contract.
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
        range_shock = abs(
            self._gaussian("underlying-range", underlying.underlying_id, market_date)
        )
        close = self.quantize_underlying_price(
            process.evolve(0.0, float(previous_close), dt, close_shock)
        )
        # Persisted precision is part of the path law: an append run restarts
        # from this configured-precision close, exactly as a one-shot run advances
        # from the preceding in-memory row.
        open_price = self.quantize_underlying_price(previous_close)
        range_fraction = effective_volatility * math.sqrt(dt) * range_shock * 0.25
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
        return (*logical, run_id)

    def initial_underlying_daily_row(
        self, underlying: UnderlyingConfig, run_id: str
    ) -> tuple[Any, ...]:
        """Materialize ``S(start_date)=initial_spot`` without a fake transition."""

        initial_spot = self.quantize_underlying_price(underlying.initial_spot)
        volume_uniform = self._uniform(
            "underlying-volume", underlying.underlying_id, self.config.start_date
        )
        volume = 750_000 + int(volume_uniform * 500_000)
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
            "ohlc_model": "separate_synthetic_range-v1",
        }
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
                "quote_iv_source": "QuantLib inversion of canonical option mid",
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
