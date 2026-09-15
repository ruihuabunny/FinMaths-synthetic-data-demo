from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from synthetic_derivatives.authoring.backends import (
    TDGBM_BSM_AUTHORING_BACKEND_ID,
)
from synthetic_derivatives.model_families import (
    CapabilityBinding,
    CapabilityKey,
    ExecutableCapabilityRegistry,
    ModelFamilyRegistry,
)
from synthetic_derivatives.task_space import TaskSpaceRegistry


def _raw(repository_root: Path) -> dict:
    return json.loads(
        (
            repository_root
            / "configs/task_space/executable_capabilities_v1.json"
        ).read_text()
    )


def _dependencies(repository_root: Path):
    return (
        ModelFamilyRegistry.from_directory(
            repository_root / "configs/model_families"
        ),
        TaskSpaceRegistry.from_path(
            repository_root / "configs/task_space/derivatives_v2.json"
        ),
    )


def _registry(repository_root: Path) -> ExecutableCapabilityRegistry:
    families, catalog = _dependencies(repository_root)
    return ExecutableCapabilityRegistry(
        _raw(repository_root),
        model_families=families,
        design_catalog=catalog,
    )


def test_capability_sidecar_is_closed_and_maps_only_current_evidence(
    repository_root: Path,
) -> None:
    raw = _raw(repository_root)
    schema = json.loads(
        (
            repository_root / "schemas/executable-capability-v1.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(raw)

    registry = _registry(repository_root)
    statuses = [capability.status for capability in registry.capabilities]
    assert len(statuses) == 15
    assert statuses.count("library_implemented") == 2
    assert statuses.count("portable_verified") == 13
    assert {item.key.model_family_id for item in registry.capabilities} == {
        "tdgbm_bsm"
    }
    assert not any(
        token in item.key.task_family_id
        for item in registry.capabilities
        for token in ("heston", "local_vol", "monte_carlo")
    )
    assert all(
        item.evidence.authoring_backend_id == TDGBM_BSM_AUTHORING_BACKEND_ID
        for item in registry.capabilities
    )
    assert all(
        (repository_root / test_id).is_file()
        for item in registry.capabilities
        for test_id in item.evidence.test_ids
    )


def test_static_and_query_v3_are_distinct_exact_capabilities(
    repository_root: Path,
) -> None:
    registry = _registry(repository_root)
    common = dict(
        model_family_id="tdgbm_bsm",
        task_family_id="market_implied_metric",
        task_kind_id="delta",
        method_id="bsm-mid-iv-bisection80-analytic-delta-v1",
    )
    static = registry.get(
        CapabilityKey(
            **common,
            solver_interface_id="static-json-query-schema-submit-v2",
        )
    )
    query = registry.get(
        CapabilityKey(
            **common,
            solver_interface_id="read-only-duckdb-query-schema-submit-v3",
        )
    )

    assert static is not None and query is not None
    assert static.output_contract_id.endswith("-v1.0.0")
    assert query.output_contract_id.endswith("-v2.0.0")
    assert static.evidence.runtime_contract_id != query.evidence.runtime_contract_id


def test_portable_gate_rejects_missing_key_output_conflict_and_library_status(
    repository_root: Path,
) -> None:
    registry = _registry(repository_root)
    missing = CapabilityBinding(
        CapabilityKey(
            "tdgbm_bsm",
            "monte_carlo",
            "european_price",
            "tdgbm-bsm-mc-v1",
            "python-library-mc-v1",
        ),
        "mc-output-v1",
    )
    assert registry.evaluate_binding(missing).code == "CAPABILITY_NOT_REGISTERED"

    analytic = registry.require_legacy_identity(
        "variant:bsm_analytic_greeks_v1"
    )
    library_decision = registry.evaluate_binding(analytic.binding)
    assert library_decision.code == "CAPABILITY_STATUS_DENIED"

    wrong_output = CapabilityBinding(
        registry.require_legacy_identity(
            "delivery:20260814_metric_6x4_db_query_v3:delta"
        ).key,
        "wrong-output-v1",
    )
    assert registry.evaluate_binding(wrong_output).code == "OUTPUT_CONTRACT_MISMATCH"


def test_registry_rejects_duplicate_keys_unknown_family_and_missing_evidence(
    repository_root: Path,
) -> None:
    families, catalog = _dependencies(repository_root)
    raw = _raw(repository_root)

    duplicate = deepcopy(raw)
    duplicate["capabilities"].append(deepcopy(duplicate["capabilities"][0]))
    with pytest.raises(ValueError, match="keys must be unique"):
        ExecutableCapabilityRegistry(
            duplicate, model_families=families, design_catalog=catalog
        )

    unknown = deepcopy(raw)
    unknown["capabilities"][0]["model_family_id"] = "heston"
    with pytest.raises(ValueError, match="unknown or unimplemented"):
        ExecutableCapabilityRegistry(
            unknown, model_families=families, design_catalog=catalog
        )

    missing = deepcopy(raw)
    missing["capabilities"][2]["evidence"]["runtime_contract_id"] = None
    with pytest.raises(ValueError, match="lacks required evidence"):
        ExecutableCapabilityRegistry(
            missing, model_families=families, design_catalog=catalog
        )
