"""Explicit identities for currently implemented derivative model families."""

from synthetic_derivatives.model_families.adapters import (
    ANALYTIC_GREEKS_LIBRARY_INTERFACE_ID,
    DUCKDB_QUERY_V3_SOLVER_INTERFACE_ID,
    SCALAR_IV_LIBRARY_INTERFACE_ID,
    STATIC_JSON_SOLVER_INTERFACE_ID,
    TDGBM_BSM_MODEL_FAMILY_ID,
    LegacyVariantIdentity,
    adapt_legacy_variant_config,
    get_legacy_variant_identity,
)
from synthetic_derivatives.model_families.capabilities import (
    EXECUTABLE_CAPABILITY_STATUSES,
    PORTABLE_CAPABILITY_STATUSES,
    CapabilityBinding,
    CapabilityDecision,
    CapabilityKey,
    ExecutableCapability,
    ExecutableCapabilityRegistry,
    GatedTaskPool,
)
from synthetic_derivatives.model_families.contracts import (
    ModelFamilySpec,
    StochasticIdentity,
)
from synthetic_derivatives.model_families.registry import ModelFamilyRegistry

__all__ = [
    "ANALYTIC_GREEKS_LIBRARY_INTERFACE_ID",
    "CapabilityBinding",
    "CapabilityDecision",
    "CapabilityKey",
    "DUCKDB_QUERY_V3_SOLVER_INTERFACE_ID",
    "EXECUTABLE_CAPABILITY_STATUSES",
    "ExecutableCapability",
    "ExecutableCapabilityRegistry",
    "GatedTaskPool",
    "LegacyVariantIdentity",
    "ModelFamilyRegistry",
    "ModelFamilySpec",
    "PORTABLE_CAPABILITY_STATUSES",
    "SCALAR_IV_LIBRARY_INTERFACE_ID",
    "STATIC_JSON_SOLVER_INTERFACE_ID",
    "StochasticIdentity",
    "TDGBM_BSM_MODEL_FAMILY_ID",
    "adapt_legacy_variant_config",
    "get_legacy_variant_identity",
]
