from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from synthetic_derivatives.authoring.config import (
    load_generator_config,
    parse_generator_config,
)
from synthetic_derivatives.authoring.config_versions import (
    CONFIG_VERSION_POLICIES,
    DEPENDENCE_RNG,
    LEGACY_RNG,
    SEMANTIC_KEYED_RNG,
    config_version_policy,
)


def test_supported_versions_have_one_closed_capability_row_each() -> None:
    assert tuple(CONFIG_VERSION_POLICIES) == (
        "1.0.0",
        "1.1.0",
        "1.2.0",
        "1.3.0",
        "1.4.0",
        "1.5.0",
        "1.6.0",
        "1.7.0",
        "1.8.0",
    )
    assert [
        CONFIG_VERSION_POLICIES[version].expected_rng
        for version in CONFIG_VERSION_POLICIES
    ] == [
        LEGACY_RNG,
        LEGACY_RNG,
        DEPENDENCE_RNG,
        DEPENDENCE_RNG,
        DEPENDENCE_RNG,
        DEPENDENCE_RNG,
        DEPENDENCE_RNG,
        DEPENDENCE_RNG,
        SEMANTIC_KEYED_RNG,
    ]
    assert CONFIG_VERSION_POLICIES["1.0.0"].option_input == "templates"
    assert CONFIG_VERSION_POLICIES["1.3.0"].option_input == "chain"
    assert CONFIG_VERSION_POLICIES["1.7.0"].dependence_contract == "p_q"
    assert (
        CONFIG_VERSION_POLICIES["1.8.0"].observation_contract
        == "bridge_volume"
    )


@pytest.mark.parametrize("version", ["0.9.0", "1.9.0", "2.0.0", None])
def test_unknown_config_versions_fail_closed(version: object) -> None:
    with pytest.raises(ValueError, match="unsupported generator config schema_version"):
        config_version_policy(version)


@pytest.mark.parametrize(
    "relative_path",
    [
        "configs/generators/quantlib_bsm_smoke_v1.json",
        "authoring/templates/quantlib_bsm_generator.template.json",
        "authoring/templates/quantlib_bsm_correlated_underlyings.template.json",
        "configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json",
        "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json",
        "authoring/templates/quantlib_bsm_brownian_bridge_volume.template.json",
        "configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json",
    ],
)
def test_pure_parser_matches_file_loader(
    repository_root: Path, relative_path: str
) -> None:
    path = repository_root / relative_path
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert parse_generator_config(MappingProxyType(raw)) == load_generator_config(path)
