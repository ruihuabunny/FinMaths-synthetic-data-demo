from pathlib import Path

import pytest


@pytest.fixture
def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def smoke_config_path(repository_root: Path) -> Path:
    return repository_root / "configs/generators/quantlib_bsm_smoke_v1.json"


@pytest.fixture
def task_space_config_path(repository_root: Path) -> Path:
    return repository_root / "configs/task_space/derivatives_v2.json"


@pytest.fixture
def mutation_config_path(repository_root: Path) -> Path:
    return repository_root / "configs/mutations/deterministic_v2.json"


@pytest.fixture
def curriculum_config_path(repository_root: Path) -> Path:
    return repository_root / "configs/curricula/adaptive_v2.json"


@pytest.fixture
def base_task_manifest_path(repository_root: Path) -> Path:
    return (
        repository_root
        / "datasets/manifests/tasks/quantlib_bsm_smoke_price_v2.json"
    )
