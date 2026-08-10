"""Shared task contracts without solver or verifier numerical implementations."""

from synthetic_derivatives.tasks.bsm_implied_volatility import (
    BSMImpliedVolatilityContract,
    BSMImpliedVolatilityInput,
    CanonicalBSMImpliedVolatilityResult,
    bsm_iv_contract,
)
from synthetic_derivatives.tasks.bsm_greeks import (
    BSM_ANALYTIC_GREEKS_METHOD_ID,
    BSM_ANALYTIC_GREEKS_VARIANT_ID,
    BSM_GREEKS_CONVENTION_ID,
    BSM_GREEKS_OUTPUT_CONTRACT_ID,
    BSMGreeksInput,
    BSMGreeksValues,
    CanonicalBSMGreeksResult,
    analytic_bsm_greeks_contract,
    canonicalize_bsm_greeks,
)
from synthetic_derivatives.tasks.bsm_market_greeks import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_VARIANT_ID,
    BSMMarketGreeksInput,
    CanonicalMarketGreeksRow,
    MarketGreeksSubmission,
)

__all__ = [
    "BSMImpliedVolatilityContract",
    "BSMImpliedVolatilityInput",
    "CanonicalBSMImpliedVolatilityResult",
    "bsm_iv_contract",
    "BSM_ANALYTIC_GREEKS_METHOD_ID",
    "BSM_ANALYTIC_GREEKS_VARIANT_ID",
    "BSM_GREEKS_CONVENTION_ID",
    "BSM_GREEKS_OUTPUT_CONTRACT_ID",
    "BSMGreeksInput",
    "BSMGreeksValues",
    "CanonicalBSMGreeksResult",
    "analytic_bsm_greeks_contract",
    "canonicalize_bsm_greeks",
    "BSM_MARKET_GREEKS_METHOD_ID",
    "BSM_MARKET_GREEKS_VARIANT_ID",
    "BSMMarketGreeksInput",
    "CanonicalMarketGreeksRow",
    "MarketGreeksSubmission",
]
