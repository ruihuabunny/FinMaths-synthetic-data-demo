"""Deterministic agent-task packaging contracts and materialization."""

from synthetic_derivatives.packaging.contracts import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_VARIANT_ID,
    PACKAGE_SCHEMA_VERSION,
    canonical_json_bytes,
    digest_json,
    market_greeks_method_contract,
)
from synthetic_derivatives.packaging.runtime import (
    CapabilityViolation,
    audit_solver_source,
    compose_runtime_contract,
)

__all__ = [
    "BSM_MARKET_GREEKS_METHOD_ID",
    "BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION",
    "BSM_MARKET_GREEKS_VARIANT_ID",
    "CapabilityViolation",
    "PACKAGE_SCHEMA_VERSION",
    "audit_solver_source",
    "canonical_json_bytes",
    "compose_runtime_contract",
    "digest_json",
    "market_greeks_method_contract",
]
