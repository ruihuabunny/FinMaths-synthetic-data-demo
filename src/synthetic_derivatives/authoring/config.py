"""Stable compatibility façade for versioned authoring configuration.

Definitions live in focused internal modules. Existing imports from
``synthetic_derivatives.authoring.config`` remain supported by this façade.
"""

from synthetic_derivatives.authoring.canonicalization import quantize_to_increment
from synthetic_derivatives.authoring.config_loader import (
    load_generator_config,
    parse_generator_config,
)
from synthetic_derivatives.authoring.config_models import (
    GeneratorConfig,
    QPricingConfig,
    UnderlyingConfig,
)
from synthetic_derivatives.authoring.dependence import UnderlyingDependenceConfig
from synthetic_derivatives.authoring.deterministic_functions import (
    DeterministicFunction,
    DeterministicFunctionNode,
)
from synthetic_derivatives.authoring.observation_contracts import (
    IntradayBridgeConfig,
    VolumeModelConfig,
)
from synthetic_derivatives.authoring.option_chain import (
    BidAskNoiseConfig,
    OptionChainBuilder,
    OptionChainConfig,
    OptionLiquidityFilter,
    OptionTemplate,
    option_id,
)

__all__ = [
    "BidAskNoiseConfig",
    "DeterministicFunction",
    "DeterministicFunctionNode",
    "GeneratorConfig",
    "IntradayBridgeConfig",
    "OptionChainBuilder",
    "OptionChainConfig",
    "OptionLiquidityFilter",
    "OptionTemplate",
    "QPricingConfig",
    "UnderlyingConfig",
    "UnderlyingDependenceConfig",
    "VolumeModelConfig",
    "load_generator_config",
    "option_id",
    "parse_generator_config",
    "quantize_to_increment",
]
