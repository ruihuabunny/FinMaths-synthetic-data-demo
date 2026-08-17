from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest

from synthetic_derivatives.mutation import (
    Lineage,
    MutatedTask,
    MutationEngine,
    MutationLineage as PublicMutationLineage,
    Operator,
)
from synthetic_derivatives.mutation.engine import MutationLineage, MutationOperator
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


def test_engine_preserves_legacy_model_imports() -> None:
    assert PublicMutationLineage is Lineage
    assert MutationLineage is Lineage
    assert MutationOperator is Operator


def test_legacy_model_pickle_paths_resolve() -> None:
    assert (
        pickle.loads(
            b"csynthetic_derivatives.mutation.engine\nMutationLineage\n."
        )
        is Lineage
    )
    assert (
        pickle.loads(
            b"csynthetic_derivatives.mutation.engine\nMutationOperator\n."
        )
        is Operator
    )


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
    assert first.task.snapshot_revision == parent.snapshot_revision
    assert first.lineage.before == parent.coordinates.to_dict()
    assert first.lineage.before["F"] == first.lineage.after["F"] == "F0"
    assert "-F0-" in first.task.task_id
    assert first.lineage.engine_id == engine.engine_id
    assert isinstance(engine.operators["raise_single_axis"], Operator)
    assert isinstance(first, MutatedTask)
    assert isinstance(first.lineage, Lineage)


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


def test_f_axis_is_serialized_but_not_mutable_without_a_declared_operator(
    task_space_config_path: Path,
    mutation_config_path: Path,
    base_task_manifest_path: Path,
) -> None:
    engine, parent = _load(
        task_space_config_path, mutation_config_path, base_task_manifest_path
    )

    with pytest.raises(ValueError, match=r"cannot change axes \['F'\]"):
        engine.mutate(
            parent,
            operator_id="single_axis_counterfactual",
            changes={"F": "F1"},
            seed=20260805,
        )


def test_child_identity_changes_with_seed_and_contains_all_seven_axes(
    task_space_config_path: Path,
    mutation_config_path: Path,
    base_task_manifest_path: Path,
) -> None:
    engine, parent = _load(
        task_space_config_path, mutation_config_path, base_task_manifest_path
    )

    first = engine.mutate(
        parent,
        operator_id="raise_single_axis",
        changes={"L": 1},
        seed=7,
    )
    replay = engine.mutate(
        parent,
        operator_id="raise_single_axis",
        changes={"L": 1},
        seed=7,
    )
    different_seed = engine.mutate(
        parent,
        operator_id="raise_single_axis",
        changes={"L": 1},
        seed=8,
    )

    assert first.task.task_id == replay.task.task_id
    assert first.task.task_id != different_seed.task.task_id
    assert "L1-P0-M0-A0-D0-R0-F0" in first.task.task_id
    assert first.lineage.to_dict()["after"] == first.task.coordinates.to_dict()
