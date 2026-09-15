"""Stable leaf-verifier templates and repository-side metric verification."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    DUCKDB_QUERY_V3_PROFILE,
    METRIC_LEAF_VERIFIER_FILENAMES,
    STATIC_V2_PROFILE,
    expected_market_metric_submission as _expected_market_metric_submission,
    load_bsm_market_metric_inputs,
    oracle_config_for_metric,
    render_metric_leaf_verifier_files,
    write_metric_leaf_verifier_bundle,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    submission_duckdb_v3,
    submission_static_v2,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    MetricSpec,
    get_metric_spec,
    get_metric_spec_db_query_v3,
)


# This historical public name remains the packaging API, but it is deliberately
# the same tuple object as the renderer inventory.  Nested leaf paths therefore
# cannot drift between rendering, manifest binding, and suite verification.
METRIC_VERIFIER_FILENAMES = METRIC_LEAF_VERIFIER_FILENAMES


def _canonical_spec(spec: MetricSpec | str) -> MetricSpec:
    candidate = get_metric_spec(spec) if isinstance(spec, str) else spec
    if not isinstance(candidate, MetricSpec):
        raise TypeError("spec must be a MetricSpec or supported target string")
    canonical = get_metric_spec(candidate.target)
    if candidate != canonical:
        raise ValueError("metric spec differs from the frozen registry")
    return canonical


def _canonical_spec_v3(spec: MetricSpec | str) -> MetricSpec:
    candidate = (
        get_metric_spec_db_query_v3(spec) if isinstance(spec, str) else spec
    )
    if not isinstance(candidate, MetricSpec):
        raise TypeError("spec must be a MetricSpec or supported target string")
    canonical = get_metric_spec_db_query_v3(candidate.target)
    if candidate != canonical:
        raise ValueError("metric spec differs from the frozen v3 registry")
    return canonical


def metric_oracle_config(spec: MetricSpec | str) -> dict[str, Any]:
    """Return the exact allowlisted oracle config for one target."""

    canonical = _canonical_spec(spec)
    config = oracle_config_for_metric(STATIC_V2_PROFILE, canonical.target)
    registry_binding = {
        "output_field": canonical.output_field,
        "variant_id": canonical.variant_id,
        "method_id": canonical.method_id,
        "submission_schema_version": canonical.submission_schema_version,
        "verifier_id": canonical.verifier_id,
        "database_schema_version": canonical.database_schema_version,
        "task_version": canonical.task_version,
        "task_id_pattern": canonical.task_id_pattern,
        "decimal_constraint": canonical.decimal_constraint,
        "needs_iv_status": canonical.needs_iv_status,
    }
    if any(config[field] != value for field, value in registry_binding.items()):
        raise ValueError("metric registry and self-contained verifier drifted")
    return config


def metric_oracle_config_v3(spec: MetricSpec | str) -> dict[str, Any]:
    """Return the exact DuckDB-query v3 oracle config for one target."""

    canonical = _canonical_spec_v3(spec)
    config = oracle_config_for_metric(DUCKDB_QUERY_V3_PROFILE, canonical.target)
    registry_binding = {
        "output_field": canonical.output_field,
        "variant_id": canonical.variant_id,
        "method_id": canonical.method_id,
        "submission_schema_version": canonical.submission_schema_version,
        "verifier_id": canonical.verifier_id,
        "database_schema_version": canonical.database_schema_version,
        "task_version": canonical.task_version,
        "task_id_pattern": canonical.task_id_pattern,
        "decimal_constraint": canonical.decimal_constraint,
        "needs_iv_status": canonical.needs_iv_status,
    }
    if any(config[field] != value for field, value in registry_binding.items()):
        raise ValueError("v3 metric registry and self-contained verifier drifted")
    return config


def _coerce_config(
    config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> dict[str, Any]:
    if isinstance(config_or_spec, Mapping):
        config = dict(config_or_spec)
        target = config.get("target")
        if not isinstance(target, str) or config != metric_oracle_config(target):
            raise ValueError("oracle config differs from the frozen target config")
        return config
    return metric_oracle_config(config_or_spec)


def _coerce_config_v3(
    config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> dict[str, Any]:
    if isinstance(config_or_spec, Mapping):
        config = dict(config_or_spec)
        target = config.get("target")
        if not isinstance(target, str) or config != metric_oracle_config_v3(target):
            raise ValueError("oracle config differs from the frozen v3 target config")
        return config
    return metric_oracle_config_v3(config_or_spec)


def metric_verifier_files(spec: MetricSpec | str) -> dict[str, bytes]:
    """Render the exact modular static-v2 package-local verifier bundle."""

    canonical = _canonical_spec(spec)
    metric_oracle_config(canonical)
    return render_metric_leaf_verifier_files(STATIC_V2_PROFILE, canonical.target)


def metric_verifier_files_v3(spec: MetricSpec | str) -> dict[str, bytes]:
    """Render the exact modular order-insensitive v3 verifier bundle."""

    canonical = _canonical_spec_v3(spec)
    metric_oracle_config_v3(canonical)
    return render_metric_leaf_verifier_files(
        DUCKDB_QUERY_V3_PROFILE, canonical.target
    )


def write_metric_verifier(directory: str | Path, spec: MetricSpec | str) -> None:
    """Write one modular static-v2 package-local verifier."""

    canonical = _canonical_spec(spec)
    metric_oracle_config(canonical)
    write_metric_leaf_verifier_bundle(
        directory, STATIC_V2_PROFILE, canonical.target
    )


def write_metric_verifier_v3(directory: str | Path, spec: MetricSpec | str) -> None:
    """Write one modular DuckDB-query-v3 package-local verifier."""

    canonical = _canonical_spec_v3(spec)
    metric_oracle_config_v3(canonical)
    write_metric_leaf_verifier_bundle(
        directory, DUCKDB_QUERY_V3_PROFILE, canonical.target
    )


def _database_path(task_root_or_database: str | Path) -> Path:
    path = Path(task_root_or_database)
    return path / "task.duckdb" if path.is_dir() else path


def expected_metric_submission(
    task_root_or_database: str | Path,
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> dict[str, Any]:
    """Recompute canonical truth directly from one derived public database."""

    config = _coerce_config(oracle_config_or_spec)
    inputs = load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    return _expected_market_metric_submission(inputs, config)


def expected_metric_submission_v3(
    task_root_or_database: str | Path,
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> dict[str, Any]:
    """Recompute canonical v3 truth from one derived public database."""

    config = _coerce_config_v3(oracle_config_or_spec)
    inputs = load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    return _expected_market_metric_submission(inputs, config)


def verify_market_metric_submission(
    task_root_or_database: str | Path,
    submission: Mapping[str, Any],
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> None:
    """Recompute from a derived DB and require exact complete-submission equality."""

    config = _coerce_config(oracle_config_or_spec)
    inputs = load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    submission_static_v2.verify_market_metric_submission(
        inputs, submission, config
    )


def verify_market_metric_submission_v3(
    task_root_or_database: str | Path,
    submission: Mapping[str, Any],
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> None:
    """Key-align v3 rows and require exact canonical-string equality."""

    config = _coerce_config_v3(oracle_config_or_spec)
    inputs = load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    submission_duckdb_v3.verify_market_metric_submission(
        inputs, submission, config
    )


__all__ = [
    "METRIC_VERIFIER_FILENAMES",
    "expected_metric_submission",
    "expected_metric_submission_v3",
    "metric_oracle_config",
    "metric_oracle_config_v3",
    "metric_verifier_files",
    "metric_verifier_files_v3",
    "verify_market_metric_submission",
    "verify_market_metric_submission_v3",
    "write_metric_verifier",
    "write_metric_verifier_v3",
]
