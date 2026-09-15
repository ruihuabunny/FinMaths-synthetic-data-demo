from __future__ import annotations

import json
from pathlib import Path

from synthetic_derivatives.curriculum import FamilyAwareCurriculumScheduler
from synthetic_derivatives.model_families import (
    ExecutableCapabilityRegistry,
    ModelFamilyRegistry,
    get_legacy_variant_identity,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    get_metric_spec,
    get_metric_spec_db_query_v3,
    metric_task_spec_v3,
)
from synthetic_derivatives.task_space import (
    TaskCoordinates,
    TaskSpaceRegistry,
    TaskSpecV3,
)


def _capabilities(repository_root: Path) -> ExecutableCapabilityRegistry:
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


def _scheduler(repository_root: Path) -> FamilyAwareCurriculumScheduler:
    return FamilyAwareCurriculumScheduler.from_path(
        repository_root / "configs/curricula/tdgbm_bsm_portable_v1.json",
        _capabilities(repository_root),
    )


def _bundle(task_id: str) -> TaskSpecV3:
    binding = get_legacy_variant_identity("bsm_market_implied_greeks_v1").binding
    return TaskSpecV3(
        task_id=task_id,
        model_family_id=binding.key.model_family_id,
        task_family_id=binding.key.task_family_id,
        task_kind_id=binding.key.task_kind_id,
        solver_interface_id=binding.key.solver_interface_id,
        coordinates=TaskCoordinates(5, 0, 0, 1, 4, 1, "F0"),
        snapshot_id="DERIVATIVES-METALS-LIQUID-BSM-v1",
        snapshot_revision=1,
        method_id=binding.key.method_id,
        output_contract_id=binding.output_contract_id,
    )


def _metric(task_id: str, target: str, *, query_v3: bool) -> TaskSpecV3:
    spec = (
        get_metric_spec_db_query_v3(target)
        if query_v3
        else get_metric_spec(target)
    )
    return metric_task_spec_v3(
        spec,
        task_id=task_id,
        snapshot_id="DERIVATIVES-METALS-LIQUID-BSM-v1",
        snapshot_revision=1,
    )


def test_family_local_stage_mapping_is_explicit(repository_root: Path) -> None:
    scheduler = _scheduler(repository_root)

    assert scheduler.stage_for(_bundle("bundle")).stage_id == (
        "market_implied_bundle_static"
    )
    assert scheduler.stage_for(_metric("static", "delta", query_v3=False)).stage_id == (
        "market_implied_metrics_static"
    )
    assert scheduler.stage_for(_metric("query", "delta", query_v3=True)).stage_id == (
        "market_implied_metrics_duckdb_query"
    )


def test_family_scheduler_gates_before_mapping_and_rejects_scaffolds(
    repository_root: Path,
) -> None:
    scheduler = _scheduler(repository_root)
    analytic_binding = get_legacy_variant_identity("bsm_analytic_greeks_v1").binding
    library_only = TaskSpecV3(
        task_id="library-only",
        model_family_id=analytic_binding.key.model_family_id,
        task_family_id=analytic_binding.key.task_family_id,
        task_kind_id=analytic_binding.key.task_kind_id,
        solver_interface_id=analytic_binding.key.solver_interface_id,
        coordinates=TaskCoordinates(1, 0, 0, 0, 1, 1, "F0"),
        snapshot_id="DIRECT",
        snapshot_revision=1,
        method_id=analytic_binding.key.method_id,
        output_contract_id=analytic_binding.output_contract_id,
    )
    mc_scaffold = TaskSpecV3(
        task_id="mc-scaffold",
        model_family_id="tdgbm_bsm",
        task_family_id="monte_carlo",
        task_kind_id="european_price",
        solver_interface_id="mc-scaffold-v1",
        coordinates=TaskCoordinates(3, 0, 0, 6, 2, 0, "F0"),
        snapshot_id="CATALOG",
        snapshot_revision=1,
        method_id="bsm-mc-scaffold-v1",
        output_contract_id="price-v1",
    )
    portable = _metric("portable", "delta", query_v3=False)

    pool = scheduler.construct_task_pool((library_only, mc_scaffold, portable))

    assert pool.tasks == (portable,)
    assert [item.decision.code for item in pool.decisions] == [
        "CAPABILITY_STATUS_DENIED",
        "CAPABILITY_NOT_REGISTERED",
        "CAPABILITY_ADMITTED",
    ]


def test_family_scheduler_preserves_20_60_20_and_binary_mastery_actions(
    repository_root: Path,
) -> None:
    scheduler = _scheduler(repository_root)
    tasks = (
        _bundle("replay"),
        _metric("current-core", "delta", query_v3=False),
        _metric("current-mastered", "gamma", query_v3=False),
        _metric("explore", "delta", query_v3=True),
    )

    weights = scheduler.sampling_weights(
        tasks,
        current_stage=1,
        pass_at_1={
            "replay": 0.99,
            "current-core": 0.5,
            "current-mastered": 0.99,
            "explore": 0.1,
        },
    )

    assert weights == {
        "replay": 0.2,
        "current-core": 0.48,
        "current-mastered": 0.12,
        "explore": 0.2,
    }
    assert scheduler.mastery_band(0.04).action == "decompose_or_sft"
    assert scheduler.mastery_band(0.5).action == "core_rl"
    assert scheduler.mastery_band(0.99).action == "replay_only"


def test_family_config_reuses_legacy_mixture_without_selecting_planned_models(
    repository_root: Path,
) -> None:
    legacy = json.loads(
        (repository_root / "configs/curricula/adaptive_v2.json").read_text()
    )
    family = json.loads(
        (
            repository_root / "configs/curricula/tdgbm_bsm_portable_v1.json"
        ).read_text()
    )

    assert family["mixture"] == legacy["mixture"] == {
        "replay": 0.2,
        "current": 0.6,
        "explore": 0.2,
    }
    assert family["mastery_bands"] == legacy["mastery_bands"]
    assert family["model_family_id"] == "tdgbm_bsm"
    assert "M" not in json.dumps(family["stages"], sort_keys=True)
    assert all(
        token not in json.dumps(family)
        for token in ("heston", "local_vol", "monte_carlo")
    )
