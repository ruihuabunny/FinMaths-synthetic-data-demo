from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from synthetic_derivatives.model_families import (
    ModelFamilyRegistry,
    ModelFamilySpec,
)
from synthetic_derivatives.task_space import TaskCoordinates


def _family(repository_root: Path) -> ModelFamilySpec:
    registry = ModelFamilyRegistry.from_directory(
        repository_root / "configs/model_families"
    )
    return registry.require("tdgbm_bsm")


def test_tdgbm_bsm_is_the_only_declared_implemented_family(
    repository_root: Path,
) -> None:
    registry = ModelFamilyRegistry.from_directory(
        repository_root / "configs/model_families"
    )
    family = registry.require("tdgbm_bsm")

    assert registry.model_family_ids == ("tdgbm_bsm",)
    assert family.underlying_dynamics_id == (
        "deterministic_time_inhomogeneous_gbm_p_v1"
    )
    assert family.pricing_model_id == "european_bsm_q_v1"
    assert family.measure_mapping_id == (
        "girsanov_drift_only_same_diffusion_v1"
    )
    assert family.model_coordinate == 0
    assert family.stochastic_identity.physical_measure_id == "physical-history-p-v1"
    assert family.stochastic_identity.pricing_measure_id == (
        "USD-MONEY-MARKET-Q-v1"
    )
    assert family.stochastic_identity.numeraire_id == (
        "USD-MONEY-MARKET-ACCOUNT-v1"
    )


def test_model_family_config_validates_against_closed_schema(
    repository_root: Path,
) -> None:
    schema = json.loads(
        (repository_root / "schemas/model-family-v1.schema.json").read_text()
    )
    config = json.loads(
        (
            repository_root / "configs/model_families/tdgbm_bsm_v1.json"
        ).read_text()
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(config)
    assert ModelFamilySpec.from_mapping(config).to_dict() == config
    assert all("import" not in key for key in config)


def test_family_registry_rejects_unknown_duplicate_and_m_mismatch(
    repository_root: Path,
) -> None:
    family = _family(repository_root)
    registry = ModelFamilyRegistry((family,))

    with pytest.raises(ValueError, match="unknown or unimplemented"):
        registry.require("heston")
    with pytest.raises(ValueError, match="must be unique"):
        ModelFamilyRegistry((family, family))
    with pytest.raises(ValueError, match="must be unique"):
        ModelFamilyRegistry(
            (family, replace(family, model_family_id="same_coordinate"))
        )
    with pytest.raises(ValueError, match="requires M=0"):
        registry.require_matching_coordinates(
            "tdgbm_bsm", TaskCoordinates(5, 0, 5, 1, 4, 1, "F0")
        )


def test_model_family_document_rejects_dynamic_backend_paths(
    repository_root: Path,
) -> None:
    raw = json.loads(
        (
            repository_root / "configs/model_families/tdgbm_bsm_v1.json"
        ).read_text()
    )
    raw["python_backend"] = "package.module:Backend"

    with pytest.raises(ValueError, match=r"extra=\['python_backend'\]"):
        ModelFamilySpec.from_mapping(raw)
