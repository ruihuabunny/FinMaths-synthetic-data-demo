from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthetic_derivatives.mutation import MutationEngine
from synthetic_derivatives.task_space import TaskSpaceRegistry, TaskSpec


def _load(
    task_space_config_path: Path,
    mutation_config_path: Path,
    base_task_manifest_path: Path,
) -> tuple[MutationEngine, TaskSpec]:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)
    engine = MutationEngine.from_path(mutation_config_path, registry)
    task = TaskSpec.from_mapping(
        json.loads(base_task_manifest_path.read_text(encoding="utf-8"))
    )
    return engine, task


def test_single_axis_mutation_is_deterministic_and_keeps_snapshot(
    task_space_config_path: Path,
    mutation_config_path: Path,
    base_task_manifest_path: Path,
) -> None:
    engine, parent = _load(
        task_space_config_path, mutation_config_path, base_task_manifest_path
    )

    first = engine.mutate(
        parent, operator_id="raise_single_axis", changes={"L": 1}, seed=20260805
    )
    second = engine.mutate(
        parent, operator_id="raise_single_axis", changes={"L": 1}, seed=20260805
    )

    assert first == second
    assert parent.coordinates.L == 0
    assert first.task.coordinates.L == 1
    assert first.task.snapshot_id == parent.snapshot_id
    assert first.task.snapshot_hash == parent.snapshot_hash
    assert first.lineage.before == parent.coordinates.to_dict()
    assert first.lineage.logical_hash == first.task.logical_hash
    assert first.lineage.parent_hash == parent.logical_hash


def test_mutation_rejects_incompatible_child(
    task_space_config_path: Path,
    mutation_config_path: Path,
    base_task_manifest_path: Path,
) -> None:
    engine, parent = _load(
        task_space_config_path, mutation_config_path, base_task_manifest_path
    )

    with pytest.raises(ValueError, match="incompatible task coordinates"):
        engine.mutate(
            parent,
            operator_id="raise_single_axis",
            changes={"M": 7},
            seed=20260805,
        )


def test_method_axis_mutation_requires_new_method_identity(
    task_space_config_path: Path,
    mutation_config_path: Path,
    base_task_manifest_path: Path,
) -> None:
    engine, parent = _load(
        task_space_config_path, mutation_config_path, base_task_manifest_path
    )

    with pytest.raises(ValueError, match="explicit method_id"):
        engine.mutate(
            parent,
            operator_id="raise_single_axis",
            changes={"A": 1},
            seed=20260805,
        )
