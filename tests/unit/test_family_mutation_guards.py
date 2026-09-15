from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from synthetic_derivatives.model_families import (
    ExecutableCapabilityRegistry,
    ModelFamilyRegistry,
)
from synthetic_derivatives.mutation import (
    FamilyLineage,
    FamilyMutatedTask,
    FamilyMutationEngine,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    get_metric_spec,
    get_metric_spec_db_query_v3,
    metric_task_spec_v3,
)
from synthetic_derivatives.task_space import TaskSpaceRegistry


def _engine(repository_root: Path) -> FamilyMutationEngine:
    catalog = TaskSpaceRegistry.from_path(
        repository_root / "configs/task_space/derivatives_v2.json"
    )
    families = ModelFamilyRegistry.from_directory(
        repository_root / "configs/model_families"
    )
    capabilities = ExecutableCapabilityRegistry.from_path(
        repository_root / "configs/task_space/executable_capabilities_v1.json",
        model_families=families,
        design_catalog=catalog,
    )
    return FamilyMutationEngine.from_path(
        repository_root / "configs/mutations/tdgbm_bsm_deterministic_v1.json",
        catalog,
        capabilities,
    )


def _static_delta():
    return metric_task_spec_v3(
        get_metric_spec("delta"),
        task_id="bsm-market-delta-v1-parent",
        snapshot_id="DERIVATIVES-METALS-LIQUID-BSM-v1",
        snapshot_revision=1,
    )


def test_ordinary_family_mutation_rejects_model_family_and_m_changes(
    repository_root: Path,
) -> None:
    engine = _engine(repository_root)
    parent = _static_delta()

    with pytest.raises(ValueError, match="cannot change model_family_id"):
        engine.mutate(
            parent,
            operator_id="semantic_counterfactual",
            changes={},
            seed=7,
            model_family_id="heston",
        )
    with pytest.raises(ValueError, match="cannot change model coordinate M"):
        engine.mutate(
            parent,
            operator_id="late_stage_multi_axis",
            changes={"M": 5},
            seed=7,
        )


def test_interface_and_output_change_produce_deterministic_capability_backed_child(
    repository_root: Path,
) -> None:
    engine = _engine(repository_root)
    parent = _static_delta()
    query_spec = get_metric_spec_db_query_v3("delta")

    first = engine.mutate(
        parent,
        operator_id="semantic_counterfactual",
        changes={},
        seed=20260817,
        solver_interface_id="read-only-duckdb-query-schema-submit-v3",
        output_contract_id=query_spec.submission_schema_version,
    )
    replay = engine.mutate(
        parent,
        operator_id="semantic_counterfactual",
        changes={},
        seed=20260817,
        solver_interface_id="read-only-duckdb-query-schema-submit-v3",
        output_contract_id=query_spec.submission_schema_version,
    )

    assert first == replay
    assert isinstance(first, FamilyMutatedTask)
    assert isinstance(first.lineage, FamilyLineage)
    assert first.task.task_id.startswith(parent.task_id + "_fm-")
    assert first.task.solver_interface_id.endswith("-v3")
    assert first.task.output_contract_id.endswith("-v2.0.0")
    assert first.task.coordinates.M == parent.coordinates.M == 0
    assert first.lineage.before_identity == parent.identity_dict()
    assert first.lineage.after_identity == first.task.identity_dict()
    assert first.lineage.capability_registry_id == (
        "synthetic-derivatives-executable-capabilities-v1"
    )


def test_kind_method_output_and_snapshot_all_enter_child_identity(
    repository_root: Path,
) -> None:
    engine = _engine(repository_root)
    parent = _static_delta()
    gamma = get_metric_spec("gamma")
    changed_kind = engine.mutate(
        parent,
        operator_id="semantic_counterfactual",
        changes={},
        seed=11,
        task_kind_id="gamma",
        method_id=gamma.method_id,
        output_contract_id=gamma.submission_schema_version,
    )
    changed_snapshot = engine.mutate(
        parent,
        operator_id="semantic_counterfactual",
        changes={},
        seed=11,
        snapshot_id="DERIVATIVES-METALS-LIQUID-BSM-child",
        snapshot_revision=2,
    )

    assert changed_kind.task.task_kind_id == "gamma"
    assert changed_kind.task.task_id != changed_snapshot.task.task_id
    assert changed_snapshot.lineage.after_identity["snapshot_revision"] == 2
    assert changed_kind.lineage.after_identity["method_id"] == gamma.method_id


def test_identity_conflict_fails_closed_in_capability_gate(
    repository_root: Path,
) -> None:
    engine = _engine(repository_root)

    with pytest.raises(ValueError, match="OUTPUT_CONTRACT_MISMATCH"):
        engine.mutate(
            _static_delta(),
            operator_id="semantic_counterfactual",
            changes={},
            seed=3,
            output_contract_id="invented-output-v1",
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"A": 2}, "new method_id"),
        ({"D": 5}, "new solver_interface_id"),
        ({"R": 2}, "new output_contract_id"),
    ],
)
def test_coordinate_semantics_require_new_owning_identity(
    repository_root: Path,
    changes: dict[str, int],
    message: str,
) -> None:
    engine = _engine(repository_root)

    with pytest.raises(ValueError, match=message):
        engine.mutate(
            _static_delta(),
            operator_id="raise_single_axis",
            changes=changes,
            seed=5,
        )


def test_family_mutation_config_freezes_family_and_removes_m_from_all_operators(
    repository_root: Path,
) -> None:
    legacy_path = repository_root / "configs/mutations/deterministic_v2.json"
    before = legacy_path.read_bytes()
    family = json.loads(
        (
            repository_root
            / "configs/mutations/tdgbm_bsm_deterministic_v1.json"
        ).read_text()
    )

    assert family["model_family_id"] == "tdgbm_bsm"
    assert family["immutable_fields"] == ["model_family_id", "coordinates.M"]
    assert all("M" not in item["allowed_axes"] for item in family["operators"])
    assert legacy_path.read_bytes() == before


def test_family_mutation_output_validates_against_its_new_schema(
    repository_root: Path,
) -> None:
    engine = _engine(repository_root)
    query_spec = get_metric_spec_db_query_v3("delta")
    child = engine.mutate(
        _static_delta(),
        operator_id="semantic_counterfactual",
        changes={},
        seed=19,
        solver_interface_id="read-only-duckdb-query-schema-submit-v3",
        output_contract_id=query_spec.submission_schema_version,
    )
    schemas = {
        name: json.loads((repository_root / "schemas" / name).read_text())
        for name in (
            "family-mutation-v1.schema.json",
            "task-v3.schema.json",
            "difficulty-v2.schema.json",
        )
    }
    schema_registry = Registry().with_resources(
        (name, Resource.from_contents(schema))
        for name, schema in schemas.items()
    )

    Draft202012Validator.check_schema(schemas["family-mutation-v1.schema.json"])
    Draft202012Validator(
        schemas["family-mutation-v1.schema.json"], registry=schema_registry
    ).validate(child.to_dict())


def test_family_mutation_loader_rejects_operator_that_reintroduces_m(
    repository_root: Path,
) -> None:
    raw = json.loads(
        (
            repository_root
            / "configs/mutations/tdgbm_bsm_deterministic_v1.json"
        ).read_text()
    )
    changed = deepcopy(raw)
    changed["operators"][0]["allowed_axes"].append("M")
    engine = _engine(repository_root)

    with pytest.raises(ValueError, match="cannot allow M"):
        FamilyMutationEngine(changed, engine.registry, engine.capabilities)
