from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from synthetic_derivatives.authoring import AuthoringPipeline
from synthetic_derivatives.authoring.backends import (
    AuthoringBackendRegistry,
    TDGBMBSMAuthoringBackend,
    TDGBM_BSM_AUTHORING_BACKEND_ID,
)
from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.option_daily_generator import OptionDailyGenerator
from synthetic_derivatives.authoring.underlying_daily_generator import (
    UnderlyingDailyGenerator,
)


def test_current_backend_is_explicit_and_constructs_existing_generators(
    smoke_config_path: Path,
) -> None:
    config = load_generator_config(smoke_config_path)
    backend = TDGBMBSMAuthoringBackend()
    registry = AuthoringBackendRegistry((backend,))

    resolved = registry.resolve("tdgbm_bsm")

    assert registry.model_family_ids == ("tdgbm_bsm",)
    assert resolved.backend_id == TDGBM_BSM_AUTHORING_BACKEND_ID
    assert isinstance(resolved.create_underlying_generator(config), UnderlyingDailyGenerator)
    assert isinstance(resolved.create_option_generator(config), OptionDailyGenerator)


def test_backend_registry_rejects_duplicate_and_unknown_family() -> None:
    backend = TDGBMBSMAuthoringBackend()

    with pytest.raises(ValueError, match="model_family_id values must be unique"):
        AuthoringBackendRegistry((backend, backend))
    with pytest.raises(ValueError, match="unknown or unimplemented"):
        AuthoringBackendRegistry((backend,)).resolve("heston")


def test_unknown_family_fails_before_database_or_parent_directory_is_created(
    tmp_path: Path,
    smoke_config_path: Path,
) -> None:
    config = load_generator_config(smoke_config_path)
    target = tmp_path / "must-not-exist" / "unknown.duckdb"

    with pytest.raises(ValueError, match="unknown or unimplemented"):
        AuthoringPipeline(target, config, model_family_id="heston")

    assert not target.parent.exists()


def test_backend_rechecks_runtime_identity_on_dataclass_bypass(
    smoke_config_path: Path,
) -> None:
    config = load_generator_config(smoke_config_path)
    changed = replace(config, pricing_model="DifferentModel")

    with pytest.raises(ValueError, match="pricing_model"):
        TDGBMBSMAuthoringBackend().validate_config(changed)
