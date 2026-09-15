"""Deterministic renderer for self-contained modular metric leaf verifiers."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from . import profiles as _profiles
from .profiles import (
    DUCKDB_QUERY_V3_PROFILE_ID,
    STATIC_V2_PROFILE_ID,
    RuntimeProfile,
    get_runtime_profile,
    oracle_config_for_metric,
    validate_render_profile,
)


# POSIX relative paths are part of the generated-artifact contract.  Packaging
# consumes this exact tuple for tree allowlists, digests, and visibility maps;
# directory entries are created from the paths and are never separate artifacts.
METRIC_LEAF_VERIFIER_FILENAMES = (
    "README.md",
    "__init__.py",
    "conftest.py",
    "oracle_config.json",
    "requirements.lock",
    "runtime.py",
    "_runtime/__init__.py",
    "_runtime/profile.py",
    "_runtime/models.py",
    "_runtime/database.py",
    "_runtime/oracle.py",
    "_runtime/submission.py",
    "test_contract.py",
    "test_data_identity.py",
    "test_semantics.py",
)

_SOURCE_DIRECTORY = Path(__file__).resolve().parent
_PROFILE_CONSTANT_BY_ID = {
    STATIC_V2_PROFILE_ID: "STATIC_V2_PROFILE",
    DUCKDB_QUERY_V3_PROFILE_ID: "DUCKDB_QUERY_V3_PROFILE",
}
_METRIC_SPEC_FIELDS = (
    "target",
    "output_field",
    "variant_id",
    "method_id",
    "submission_schema_version",
    "verifier_id",
    "task_id_pattern",
    "decimal_constraint",
    "needs_iv_status",
    "database_schema_version",
    "task_version",
)

_REQUIREMENTS_LOCK = """# Trusted evaluation image. This lock is never installed in the solver image.
duckdb==1.5.5
QuantLib==1.39
pytest==8.4.1
"""

_ROOT_SOURCES = {
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
    "test_contract.py": '''from .runtime import validate_market_metric_submission_contract


def test_submission_contract(agent_submission, oracle_config):
    validate_market_metric_submission_contract(agent_submission, oracle_config)
''',
    "test_data_identity.py": '''import json

from .runtime import bsm_metric_logical_checksum, digest_file


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
    "test_semantics.py": '''from .runtime import (
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

`runtime.py` is the stable public facade. The `_runtime/` package contains the
private database, input-model, independent-oracle, frozen-profile, and selected
submission-protocol modules. All implementation imports are package-relative,
and each generated leaf contains exactly one active submission protocol. This
intentional duplication keeps every task relocatable and independently
verifiable instead of depending on repository-side authoring code.

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

_FACADE_SOURCE = '''"""Stable public facade for the modular BSM metric verifier."""

from ._runtime.database import (
    bsm_metric_logical_checksum,
    digest_file,
    load_bsm_market_metric_inputs,
)
from ._runtime.models import BSMMarketMetricInput
from ._runtime.oracle import expected_market_metric_submission
from ._runtime.submission import (
    validate_market_metric_submission_contract,
    verify_market_metric_submission,
)


__all__ = [
    "BSMMarketMetricInput",
    "bsm_metric_logical_checksum",
    "digest_file",
    "expected_market_metric_submission",
    "load_bsm_market_metric_inputs",
    "validate_market_metric_submission_contract",
    "verify_market_metric_submission",
]
'''

_RUNTIME_INIT_SOURCE = '''"""Private implementation of one BSM metric verifier protocol."""
'''


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _coerce_profile(profile: RuntimeProfile | str) -> RuntimeProfile:
    if isinstance(profile, str):
        return get_runtime_profile(profile)
    return validate_render_profile(profile)


def _metric_specs_source(profile: RuntimeProfile) -> str:
    blocks = []
    for spec in profile.metric_specs:
        lines = ["    MetricSpec("]
        lines.extend(
            f"        {field}={getattr(spec, field)!r},"
            for field in _METRIC_SPEC_FIELDS
        )
        lines.append("    ),")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _render_profile_source(profile: RuntimeProfile) -> bytes:
    constant = _PROFILE_CONSTANT_BY_ID[profile.profile_id]
    specs = _metric_specs_source(profile)
    source = f'''"""Frozen identities for one rendered BSM metric protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_TASK_FAMILY = {_profiles._TASK_FAMILY!r}
_SELECTION_POLICY_ID = {_profiles._SELECTION_POLICY_ID!r}
_LOGICAL_CHECKSUM_ID = {_profiles._LOGICAL_CHECKSUM_ID!r}
_RISK_NEUTRAL_MEASURE_ID = {_profiles._RISK_NEUTRAL_MEASURE_ID!r}
_NUMERAIRE_ID = {_profiles._NUMERAIRE_ID!r}
_SUCCESS_IV_STATUS = {_profiles._SUCCESS_IV_STATUS!r}
_ORACLE_CONFIG_SCHEMA_VERSION = {_profiles._ORACLE_CONFIG_SCHEMA_VERSION!r}
_IV_METHOD_ID = {_profiles._IV_METHOD_ID!r}
_GREEKS_METHOD_ID = {_profiles._GREEKS_METHOD_ID!r}
_LOWER_VOLATILITY = {_profiles._LOWER_VOLATILITY!r}
_UPPER_VOLATILITY = {_profiles._UPPER_VOLATILITY!r}
_BISECTION_ITERATIONS = {_profiles._BISECTION_ITERATIONS!r}
METRIC_TARGET_ORDER = {tuple(spec.target for spec in profile.metric_specs)!r}


