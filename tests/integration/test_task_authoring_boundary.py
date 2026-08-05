from __future__ import annotations

import json
from pathlib import Path

from synthetic_derivatives.task_space import TaskSpaceRegistry, TaskSpec


def test_base_task_references_authoring_snapshot_without_copying_oracle_data(
    repository_root: Path,
    task_space_config_path: Path,
    base_task_manifest_path: Path,
) -> None:
    raw_task = json.loads(base_task_manifest_path.read_text(encoding="utf-8"))
    task = TaskSpec.from_mapping(raw_task)
    snapshot_manifest_path = repository_root / raw_task["snapshot_manifest"]
    snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))

    assert task.snapshot_id == snapshot_manifest["snapshot_id"]
    assert task.snapshot_revision == snapshot_manifest["revision"]
    assert raw_task["status"] == snapshot_manifest["status"] == "DRAFT"
    assert raw_task["publication_eligible"] is False
    assert "oracle" not in raw_task
    assert "reference_answer" not in raw_task
    assert TaskSpaceRegistry.from_path(task_space_config_path).validate_task(task).compatible
