"""Stable leaf-verifier templates and repository-side metric verification."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv import (
    bsm_market_metric_verifier_runtime as runtime,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv import (
    bsm_market_metric_verifier_runtime_v3 as runtime_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    MetricSpec,
    get_metric_spec,
    get_metric_spec_db_query_v3,
)


METRIC_VERIFIER_FILENAMES = (
    "README.md",
    "__init__.py",
    "conftest.py",
    "oracle_config.json",
    "requirements.lock",
    "runtime.py",
    "test_contract.py",
    "test_data_identity.py",
    "test_semantics.py",
)

_REQUIREMENTS_LOCK = """# Trusted evaluation image. This lock is never installed in the solver image.
duckdb==1.5.5
QuantLib==1.39
pytest==8.4.1
"""

_VERIFIER_SOURCES = {
    "__init__.py": '''"""Package-local trusted single-metric verifier."""
''',
    "conftest.py": '''from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))


@pytest.fixture
def package_root() -> Path:
    return PACKAGE_ROOT


@pytest.fixture
def oracle_config(package_root: Path) -> dict:
    return json.loads(
        (package_root / "verifier/oracle_config.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def agent_submission() -> dict:
    declared = os.environ.get("BSM_GREEKS_SUBMISSION")
    if not declared:
        raise RuntimeError("BSM_GREEKS_SUBMISSION must identify the agent file")
    return json.loads(Path(declared).read_text(encoding="utf-8"))
''',
    "test_contract.py": '''from verifier.runtime import validate_market_metric_submission_contract


def test_submission_contract(agent_submission, oracle_config):
    validate_market_metric_submission_contract(agent_submission, oracle_config)
''',
    "test_data_identity.py": '''import json

from verifier.runtime import bsm_metric_logical_checksum, digest_file


def test_public_data_identity(package_root, oracle_config):
    manifest = json.loads(
        (package_root / "delivery_manifest.json").read_text(encoding="utf-8")
    )
    database = package_root / "task.duckdb"
    assert bsm_metric_logical_checksum(database, oracle_config) == (
        manifest["public_child_snapshot"]["logical_checksum"]
    )
    assert digest_file(database) == manifest["artifacts"]["task.duckdb"]
''',
    "test_semantics.py": '''from verifier.runtime import (
    load_bsm_market_metric_inputs,
    verify_market_metric_submission,
)


def test_exact_market_metric(package_root, agent_submission, oracle_config):
    inputs = load_bsm_market_metric_inputs(
        package_root / "task.duckdb", oracle_config
    )
    verify_market_metric_submission(inputs, agent_submission, oracle_config)
''',
    "README.md": '''# Package-local trusted verifier

This directory is self-contained project code. It imports only the Python
standard library plus the pinned third-party packages in `requirements.lock`;
it does not require the `synthetic_derivatives` source tree or wheel.

From the leaf task root, verify one submission with:

```bash
python -m pip install -r verifier/requirements.lock
BSM_GREEKS_SUBMISSION=/absolute/path/submission.json \\
  python -B -m pytest -q verifier
```

The verifier reconstructs the one declared IV/Greek result from `task.duckdb`
and `verifier/oracle_config.json`. It does not read a packaged reference answer.
''',
}


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
    internal = runtime._METRIC_SPEC_BY_TARGET[canonical.target]
    config = runtime._oracle_config(internal)
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
    internal = runtime_v3._METRIC_SPEC_BY_TARGET[canonical.target]
    config = runtime_v3._oracle_config(internal)
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
    """Render the exact nine-file package-local verifier bundle."""

    config = metric_oracle_config(spec)
    runtime_source = Path(runtime.__file__).read_bytes()
    files = {
        name: source.encode("utf-8") for name, source in _VERIFIER_SOURCES.items()
    }
    files.update(
        {
            "oracle_config.json": canonical_json_bytes(config),
            "requirements.lock": _REQUIREMENTS_LOCK.encode("utf-8"),
            "runtime.py": runtime_source,
        }
    )
    if set(files) != set(METRIC_VERIFIER_FILENAMES):
        raise RuntimeError("metric verifier template file allowlist changed")
    return {name: files[name] for name in METRIC_VERIFIER_FILENAMES}


def metric_verifier_files_v3(spec: MetricSpec | str) -> dict[str, bytes]:
    """Render the standalone order-insensitive v3 verifier bundle."""

    config = metric_oracle_config_v3(spec)
    files = {
        name: source.encode("utf-8") for name, source in _VERIFIER_SOURCES.items()
    }
    files.update(
        {
            "oracle_config.json": canonical_json_bytes(config),
            "requirements.lock": _REQUIREMENTS_LOCK.encode("utf-8"),
            "runtime.py": runtime_v3.leaf_runtime_source().encode("utf-8"),
        }
    )
    if set(files) != set(METRIC_VERIFIER_FILENAMES):
        raise RuntimeError("v3 metric verifier template file allowlist changed")
    return {name: files[name] for name in METRIC_VERIFIER_FILENAMES}


def write_metric_verifier(directory: str | Path, spec: MetricSpec | str) -> None:
    """Write one new package-local verifier without source-string adaptation."""

    destination = Path(directory)
    if destination.exists() and (
        destination.is_symlink()
        or not destination.is_dir()
        or any(destination.iterdir())
    ):
        raise ValueError("metric verifier destination must be absent or empty")
    destination.mkdir(parents=True, exist_ok=True)
    for name, payload in metric_verifier_files(spec).items():
        (destination / name).write_bytes(payload)


def write_metric_verifier_v3(directory: str | Path, spec: MetricSpec | str) -> None:
    """Write one standalone DuckDB-query v3 leaf verifier."""

    destination = Path(directory)
    if destination.exists() and (
        destination.is_symlink()
        or not destination.is_dir()
        or any(destination.iterdir())
    ):
        raise ValueError("metric verifier destination must be absent or empty")
    destination.mkdir(parents=True, exist_ok=True)
    for name, payload in metric_verifier_files_v3(spec).items():
        (destination / name).write_bytes(payload)


def _database_path(task_root_or_database: str | Path) -> Path:
    path = Path(task_root_or_database)
    return path / "task.duckdb" if path.is_dir() else path


def expected_metric_submission(
    task_root_or_database: str | Path,
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> dict[str, Any]:
    """Recompute canonical truth directly from one derived public database."""

    config = _coerce_config(oracle_config_or_spec)
    inputs = runtime.load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    return runtime.expected_market_metric_submission(inputs, config)


def expected_metric_submission_v3(
    task_root_or_database: str | Path,
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> dict[str, Any]:
    """Recompute canonical v3 truth from one derived public database."""

    config = _coerce_config_v3(oracle_config_or_spec)
    inputs = runtime_v3.load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    return runtime_v3.expected_market_metric_submission(inputs, config)


def verify_market_metric_submission(
    task_root_or_database: str | Path,
    submission: Mapping[str, Any],
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> None:
    """Recompute from a derived DB and require exact complete-submission equality."""

    config = _coerce_config(oracle_config_or_spec)
    inputs = runtime.load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    runtime.verify_market_metric_submission(inputs, submission, config)


def verify_market_metric_submission_v3(
    task_root_or_database: str | Path,
    submission: Mapping[str, Any],
    oracle_config_or_spec: Mapping[str, Any] | MetricSpec | str,
) -> None:
    """Key-align v3 rows and require exact canonical-string equality."""

    config = _coerce_config_v3(oracle_config_or_spec)
    inputs = runtime_v3.load_bsm_market_metric_inputs(
        _database_path(task_root_or_database), config
    )
    runtime_v3.verify_market_metric_submission(inputs, submission, config)


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
