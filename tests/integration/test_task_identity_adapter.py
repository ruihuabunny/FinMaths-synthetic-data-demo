from __future__ import annotations

import json
from pathlib import Path

from synthetic_derivatives.model_families import (
    EXECUTABLE_CAPABILITY_STATUSES,
    ExecutableCapabilityRegistry,
    ModelFamilyRegistry,
    adapt_legacy_variant_config,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS,
    METRIC_SPECS_DB_QUERY_V3,
    metric_capability_binding,
)
from synthetic_derivatives.task_space import TaskSpaceRegistry


def _registry(repository_root: Path) -> ExecutableCapabilityRegistry:
    families = ModelFamilyRegistry.from_directory(
        repository_root / "configs/model_families"
    )
    catalog = TaskSpaceRegistry.from_path(
        repository_root / "configs/task_space/derivatives_v2.json"
    )
    return ExecutableCapabilityRegistry.from_path(
        repository_root / "configs/task_space/executable_capabilities_v1.json",
        model_families=families,
        design_catalog=catalog,
    )


def test_current_three_variants_map_without_rewriting_configs(
    repository_root: Path,
) -> None:
    registry = _registry(repository_root)
    paths = (
        repository_root / "configs/variants/bsm_analytic_greeks_v1.json",
        repository_root / "configs/variants/bsm_iv_scalar_v1.json",
        repository_root / "configs/variants/bsm_market_implied_greeks_v1.json",
    )
    before = {path: path.read_bytes() for path in paths}

    mapped = [
        adapt_legacy_variant_config(json.loads(path.read_text())) for path in paths
    ]

    assert [
        registry.require_binding(
            item.binding,
            required_statuses=EXECUTABLE_CAPABILITY_STATUSES,
        ).status
        for item in mapped
    ] == [
        "library_implemented",
        "library_implemented",
        "portable_verified",
    ]
    assert all(path.read_bytes() == before[path] for path in paths)


def test_metric_spec_is_the_single_source_for_both_interfaces(
    repository_root: Path,
) -> None:
    registry = _registry(repository_root)
    static = [metric_capability_binding(spec) for spec in METRIC_SPECS]
    query = [
        metric_capability_binding(spec) for spec in METRIC_SPECS_DB_QUERY_V3
    ]

    assert {item.key.task_kind_id for item in static} == {
        "iv",
        "delta",
        "gamma",
        "vega_1volpt",
        "theta_1calendar_day",
        "rho_1pct",
    }
    assert all(
        registry.require_binding(item).status == "portable_verified"
        for item in static + query
    )
    assert all(
        left.key.method_id == right.key.method_id
        and left.key.solver_interface_id != right.key.solver_interface_id
        and left.output_contract_id != right.output_contract_id
        for left, right in zip(static, query, strict=True)
    )


def test_three_accepted_delivery_baselines_have_sidecar_aliases(
    repository_root: Path,
) -> None:
    registry = _registry(repository_root)

    assert registry.require_legacy_identity(
        "delivery:20260813_prompt_v2_100"
    ).status == "portable_verified"
    for target in (
        "iv",
        "delta",
        "gamma",
        "vega_1volpt",
        "theta_1calendar_day",
        "rho_1pct",
    ):
        assert registry.require_legacy_identity(
            f"delivery:20260814_metric_6x4_unique_db:{target}"
        ).status == "portable_verified"
        assert registry.require_legacy_identity(
            f"delivery:20260814_metric_6x4_db_query_v3:{target}"
        ).status == "portable_verified"
