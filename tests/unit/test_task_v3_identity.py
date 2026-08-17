from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from synthetic_derivatives.model_families import (
    ModelFamilyRegistry,
    adapt_legacy_variant_config,
)
from synthetic_derivatives.task_space import (
    TaskCoordinates,
    TaskSpec,
    TaskSpecV3,
)


def _semantic_task() -> TaskSpecV3:
    return TaskSpecV3(
        task_id="tdgbm-bsm-market-delta-001",
        model_family_id="tdgbm_bsm",
        task_family_id="market_implied_metric",
        task_kind_id="delta",
        solver_interface_id="read-only-duckdb-query-schema-submit-v3",
        coordinates=TaskCoordinates(5, 0, 0, 1, 4, 1, "F0"),
        snapshot_id="DERIVATIVES-METALS-LIQUID-BSM-v1",
        snapshot_revision=1,
        method_id="bsm-mid-iv-bisection80-analytic-delta-v1",
        output_contract_id="bsm-market-implied-delta-submission-v2.0.0",
    )


def test_task_v3_round_trip_keeps_four_orthogonal_identities() -> None:
    task = _semantic_task()

    assert TaskSpecV3.from_mapping(task.to_dict()) == task
    assert task.capability_identity == (
        "tdgbm_bsm",
        "market_implied_metric",
        "delta",
        "bsm-mid-iv-bisection80-analytic-delta-v1",
        "read-only-duckdb-query-schema-submit-v3",
    )
    assert set(task.identity_dict()) >= {
        "model_family_id",
        "task_family_id",
        "task_kind_id",
        "solver_interface_id",
        "method_id",
        "output_contract_id",
    }


def test_task_v3_schema_is_closed_and_explicitly_distinct_from_package_v3(
    repository_root: Path,
) -> None:
    schema = json.loads(
        (repository_root / "schemas/task-v3.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert "independent" in schema["$comment"]
    assert set(schema["required"]) == set(_semantic_task().to_dict())
    assert schema["properties"]["coordinates"]["$ref"] == (
        "difficulty-v2.schema.json"
    )


def test_task_v3_never_defaults_new_semantic_identities() -> None:
    raw = _semantic_task().to_dict()
    raw.pop("solver_interface_id")

    with pytest.raises(ValueError, match="solver_interface_id"):
        TaskSpecV3.from_mapping(raw)


def test_legacy_task_requires_an_explicit_variant_adapter(
    repository_root: Path,
) -> None:
    raw_variant = json.loads(
        (
            repository_root / "configs/variants/bsm_analytic_greeks_v1.json"
        ).read_text()
    )
    adapter = adapt_legacy_variant_config(raw_variant)
    legacy = TaskSpec(
        task_id="legacy-analytic-task",
        task_family_id="bsm_greeks",
        coordinates=adapter.coordinates,
        snapshot_id="DIRECT-BSM-INPUT-v1",
        snapshot_revision=1,
        method_id=adapter.method_id,
        output_contract_id=adapter.output_contract_id,
    )

    semantic = adapter.adapt_task(legacy)

    assert legacy.to_dict() == {
        "task_id": "legacy-analytic-task",
        "task_family_id": "bsm_greeks",
        "coordinates": adapter.coordinates.to_dict(),
        "snapshot_id": "DIRECT-BSM-INPUT-v1",
        "snapshot_revision": 1,
        "method_id": adapter.method_id,
        "output_contract_id": adapter.output_contract_id,
    }
    assert semantic.model_family_id == "tdgbm_bsm"
    assert semantic.task_family_id == "analytic_greeks"
    assert semantic.task_kind_id == "core_greeks"


def test_task_v3_family_coordinate_is_validated_by_family_registry(
    repository_root: Path,
) -> None:
    registry = ModelFamilyRegistry.from_directory(
        repository_root / "configs/model_families"
    )
    mismatched = TaskSpecV3.from_mapping(
        {
            **_semantic_task().to_dict(),
            "coordinates": {
                **_semantic_task().coordinates.to_dict(),
                "M": 5,
            },
        }
    )

    with pytest.raises(ValueError, match="requires M=0"):
        registry.require_matching_coordinates(
            mismatched.model_family_id, mismatched.coordinates
        )
