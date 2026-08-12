from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.package import (
    AgentTaskPackage,
    build_bsm_greeks_package,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.parent import joint_parent_config


@dataclass(frozen=True)
class PackagedGreeksFixture:
    repository_root: Path
    parent_database: Path
    package: AgentTaskPackage


@pytest.fixture(scope="session")
def packaged_bsm_greeks(
    tmp_path_factory: pytest.TempPathFactory,
) -> PackagedGreeksFixture:
    repository = Path(__file__).resolve().parents[2]
    root = tmp_path_factory.mktemp("bsm-greeks-package-e2e")
    base = json.loads(
        (
            repository
            / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json"
        ).read_text(encoding="utf-8")
    )
    raw = joint_parent_config(base)
    raw.update(
        {
            "generator_config_id": "quantlib-bsm-greeks-package-test-v1",
            "snapshot_id": "DERIVATIVES-BSM-GREEKS-PACKAGE-TEST-v1",
            "business_days": 2,
        }
    )
    config_path = root / "parent.json"
    config_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    config = load_generator_config(config_path)
    parent = root / "parent.duckdb"
    with AuthoringPipeline(parent, config) as pipeline:
        created = pipeline.create_smoke_snapshot()
        frozen = pipeline.freeze()
    assert created["summary"]["underlying_count"] == 22
    assert frozen["status"] == "FROZEN"
    package = build_bsm_greeks_package(
        repository_root=repository,
        parent_database=parent,
        output_root=root / "packages",
        package_config_path=(
            repository
            / "configs/task_packages/bsm_market_implied_greeks_v1.json"
        ),
    )
    return PackagedGreeksFixture(repository, parent, package)
