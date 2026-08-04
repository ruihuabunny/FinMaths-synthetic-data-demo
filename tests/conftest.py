from pathlib import Path

import pytest


@pytest.fixture
def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def smoke_config_path(repository_root: Path) -> Path:
    return repository_root / "configs/generators/quantlib_bsm_smoke_v1.json"
