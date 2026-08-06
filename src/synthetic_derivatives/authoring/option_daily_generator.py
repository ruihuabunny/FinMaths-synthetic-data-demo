"""Deterministic option-chain contract and daily quote generation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import QuantLib as ql

from synthetic_derivatives.authoring.config import (
    GeneratorConfig,
    OptionChainBuilder,
    OptionTemplate,
    UnderlyingConfig,
    option_id,
)
from synthetic_derivatives.authoring.generator_common import (
    QuantLibGeneratorBase,
    canonical_json,
    py_date,
    ql_date,
    quantize_price,
)


@dataclass(frozen=True)
class OptionDailyResult:
    """One public quote and its optional private quote-IV audit row."""

    quote_row: tuple[Any, ...]
    pricing_audit_row: tuple[Any, ...] | None


class OptionDailyGenerator(QuantLibGeneratorBase):
    """Generate immutable option contracts and quotes from realized spots.

    The class has no underlying-path transition or dependence API.  Correlated
    P-measure simulation is complete before the pipeline passes ``spot_close``
    into ``option_daily_row``.
    """

    def __init__(self, config: GeneratorConfig):
        """Initialize shared QuantLib infrastructure for option generation."""

        super().__init__(config)

    def option_chain_spec_row(self, run_id: str) -> tuple[Any, ...] | None:
        """Build private listing and roll provenance for a generated chain.

        Grid values are serialized as decimal strings so JSON round-tripping
        cannot silently replace the authoring decimal contract with binary
        floating-point values.  This table has no solver-visible view.
        """

        chain = self.config.option_chain
        if chain is None:
            return None
        logical = [
            self.config.snapshot_id,
            chain.chain_id,
            chain.listing_rule,
            self.option_chain_listing_date(),
            chain.roll_rule,
            "moneyness" if chain.moneyness_grid is not None else "strike",
            canonical_json(chain.expiry_days),
            (
                canonical_json(tuple(str(value) for value in chain.moneyness_grid))
                if chain.moneyness_grid is not None
                else None
            ),
            (
                canonical_json(tuple(str(value) for value in chain.strike_grid))
                if chain.strike_grid is not None
                else None
            ),
            canonical_json(chain.call_put),
            quantize_price(chain.strike_increment),
            chain.strike_rounding,
            chain.exercise_style,
            chain.settlement_type,
            quantize_price(chain.contract_multiplier),
            (
                canonical_json(chain.liquidity_filter.as_dict())
                if chain.liquidity_filter is not None
                else None
            ),
            (
                canonical_json(self.config.quote_model)
                if self.config.schema_version in {"1.4.0", "1.5.0"}
                else None
            ),
            self.config.generator_config_id,
        ]
        return (*logical, run_id)

    def option_chain_listing_date(self) -> date:
        """Return the first business date on which static chain contracts list."""

        return py_date(
            self.calendar.adjust(ql_date(self.config.start_date), ql.Following)
        )

    def option_contract_row(
        self,
        underlying: UnderlyingConfig,
        template: OptionTemplate,
        run_id: str,
    ) -> tuple[Any, ...]:
        """Materialize one immutable contract from listing-time state.

        For a 1.3 chain, ``initial_spot`` is the declared listing spot and is
        used at most once to convert moneyness.  The returned absolute strike is
        stored in ``market.option_contracts`` and reused verbatim by every daily
        quote; later realized spots never re-strike the contract.

        Expiry is relative to the adjusted listing date for chain contracts.
        Legacy templates retain their historical start-date behavior.
        """

        listing_date = self.option_chain_listing_date()
        if template.chain_id is None:
            if template.strike_moneyness is None:
                raise ValueError("legacy template requires strike_moneyness")
            strike = quantize_price(
                underlying.initial_spot * template.strike_moneyness
            )
        else:
            if self.config.option_chain is None:
                raise ValueError("chain template requires option_chain config")
            strike = quantize_price(
                OptionChainBuilder(self.config.option_chain).absolute_strike(
                    underlying.initial_spot,
                    template.strike_moneyness,
                    template.strike_absolute,
                )
            )
        expiry_unadjusted = ql_date(
            (
                listing_date
                if template.chain_id is not None
                else self.config.start_date
            )
            + timedelta(days=template.expiry_days)
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
            template.chain_id,
            listing_date,
            quantize_price(underlying.initial_spot),
            (
                quantize_price(template.strike_moneyness)
                if template.strike_moneyness is not None
                else None
            ),
        ]
        return (*logical, run_id)

    def option_daily_row(
        self,
        underlying: UnderlyingConfig,
        contract_row: tuple[Any, ...],
        market_date: date,
        spot_close: Decimal,
        run_id: str,
    ) -> tuple[Any, ...] | None:
        """Return the public quote row, retaining the legacy generator API."""

        result = self.option_daily_result(
            underlying, contract_row, market_date, spot_close, run_id
        )
        return result.quote_row if result is not None else None

    def option_daily_result(
        self,
        underlying: UnderlyingConfig,
        contract_row: tuple[Any, ...],
        market_date: date,
        spot_close: Decimal,
        run_id: str,
    ) -> OptionDailyResult | None:
        """Price one frozen contract from the realized underlying spot.

        ``strike`` and the remaining static terms are unpacked from the contract
        master row.  The method receives neither ``UnderlyingSimulationConfig``
        nor its correlation matrix: P-measure dependence affects this quote only
        indirectly through the realized ``spot_close`` supplied by the pipeline.

        QuantLib's analytic BSM NPV is canonicalized as both ``mid`` and
        settlement price. Optional deterministic noise independently multiplies
        the bid and ask half-spreads; it cannot perturb the model value.
        """

        (
            _, option_identifier, underlying_id, _, call_put, strike, expiry,
            exercise_style, settlement_type, multiplier, *_lineage,
        ) = contract_row
        # Expiry-day settlement is outside the current daily quote contract.
        # Returning None makes absence after expiry explicit rather than a zero
        # or stale quote, and the pipeline quality gate uses the same predicate.
        if market_date >= expiry:
            return None

        evaluation_date = ql_date(market_date)
        expiry_date = ql_date(expiry)
        ql.Settings.instance().evaluationDate = evaluation_date
        maturity = self.day_count.yearFraction(evaluation_date, expiry_date)
        pricing_volatility = self._pricing_volatility(
            underlying, market_date, expiry, spot_close, strike, maturity
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
                evaluation_date, self.calendar, pricing_volatility, self.day_count
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
        # Model value is canonicalized before microstructure is added.  This mid
        # remains the BSM price used by parity/bounds checks and IV tasks.
        theoretical_price = float(option.NPV())
        # Analytic engines can return a tiny negative floating-point artifact for
        # a mathematically zero deep-OTM value. The observable option price obeys
        # the non-negative payoff bound before decimal canonicalization.
        mid = quantize_price(max(0.0, theoretical_price))
        half_spread = max(
            Decimal(str(self.config.quote_model["minimum_half_spread"])),
            mid * Decimal(str(self.config.quote_model["relative_half_spread"])),
        )
        bid_half_spread = half_spread * Decimal(
            str(self._half_spread_multiplier("bid", option_identifier, market_date))
        )
        ask_half_spread = half_spread * Decimal(
            str(self._half_spread_multiplier("ask", option_identifier, market_date))
        )
        # Side-specific noise widens/narrows only executable quotes.  Flooring
        # bid at zero and keeping both spreads non-negative preserves
        # bid <= BSM mid <= ask by construction.
        bid = quantize_price(max(Decimal("0"), mid - bid_half_spread))
        ask = quantize_price(mid + ask_half_spread)
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
        quote_row = (*logical, run_id)
        pricing_audit_row = None
        q_pricing = self.config.q_pricing
        if q_pricing is not None:
            solver = q_pricing.implied_volatility_solver
            try:
                derived_implied_volatility = float(
                    option.impliedVolatility(
                        float(mid),
                        process,
                        solver.accuracy,
                        solver.max_evaluations,
                        solver.minimum_volatility,
                        solver.maximum_volatility,
                    )
                )
                iv_status = "CONVERGED"
                iv_error = None
            except RuntimeError as error:
                derived_implied_volatility = None
                iv_status = "NO_FINITE_IV"
                iv_error = str(error)
            pricing_audit_row = (
                self.config.snapshot_id,
                market_date,
                option_identifier,
                q_pricing.risk_neutral_measure_id,
                q_pricing.numeraire_id,
                q_pricing.rate_path_id,
                q_pricing.measure_change,
                q_pricing.volatility_mapping,
                pricing_volatility,
                theoretical_price,
                mid,
                derived_implied_volatility,
                iv_status,
                iv_error,
                canonical_json(solver.as_dict()),
                run_id,
            )
        return OptionDailyResult(
            quote_row=quote_row,
            pricing_audit_row=pricing_audit_row,
        )

    def _pricing_volatility(
        self,
        underlying: UnderlyingConfig,
        market_date: date,
        expiry: date,
        spot_close: Decimal,
        strike: Decimal,
        maturity: float,
    ) -> float:
        """Return the declared Q diffusion over one option's remaining life."""

        if self.config.q_pricing is not None:
            start_offset = float((market_date - self.config.start_date).days)
            end_offset = float((expiry - self.config.start_date).days)
            return underlying.physical_volatility_function.interval_root_mean_square(
                start_offset, end_offset
            )

        smile = self.config.smile
        base_implied_volatility = underlying.base_implied_volatility
        if smile is None or base_implied_volatility is None:
            raise ValueError("legacy option pricing requires latent smile inputs")
        forward = float(spot_close) * math.exp(
            (underlying.risk_free_rate - underlying.dividend_yield) * maturity
        )
        log_moneyness = math.log(float(strike) / forward)
        return max(
            smile["minimum_volatility"],
            base_implied_volatility
            + smile["skew"] * log_moneyness
            + smile["curvature"] * log_moneyness * log_moneyness
            + smile["term_slope"] * maturity,
        )

    def _half_spread_multiplier(
        self, side: str, option_identifier: str, market_date: date
    ) -> float:
        """Return one clipped, side-specific and replayable noise multiplier."""

        noise = self.config.bid_ask_noise
        if noise is None:
            return 1.0
        raw_multiplier = 1.0 + noise.standard_deviation * self._gaussian(
            noise.stream_namespace,
            side,
            option_identifier,
            market_date,
        )
        return min(
            noise.maximum_multiplier,
            max(noise.minimum_multiplier, raw_multiplier),
        )
