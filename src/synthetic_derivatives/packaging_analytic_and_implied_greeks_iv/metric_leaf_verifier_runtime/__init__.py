"""Source modules and renderer for modular BSM metric leaf verifiers.

These exports preserve the shared function signatures, expose typed profiles,
and render production leaf bundles with one protocol selected before writing;
the generated verifier performs no runtime protocol dispatch.
"""

from .database import (
    bsm_metric_logical_checksum,
    digest_file,
    load_bsm_market_metric_inputs,
)
from .models import BSMMarketMetricInput
from .oracle import expected_market_metric_submission
from .profiles import (
    DUCKDB_QUERY_V3_PROFILE,
    RUNTIME_PROFILES,
    STATIC_V2_PROFILE,
    MetricSpec,
    RuntimeProfile,
    get_runtime_profile,
    oracle_config_for_metric,
    validate_render_profile,
)
from .renderer import (
    METRIC_LEAF_VERIFIER_FILENAMES,
    render_metric_leaf_verifier_files,
    write_metric_leaf_verifier_bundle,
)


__all__ = [
    "BSMMarketMetricInput",
    "DUCKDB_QUERY_V3_PROFILE",
    "MetricSpec",
    "METRIC_LEAF_VERIFIER_FILENAMES",
    "RUNTIME_PROFILES",
    "RuntimeProfile",
    "STATIC_V2_PROFILE",
    "bsm_metric_logical_checksum",
    "digest_file",
    "expected_market_metric_submission",
    "get_runtime_profile",
    "load_bsm_market_metric_inputs",
    "oracle_config_for_metric",
    "render_metric_leaf_verifier_files",
    "validate_render_profile",
    "write_metric_leaf_verifier_bundle",
]
