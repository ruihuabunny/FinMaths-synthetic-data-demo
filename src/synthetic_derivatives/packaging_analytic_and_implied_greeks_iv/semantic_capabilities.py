"""Capability adapters used before new BSM package materialization."""

from __future__ import annotations

from pathlib import Path

from synthetic_derivatives.model_families import (
    ExecutableCapability,
    ExecutableCapabilityRegistry,
    ModelFamilyRegistry,
    get_legacy_variant_identity,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS,
    METRIC_SPECS_DB_QUERY_V3,
    metric_capability_binding,
)
from synthetic_derivatives.task_space import TaskSpaceRegistry


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def load_current_capability_registry(
    repository_root: str | Path = _REPOSITORY_ROOT,
) -> ExecutableCapabilityRegistry:
    """Load the checked-in family, design catalog, and executable sidecar."""

    root = Path(repository_root)
    families = ModelFamilyRegistry.from_directory(root / "configs/model_families")
    catalog = TaskSpaceRegistry.from_path(
        root / "configs/task_space/derivatives_v2.json"
    )
    return ExecutableCapabilityRegistry.from_path(
        root / "configs/task_space/executable_capabilities_v1.json",
        model_families=families,
        design_catalog=catalog,
    )


def require_portable_metric_capabilities(
    *,
    query_v3: bool,
    registry: ExecutableCapabilityRegistry | None = None,
) -> tuple[ExecutableCapability, ...]:
    """Fail before filesystem writes unless source and six targets are portable."""

    capabilities = registry or load_current_capability_registry()
    source = capabilities.require_binding(
        get_legacy_variant_identity("bsm_market_implied_greeks_v1").binding
    )
    source_alias = capabilities.require_legacy_identity(
        "delivery:20260813_prompt_v2_100"
    )
    if source_alias.key != source.key:
        raise ValueError("source delivery adapter conflicts with bundle capability")
    specs = METRIC_SPECS_DB_QUERY_V3 if query_v3 else METRIC_SPECS
    delivery_id = (
        "20260814_metric_6x4_db_query_v3"
        if query_v3
        else "20260814_metric_6x4_unique_db"
    )
    admitted = [source]
    for spec in specs:
        capability = capabilities.require_binding(metric_capability_binding(spec))
        alias = capabilities.require_legacy_identity(
            f"delivery:{delivery_id}:{spec.target}"
        )
        if alias.key != capability.key:
            raise ValueError(
                f"delivery adapter conflicts with {spec.target!r} capability"
            )
        admitted.append(capability)
    return tuple(admitted)


__all__ = [
    "load_current_capability_registry",
    "require_portable_metric_capabilities",
]
