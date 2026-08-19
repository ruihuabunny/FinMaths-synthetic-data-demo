"""Closed capability policy for every supported generator-config identity."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


LEGACY_RNG = (
    "QuantLib.BoxMullerMersenneTwisterGaussianRng/"
    "partitioned-sha256-seed-v1"
)
DEPENDENCE_RNG = (
    "QuantLib.BoxMullerMersenneTwisterGaussianRng/"
    "underlying-factor-idiosyncratic-sha256-v1"
)
SEMANTIC_KEYED_RNG = (
    "QuantLib.BoxMullerMersenneTwisterGaussianRng/"
    "semantic-keyed-authoring-streams-sha256-v2"
)


@dataclass(frozen=True)
class ConfigVersionPolicy:
    option_input: Literal["templates", "chain"]
    require_liquidity_and_noise: bool
    pricing_contract: Literal["legacy_smile", "common_q"]
    dependence_contract: Literal["none", "p_only", "p_q"]
    observation_contract: Literal["legacy", "bridge_volume"]
    expected_rng: str
    allow_deterministic_functions: bool = True
    allow_legacy_authoring_iv_solver: bool = False


CONFIG_VERSION_POLICIES: Mapping[str, ConfigVersionPolicy] = MappingProxyType(
    {
        "1.0.0": ConfigVersionPolicy(
            "templates", False, "legacy_smile", "none", "legacy", LEGACY_RNG,
            allow_deterministic_functions=False,
        ),
        "1.1.0": ConfigVersionPolicy(
            "templates", False, "legacy_smile", "none", "legacy", LEGACY_RNG
        ),
        "1.2.0": ConfigVersionPolicy(
            "templates", False, "legacy_smile", "p_only", "legacy", DEPENDENCE_RNG
        ),
        "1.3.0": ConfigVersionPolicy(
            "chain", False, "legacy_smile", "p_only", "legacy", DEPENDENCE_RNG
        ),
        "1.4.0": ConfigVersionPolicy(
            "chain", True, "legacy_smile", "p_only", "legacy", DEPENDENCE_RNG
        ),
        "1.5.0": ConfigVersionPolicy(
            "chain", True, "common_q", "p_only", "legacy", DEPENDENCE_RNG,
            allow_legacy_authoring_iv_solver=True,
        ),
        "1.6.0": ConfigVersionPolicy(
            "chain", True, "common_q", "p_only", "legacy", DEPENDENCE_RNG
        ),
        "1.7.0": ConfigVersionPolicy(
            "chain", True, "common_q", "p_q", "legacy", DEPENDENCE_RNG
        ),
        "1.8.0": ConfigVersionPolicy(
            "chain", True, "common_q", "p_q", "bridge_volume", SEMANTIC_KEYED_RNG
        ),
    }
)


def config_version_policy(schema_version: object) -> ConfigVersionPolicy:
    """Resolve one explicitly registered version or fail closed."""

    if not isinstance(schema_version, str):
        raise ValueError("unsupported generator config schema_version")
    try:
        return CONFIG_VERSION_POLICIES[schema_version]
    except KeyError as error:
        raise ValueError("unsupported generator config schema_version") from error
