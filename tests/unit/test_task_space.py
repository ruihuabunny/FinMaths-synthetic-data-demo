from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthetic_derivatives.task_space import (
    TaskCoordinates,
    TaskSpaceRegistry,
    TaskSpec,
)


def test_checked_in_base_task_is_compatible(
    task_space_config_path: Path, base_task_manifest_path: Path
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)
    task = TaskSpec.from_mapping(
        json.loads(base_task_manifest_path.read_text(encoding="utf-8"))
    )

    decision = registry.validate_task(task)

    assert decision.compatible
    assert decision.rule_id == "bsm-vanilla"
    assert task.coordinates.to_dict() == {"L": 0, "P": 0, "M": 0, "A": 0, "D": 0, "R": 0}
    assert len(task.logical_hash) == 64


def test_registry_rejects_incompatible_product_model_method(
    task_space_config_path: Path,
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)

    decision = registry.evaluate(
        TaskCoordinates(L=0, P=6, M=0, A=1, D=0, R=0), "barrier"
    )

    assert not decision.compatible
    assert "no compatible" in decision.reason


def test_registry_rejects_coordinate_outside_documented_axis(
    task_space_config_path: Path,
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)

    with pytest.raises(ValueError, match="outside registry bounds"):
        registry.require_compatible(TaskCoordinates(L=7, P=0, M=0, A=0, D=0, R=0))