@dataclass(frozen=True, slots=True)
class MetricSpec:
    target: str
    output_field: str
    variant_id: str
    method_id: str
    submission_schema_version: str
    verifier_id: str
    task_id_pattern: str
    decimal_constraint: str
    needs_iv_status: bool
    database_schema_version: str
    task_version: str


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    profile_id: str
    submission_module: str
    database_schema_version: str
    task_version: str
    metric_specs: tuple[MetricSpec, ...]


_METRIC_SPECS = (
{specs}
)
{constant} = RuntimeProfile(
    profile_id={profile.profile_id!r},
    submission_module={profile.submission_module!r},
    database_schema_version={profile.database_schema_version!r},
    task_version={profile.task_version!r},
    metric_specs=_METRIC_SPECS,
)
_RUNTIME_PROFILE = {constant}


def _oracle_config(spec: MetricSpec) -> dict[str, Any]:
    return {{
        "oracle_config_schema_version": _ORACLE_CONFIG_SCHEMA_VERSION,
        "target": spec.target,
        "output_field": spec.output_field,
        "variant_id": spec.variant_id,
        "method_id": spec.method_id,
        "submission_schema_version": spec.submission_schema_version,
        "verifier_id": spec.verifier_id,
        "database_schema_version": spec.database_schema_version,
        "task_version": spec.task_version,
        "task_id_pattern": spec.task_id_pattern,
        "decimal_constraint": spec.decimal_constraint,
        "needs_iv_status": spec.needs_iv_status,
        "quantlib_version": "1.39",
        "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
        "iv_method_id": _IV_METHOD_ID,
        "greeks_method_id": _GREEKS_METHOD_ID,
        "pricing_measure_id": _RISK_NEUTRAL_MEASURE_ID,
        "numeraire_id": _NUMERAIRE_ID,
        "day_count": "Actual365Fixed",
        "rate_compounding": "continuous",
        "iterations": _BISECTION_ITERATIONS,
        "volatility_bracket": [_LOWER_VOLATILITY, _UPPER_VOLATILITY],
        "canonical_precision": 8,
        "canonical_rounding": "ROUND_HALF_EVEN",
    }}


def _spec_from_config(
    method_config: Mapping[str, Any],
    *,
    profile: RuntimeProfile | None = None,
) -> MetricSpec:
    if not isinstance(method_config, Mapping):
        raise ValueError("trusted verifier requires an oracle config object")
    if profile is not None and profile != _RUNTIME_PROFILE:
        raise ValueError("trusted verifier received a different oracle config")
    candidate = dict(method_config)
    target = candidate.get("target")
    spec = next(
        (
            item
            for item in _METRIC_SPECS
            if item.target == target and candidate == _oracle_config(item)
        ),
        None,
    )
    if spec is None:
        raise ValueError("trusted verifier received a different oracle config")
    return spec
'''
    return source.encode("utf-8")


def _render_implementation_source(filename: str) -> bytes:
    """Copy one authoring module and retarget its sole profile import locally."""

    source = (_SOURCE_DIRECTORY / filename).read_bytes()
    marker = b"from .profiles import"
    if source.count(marker) != 1:
        raise RuntimeError(f"runtime source import boundary changed: {filename}")
    return source.replace(marker, b"from .profile import", 1)


def render_metric_leaf_verifier_files(
    profile: RuntimeProfile | str, target: str
) -> dict[str, bytes]:
    """Render one complete, self-contained verifier tree without writing it."""

    selected = _coerce_profile(profile)
    config = oracle_config_for_metric(selected, target)
    files = {name: source.encode("utf-8") for name, source in _ROOT_SOURCES.items()}
    files.update(
        {
            "oracle_config.json": _canonical_json_bytes(config),
            "requirements.lock": _REQUIREMENTS_LOCK.encode("utf-8"),
            "runtime.py": _FACADE_SOURCE.encode("utf-8"),
            "_runtime/__init__.py": _RUNTIME_INIT_SOURCE.encode("utf-8"),
            "_runtime/profile.py": _render_profile_source(selected),
            "_runtime/models.py": _render_implementation_source("models.py"),
            "_runtime/database.py": _render_implementation_source("database.py"),
            "_runtime/oracle.py": _render_implementation_source("oracle.py"),
            "_runtime/submission.py": _render_implementation_source(
                f"{selected.submission_module}.py"
            ),
        }
    )
    if set(files) != set(METRIC_LEAF_VERIFIER_FILENAMES):
        raise RuntimeError("modular metric verifier file inventory changed")
    # Preserve the authoritative order so repeated renders and manifest maps are
    # byte-stable even though the implementation above is assembled as a dict.
    return {name: files[name] for name in METRIC_LEAF_VERIFIER_FILENAMES}


def write_metric_leaf_verifier_bundle(
    directory: str | Path,
    profile: RuntimeProfile | str,
    target: str,
) -> None:
    """Write one modular verifier into an absent or empty destination."""

    files = render_metric_leaf_verifier_files(profile, target)
    destination = Path(directory)
    if destination.exists() and (
        destination.is_symlink()
        or not destination.is_dir()
        or any(destination.iterdir())
    ):
        raise ValueError("metric verifier destination must be absent or empty")
    destination.mkdir(parents=True, exist_ok=True)
    for relative_path, payload in files.items():
        output = destination.joinpath(*PurePosixPath(relative_path).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)


__all__ = [
    "METRIC_LEAF_VERIFIER_FILENAMES",
    "render_metric_leaf_verifier_files",
    "write_metric_leaf_verifier_bundle",
]
