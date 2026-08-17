"""Build and verify the atomic six-target portable BSM metric suite."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import tempfile
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    BSM_MARKET_GREEKS_VARIANT_ID,
    canonical_json_bytes,
    digest_file,
    digest_json,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    assert_bsm_metric_database_safe,
    bsm_metric_logical_checksum,
    load_bsm_metric_query_payloads,
    market_content_digest,
    project_bsm_metric_database,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.leakage import (
    scan_evaluation_view,
    scan_solver_observable_content,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_allocation import (
    ALLOCATION_POLICY_ID,
    MetricAssignment,
    allocate_metric_candidates,
    load_source_metric_candidates,
    validate_metric_assignments,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    TARGET_ORDER,
    MetricSpec,
    get_metric_spec,
    get_metric_spec_db_query_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
    export_portable_metric_toolset,
    validate_portable_metric_toolset,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools_v3 import (
    export_portable_metric_toolset_v3,
    validate_portable_metric_toolset_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.prompt_renderer import (
    render_bsm_metric_prompt,
    render_bsm_metric_prompt_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    compose_runtime_contract_v3,
)


SUITE_SCHEMA_VERSION = "bsm-market-metric-suite-v1.0.0"
TARGET_BATCH_SCHEMA_VERSION = "bsm-market-metric-batch-v1.0.0"
METRIC_TASK_SCHEMA_VERSION = "bsm-market-metric-portable-task-v1.0.0"
METRIC_SOURCE_SCHEMA_VERSION = "bsm-market-metric-source-manifest-v1.0.0"
METRIC_EVALUATION_SCHEMA_VERSION = "bsm-market-metric-evaluation-view-v1.0.0"
METRIC_INTERFACE_CONTRACT_VERSION = "bsm-market-metric-solver-interface-v1"
METRIC_PROJECTION_SCHEMA_VERSION = "bsm-market-metric-projection-v1.0.0"
PORTABLE_SUITE_STATUS = "PORTABLE_SUITE_VERIFIED"
SUITE_VARIANT_ID = "bsm_market_implied_metric_suite_v1"

SUITE_SCHEMA_VERSION_V3 = "bsm-market-metric-suite-v2.0.0"
TARGET_BATCH_SCHEMA_VERSION_V3 = "bsm-market-metric-batch-v2.0.0"
METRIC_PACKAGE_SCHEMA_VERSION_V3 = "agent-task-package-v3.0.0"
METRIC_TASK_INTERFACE_VERSION_V3 = "bsm-market-metric-agent-task-v2.0.0"
METRIC_SOURCE_SCHEMA_VERSION_V3 = (
    "bsm-market-metric-source-manifest-v2.0.0"
)
METRIC_EVALUATION_SCHEMA_VERSION_V3 = (
    "bsm-market-metric-evaluation-view-v2.0.0"
)
METRIC_INTERFACE_CONTRACT_VERSION_V3 = (
    "bsm-market-metric-solver-interface-v2"
)
METRIC_PROJECTION_SCHEMA_VERSION_V3 = (
    "bsm-market-metric-projection-v2.0.0"
)

_EXPECTED_RUN_SCHEMA = "bsm-market-implied-greeks-batch-run-v2.0.0"
_EXPECTED_TASK_FAMILY = "bsm_greeks"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVALUATION_FILES = (
    "manifest.json",
    "public/prompt.md",
    "public/runtime_contract.json",
    "public/submission.schema.json",
)
_TOOL_FILES = (
    "toolset.json",
    "payloads/underlyings.json",
    "payloads/options.json",
)
_TOOL_FILES_V3 = ("toolset.json",)
_VERIFIER_FILES = (
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
_FORBIDDEN_TREE_PARTS = {
    "authoring_private",
    "dataset",
    "private",
    "reference",
    "__pycache__",
}


@dataclass(frozen=True, slots=True)
class PortableMetricSuite:
    """Result of one fully verified atomic suite publication."""

    delivery_root: Path
    manifest: dict[str, Any]
    assignments: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _PreparedLeaf:
    protocol_version: str
    assignment: MetricAssignment
    spec: MetricSpec
    source_package: Path
    source_manifest: dict[str, Any]
    runtime_contract: dict[str, Any]
    prompt: str
    schema_path: Path
    prompt_digest: str
    runtime_digest: str
    schema_digest: str
    source_interface_digest: str
    derived_interface_digest: str
    identity_payload: dict[str, Any]
    derived_task_id: str
    derived_snapshot_id: str


@dataclass(frozen=True, slots=True)
class _SuiteProtocol:
    name: str
    suite_schema_version: str
    batch_schema_version: str
    source_schema_version: str
    evaluation_schema_version: str
    interface_contract_version: str
    projection_schema_version: str
    tool_files: tuple[str, ...]
    package_schema_version: str | None
    task_interface_version: str | None


_V2_PROTOCOL = _SuiteProtocol(
    name="v2-static-json",
    suite_schema_version=SUITE_SCHEMA_VERSION,
    batch_schema_version=TARGET_BATCH_SCHEMA_VERSION,
    source_schema_version=METRIC_SOURCE_SCHEMA_VERSION,
    evaluation_schema_version=METRIC_EVALUATION_SCHEMA_VERSION,
    interface_contract_version=METRIC_INTERFACE_CONTRACT_VERSION,
    projection_schema_version=METRIC_PROJECTION_SCHEMA_VERSION,
    tool_files=_TOOL_FILES,
    package_schema_version=None,
    task_interface_version=None,
)
_V3_PROTOCOL = _SuiteProtocol(
    name="v3-duckdb-query",
    suite_schema_version=SUITE_SCHEMA_VERSION_V3,
    batch_schema_version=TARGET_BATCH_SCHEMA_VERSION_V3,
    source_schema_version=METRIC_SOURCE_SCHEMA_VERSION_V3,
    evaluation_schema_version=METRIC_EVALUATION_SCHEMA_VERSION_V3,
    interface_contract_version=METRIC_INTERFACE_CONTRACT_VERSION_V3,
    projection_schema_version=METRIC_PROJECTION_SCHEMA_VERSION_V3,
    tool_files=_TOOL_FILES_V3,
    package_schema_version=METRIC_PACKAGE_SCHEMA_VERSION_V3,
    task_interface_version=METRIC_TASK_INTERFACE_VERSION_V3,
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is not a portable identifier")
    if value in {".", ".."}:
        raise ValueError(f"{label} is not a portable identifier")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_plain_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required regular file is missing: {path}")


def _json_contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _json_contains_absolute_path(key) or _json_contains_absolute_path(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_json_contains_absolute_path(item) for item in value)
    return isinstance(value, str) and (
        value.startswith("/") or PureWindowsPath(value).is_absolute()
    )


def _assert_portable_json(value: Any, label: str) -> None:
    if _json_contains_absolute_path(value):
        raise ValueError(f"absolute path leaked into {label}")
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if "sampling_seed" in serialized or "selector_seed" in serialized:
        raise ValueError(f"private selection provenance leaked into {label}")


def _artifact_visibility(relative: str) -> str:
    if relative.startswith("evaluation_view/"):
        return "solver_evaluation_view"
    if relative == "task.duckdb" or relative.startswith("trusted_tools/"):
        return "tool_host_only"
    if relative.startswith("verifier/"):
        return "verifier_only"
    if relative == "source_manifest.json":
        return "delivery_audit_only"
    raise ValueError(f"artifact has no declared visibility: {relative}")


def _load_profile(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    _require_plain_file(profile_path)
    profile = load_json_object(profile_path)
    expected = {
        "profile_schema_version",
        "source_variant_id",
        "suite_variant_id",
        "targets",
        "tasks_per_target",
        "total_task_count",
        "allocation_policy_id",
        "require_unique_source_task",
        "require_unique_source_database",
        "require_unique_market_content",
        "require_unique_derived_task",
        "require_unique_derived_database",
    }
    if set(profile) != expected:
        raise ValueError("metric delivery profile has missing or extra fields")
    tasks_per_target = profile["tasks_per_target"]
    if (
        profile["profile_schema_version"]
        != "bsm-market-metric-delivery-profile-v1.0.0"
        or profile["source_variant_id"] != BSM_MARKET_GREEKS_VARIANT_ID
        or profile["suite_variant_id"] != SUITE_VARIANT_ID
        or profile["targets"] != list(TARGET_ORDER)
        or type(tasks_per_target) is not int
        or tasks_per_target < 1
        or profile["total_task_count"] != len(TARGET_ORDER) * tasks_per_target
        or profile["allocation_policy_id"] != ALLOCATION_POLICY_ID
        or any(profile[field] is not True for field in expected if field.startswith("require_"))
    ):
        raise ValueError("metric delivery profile contract is invalid")
    return profile


def _load_explicit_assignments(path: str | Path) -> list[Mapping[str, Any]]:
    assignment_path = Path(path)
    _require_plain_file(assignment_path)
    raw = json.loads(assignment_path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping) and set(raw) == {"assignments"}:
        raw = raw["assignments"]
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError("assignment file must contain an assignment array")
    return list(raw)


def _interface_digest(
    *,
    prompt_digest: str,
    runtime_digest: str,
    schema_digest: str,
    contract_version: str = METRIC_INTERFACE_CONTRACT_VERSION,
) -> str:
    return digest_json(
        {
            "interface_contract_version": contract_version,
            "prompt_digest": prompt_digest,
            "runtime_contract_digest": runtime_digest,
            "submission_schema_digest": schema_digest,
        }
    )


def _prepare_leaf(
    assignment: MetricAssignment, protocol: _SuiteProtocol = _V2_PROTOCOL
) -> _PreparedLeaf:
    spec = (
        get_metric_spec_db_query_v3(assignment.target)
        if protocol is _V3_PROTOCOL
        else get_metric_spec(assignment.target)
    )
    source_database = assignment.source_database_path
    source_package = source_database.parent.parent
    source_manifest_path = source_package / "manifest.json"
    runtime_path = source_package / "public/runtime_contract.json"
    prompt_path = source_package / "public/prompt.md"
    source_schema_path = source_package / "public/submission.schema.json"
    for path in (source_manifest_path, runtime_path, prompt_path, source_schema_path):
        _require_plain_file(path)
    source_manifest = load_json_object(source_manifest_path)
    artifacts = source_manifest.get("artifacts")
    if (
        source_manifest.get("task_id") != assignment.source_task_id
        or source_manifest.get("variant_id") != BSM_MARKET_GREEKS_VARIANT_ID
        or source_manifest.get("build_status") != "ACCEPTED"
        or not isinstance(artifacts, Mapping)
        or artifacts.get("public/task.duckdb")
        != assignment.source_database_file_digest
    ):
        raise ValueError("metric source package identity is invalid")
    for relative in (
        "public/prompt.md",
        "public/runtime_contract.json",
        "public/submission.schema.json",
    ):
        if artifacts.get(relative) != digest_file(source_package / relative):
            raise ValueError("metric source interface digest is invalid")
    if protocol is _V3_PROTOCOL:
        global_profile = load_json_object(
            _REPOSITORY_ROOT
            / "environments/solver/capabilities.global_v3.json"
        )
        task_overlay = load_json_object(
            _REPOSITORY_ROOT
            / "environments/solver/capabilities.bsm_greeks_v3.json"
        )
        runtime = compose_runtime_contract_v3(global_profile, task_overlay)
        prompt = render_bsm_metric_prompt_v3(spec, runtime)
    else:
        runtime = load_json_object(runtime_path)
        prompt = render_bsm_metric_prompt(spec, runtime)
    schema_path = _REPOSITORY_ROOT / "schemas" / spec.schema_filename
    _require_plain_file(schema_path)
    prompt_digest = sha256(prompt.encode("utf-8")).hexdigest()
    runtime_digest = (
        digest_json(runtime)
        if protocol is _V3_PROTOCOL
        else digest_file(runtime_path)
    )
    schema_digest = digest_file(schema_path)
    source_interface_digest = digest_json(
        {
            "prompt_digest": artifacts["public/prompt.md"],
            "runtime_contract_digest": artifacts["public/runtime_contract.json"],
            "submission_schema_digest": artifacts["public/submission.schema.json"],
        }
    )
    derived_interface_digest = _interface_digest(
        prompt_digest=prompt_digest,
        runtime_digest=runtime_digest,
        schema_digest=schema_digest,
        contract_version=protocol.interface_contract_version,
    )
    parent = source_manifest.get("parent_snapshot")
    if not isinstance(parent, Mapping):
        raise ValueError("source parent identity is invalid")
    parent_logical_checksum = _require_sha256(
        parent.get("logical_checksum"), "source parent logical checksum"
    )
    identity_payload = {
        "metric_projection_schema_version": protocol.projection_schema_version,
        "source_parent_logical_checksum": parent_logical_checksum,
        "source_market_content_digest": assignment.source_market_content_digest,
        "target": spec.target,
        "variant_id": spec.variant_id,
        "method_id": spec.method_id,
        "prompt_digest": prompt_digest,
        "runtime_contract_digest": runtime_digest,
        "submission_schema_digest": schema_digest,
    }
    suffix = sha256(canonical_json_bytes(identity_payload)).hexdigest()[:24]
    derived_task_id = spec.task_id_prefix + suffix
    derived_snapshot_id = "BSM-MARKET-METRIC-" + suffix.upper()
    return _PreparedLeaf(
        protocol_version=protocol.name,
        assignment=assignment,
        spec=spec,
        source_package=source_package,
        source_manifest=source_manifest,
        runtime_contract=runtime,
        prompt=prompt,
        schema_path=schema_path,
        prompt_digest=prompt_digest,
        runtime_digest=runtime_digest,
        schema_digest=schema_digest,
        source_interface_digest=source_interface_digest,
        derived_interface_digest=derived_interface_digest,
        identity_payload=identity_payload,
        derived_task_id=derived_task_id,
        derived_snapshot_id=derived_snapshot_id,
    )


def _planned_assignment(prepared: _PreparedLeaf) -> dict[str, Any]:
    assignment = prepared.assignment
    return {
        "target": prepared.spec.target,
        "round_index": assignment.round_index,
        "allocation_rank": assignment.allocation_rank,
        "source_task_id": assignment.source_task_id,
        "source_database_digest": assignment.source_database_file_digest,
        "source_market_content_digest": assignment.source_market_content_digest,
        "derived_task_id": prepared.derived_task_id,
    }


def _protocol_for_prepared(prepared: _PreparedLeaf) -> _SuiteProtocol:
    if prepared.protocol_version == _V2_PROTOCOL.name:
        return _V2_PROTOCOL
    if prepared.protocol_version == _V3_PROTOCOL.name:
        return _V3_PROTOCOL
    raise ValueError("prepared metric leaf has an unknown protocol")


def _write_evaluation_view(prepared: _PreparedLeaf, task_root: Path) -> None:
    protocol = _protocol_for_prepared(prepared)
    public = task_root / "evaluation_view/public"
    _write_text(public / "prompt.md", prepared.prompt)
    _write_json(public / "runtime_contract.json", prepared.runtime_contract)
    public.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        prepared.schema_path,
        public / "submission.schema.json",
    )
    artifacts = {
        "public/prompt.md": digest_file(public / "prompt.md"),
        "public/runtime_contract.json": digest_file(public / "runtime_contract.json"),
        "public/submission.schema.json": digest_file(public / "submission.schema.json"),
    }
    if (
        artifacts["public/prompt.md"] != prepared.prompt_digest
        or artifacts["public/runtime_contract.json"] != prepared.runtime_digest
        or artifacts["public/submission.schema.json"] != prepared.schema_digest
    ):
        raise ValueError("derived solver interface changed while materializing")
    manifest = {
        "evaluation_view_schema_version": protocol.evaluation_schema_version,
        "solver_interface_contract_version": protocol.interface_contract_version,
        "view_kind": "evaluation",
        "task_family": _EXPECTED_TASK_FAMILY,
        "task_id": prepared.derived_task_id,
        "task_version": prepared.spec.task_version,
        "target_metric": prepared.spec.target,
        "variant_id": prepared.spec.variant_id,
        "method_id": prepared.spec.method_id,
        "trusted_tools": prepared.runtime_contract["trusted_tools"],
        "artifacts": artifacts,
    }
    _assert_portable_json(manifest, "evaluation manifest")
    _write_json(task_root / "evaluation_view/manifest.json", manifest)
    scan_evaluation_view(task_root / "evaluation_view")


def _write_source_manifest(
    *,
    prepared: _PreparedLeaf,
    task_root: Path,
    run_summary_digest: str,
    derived_database_digest: str,
    derived_logical_checksum: str,
    derived_market_content_digest: str,
) -> dict[str, Any]:
    protocol = _protocol_for_prepared(prepared)
    source = prepared.source_manifest
    source_public = source.get("public_child_snapshot")
    if not isinstance(source_public, Mapping):
        raise ValueError("source public snapshot identity is invalid")
    payload = {
        "source_manifest_schema_version": protocol.source_schema_version,
        "target_metric": prepared.spec.target,
        "source_run": {
            "run_schema_version": _EXPECTED_RUN_SCHEMA,
            "run_summary_digest": run_summary_digest,
        },
        "source_package": {
            "relative_path": (
                f"packages/{BSM_MARKET_GREEKS_VARIANT_ID}/"
                f"{prepared.assignment.source_task_id}"
            ),
            "manifest_digest": digest_file(prepared.source_package / "manifest.json"),
            "task_id": prepared.assignment.source_task_id,
            "database_digest": prepared.assignment.source_database_file_digest,
            "market_content_digest": prepared.assignment.source_market_content_digest,
            "public_logical_checksum": _require_sha256(
                source_public.get("logical_checksum"),
                "source public logical checksum",
            ),
        },
        "metric_projection": {
            "projection_schema_version": protocol.projection_schema_version,
            "identity_payload": prepared.identity_payload,
            "identity_only_columns": [
                "metadata.public_task.schema_version",
                "metadata.public_task.task_id",
                "metadata.public_task.task_version",
                "metadata.public_task.variant_id",
                "metadata.public_task.snapshot_id",
                "solver_visible.underlying_market_inputs.task_id",
                "solver_visible.underlying_market_inputs.snapshot_id",
                "solver_visible.option_quote_inputs.task_id",
                "solver_visible.option_quote_inputs.snapshot_id",
            ],
        },
        "derived_task": {
            "task_id": prepared.derived_task_id,
            "snapshot_id": prepared.derived_snapshot_id,
            "database_schema_version": prepared.spec.database_schema_version,
            "database_digest": derived_database_digest,
            "logical_checksum": derived_logical_checksum,
            "market_content_digest": derived_market_content_digest,
        },
        "market_content_equal": (
            derived_market_content_digest
            == prepared.assignment.source_market_content_digest
        ),
        "interfaces": {
            "source_interface_digest": prepared.source_interface_digest,
            "derived_interface_digest": prepared.derived_interface_digest,
        },
    }
    if payload["market_content_equal"] is not True:
        raise ValueError("metric projection changed solver-visible market content")
    _assert_portable_json(payload, "source_manifest.json")
    _write_json(task_root / "source_manifest.json", payload)
    return payload


def _build_metric_leaf(
    *,
    prepared: _PreparedLeaf,
    task_root: Path,
    run_summary_digest: str,
) -> dict[str, Any]:
    """Build one derived leaf without copying source reference/private artifacts."""

    protocol = _protocol_for_prepared(prepared)
    task_root.mkdir(parents=True, exist_ok=False)
    database = task_root / "task.duckdb"
    project_bsm_metric_database(
        prepared.assignment.source_database_path,
        database,
        prepared.spec,
        prepared.derived_task_id,
        prepared.derived_snapshot_id,
    )
    assert_bsm_metric_database_safe(
        database,
        prepared.spec,
        expected_task_id=prepared.derived_task_id,
        expected_snapshot_id=prepared.derived_snapshot_id,
    )
    derived_database_digest = digest_file(database)
    derived_content_digest = market_content_digest(database)
    derived_logical_checksum = bsm_metric_logical_checksum(
        database,
        prepared.spec,
        expected_task_id=prepared.derived_task_id,
        expected_snapshot_id=prepared.derived_snapshot_id,
    )
    _write_evaluation_view(prepared, task_root)
    exporter = (
        export_portable_metric_toolset_v3
        if protocol is _V3_PROTOCOL
        else export_portable_metric_toolset
    )
    exporter(
        database=database,
        runtime_contract=task_root / "evaluation_view/public/runtime_contract.json",
        submission_schema=task_root / "evaluation_view/public/submission.schema.json",
        output_directory=task_root / "trusted_tools",
        metric_spec=prepared.spec,
    )
    if protocol is _V3_PROTOCOL:
        from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
            write_metric_verifier_v3,
        )

        write_metric_verifier_v3(task_root / "verifier", prepared.spec)
    else:
        from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
            write_metric_verifier,
        )

        write_metric_verifier(task_root / "verifier", prepared.spec)
    _write_source_manifest(
        prepared=prepared,
        task_root=task_root,
        run_summary_digest=run_summary_digest,
        derived_database_digest=derived_database_digest,
        derived_logical_checksum=derived_logical_checksum,
        derived_market_content_digest=derived_content_digest,
    )
    artifact_paths = sorted(
        path.relative_to(task_root).as_posix()
        for path in task_root.rglob("*")
        if path.is_file()
    )
    expected_paths = sorted(
        ["task.duckdb", "source_manifest.json"]
        + [f"evaluation_view/{name}" for name in _EVALUATION_FILES]
        + [f"trusted_tools/{name}" for name in protocol.tool_files]
        + [f"verifier/{name}" for name in _VERIFIER_FILES]
    )
    if artifact_paths != expected_paths:
        raise ValueError("metric leaf contains an artifact outside the allowlist")
    artifacts = {
        relative: digest_file(task_root / relative) for relative in artifact_paths
    }
    visibility = {
        relative: _artifact_visibility(relative) for relative in artifact_paths
    }
    manifest = {
        "delivery_status": PORTABLE_SUITE_STATUS,
        "task_family": _EXPECTED_TASK_FAMILY,
        "task_id": prepared.derived_task_id,
        "task_version": prepared.spec.task_version,
        "target_metric": prepared.spec.target,
        "variant_id": prepared.spec.variant_id,
        "method_id": prepared.spec.method_id,
        "source_task_id": prepared.assignment.source_task_id,
        "source_manifest_digest": artifacts["source_manifest.json"],
        "public_child_snapshot": {
            "snapshot_id": prepared.derived_snapshot_id,
            "revision": 1,
            "logical_checksum": derived_logical_checksum,
        },
        "database": {
            "schema_version": prepared.spec.database_schema_version,
            "snapshot_id": prepared.derived_snapshot_id,
            "file_digest": derived_database_digest,
            "logical_checksum": derived_logical_checksum,
            "market_content_digest": derived_content_digest,
        },
        "evaluation_view": {
            "path": "evaluation_view",
            "manifest_digest": artifacts["evaluation_view/manifest.json"],
            "interface_digest": prepared.derived_interface_digest,
        },
        "toolset": {
            "path": "trusted_tools/toolset.json",
            "digest": artifacts["trusted_tools/toolset.json"],
        },
        "verifier": {
            "id": prepared.spec.verifier_id,
            "oracle_config_digest": artifacts["verifier/oracle_config.json"],
            "runtime_digest": artifacts["verifier/runtime.py"],
        },
        "artifacts": artifacts,
        "artifact_visibility": visibility,
    }
    if protocol is _V3_PROTOCOL:
        manifest.update(
            {
                "package_schema_version": protocol.package_schema_version,
                "task_interface_version": protocol.task_interface_version,
            }
        )
    else:
        manifest["task_delivery_schema_version"] = METRIC_TASK_SCHEMA_VERSION
    _assert_portable_json(manifest, "delivery_manifest.json")
    _write_json(task_root / "delivery_manifest.json", manifest)
    return {
        **_planned_assignment(prepared),
        "derived_snapshot_id": prepared.derived_snapshot_id,
        "derived_database_digest": derived_database_digest,
        "derived_logical_checksum": derived_logical_checksum,
        "derived_interface_digest": prepared.derived_interface_digest,
        "relative_path": (
            f"targets/{prepared.spec.target}/tasks/{prepared.derived_task_id}"
        ),
        "delivery_manifest_digest": digest_file(task_root / "delivery_manifest.json"),
    }


def _verify_metric_leaf(
    task_root: Path,
    assignment: Mapping[str, Any],
    *,
    run_summary_identity: Mapping[str, Any],
    protocol: _SuiteProtocol = _V2_PROTOCOL,
) -> dict[str, Any]:
    task_id = assignment.get("derived_task_id")
    target = assignment.get("target")
    if not isinstance(task_id, str) or not isinstance(target, str):
        raise ValueError("suite assignment identity is invalid")
    spec = (
        get_metric_spec_db_query_v3(target)
        if protocol is _V3_PROTOCOL
        else get_metric_spec(target)
    )
    if task_root.name != task_id or re.fullmatch(spec.task_id_pattern, task_id) is None:
        raise ValueError("metric leaf path differs from its task identity")
    manifest_path = task_root / "delivery_manifest.json"
    _require_plain_file(manifest_path)
    manifest = load_json_object(manifest_path)
    expected_manifest_fields = {
        "delivery_status",
        "task_family",
        "task_id",
        "task_version",
        "target_metric",
        "variant_id",
        "method_id",
        "source_task_id",
        "source_manifest_digest",
        "public_child_snapshot",
        "database",
        "evaluation_view",
        "toolset",
        "verifier",
        "artifacts",
        "artifact_visibility",
    }
    if protocol is _V3_PROTOCOL:
        expected_manifest_fields.update(
            {"package_schema_version", "task_interface_version"}
        )
        schema_identity_valid = (
            manifest.get("package_schema_version")
            == protocol.package_schema_version
            and manifest.get("task_interface_version")
            == protocol.task_interface_version
        )
    else:
        expected_manifest_fields.add("task_delivery_schema_version")
        schema_identity_valid = (
            manifest.get("task_delivery_schema_version")
            == METRIC_TASK_SCHEMA_VERSION
        )
    if set(manifest) != expected_manifest_fields or (
        not schema_identity_valid
        or manifest["delivery_status"] != PORTABLE_SUITE_STATUS
        or manifest["task_family"] != _EXPECTED_TASK_FAMILY
        or manifest["task_id"] != task_id
        or manifest["task_version"] != spec.task_version
        or manifest["target_metric"] != target
        or manifest["variant_id"] != spec.variant_id
        or manifest["method_id"] != spec.method_id
        or manifest["source_task_id"] != assignment.get("source_task_id")
    ):
        raise ValueError("metric leaf manifest identity changed")
    artifacts = manifest["artifacts"]
    visibility = manifest["artifact_visibility"]
    if not isinstance(artifacts, Mapping) or not isinstance(visibility, Mapping):
        raise ValueError("metric leaf artifact maps are invalid")
    expected_artifacts = set(
        ["task.duckdb", "source_manifest.json"]
        + [f"evaluation_view/{name}" for name in _EVALUATION_FILES]
        + [f"trusted_tools/{name}" for name in protocol.tool_files]
        + [f"verifier/{name}" for name in _VERIFIER_FILES]
    )
    if set(artifacts) != expected_artifacts or set(visibility) != expected_artifacts:
        raise ValueError("metric leaf artifact allowlist changed")
    for relative in sorted(expected_artifacts):
        path = task_root / relative
        _require_plain_file(path)
        if artifacts[relative] != digest_file(path):
            raise ValueError(f"metric leaf artifact digest mismatch: {relative}")
        if visibility[relative] != _artifact_visibility(relative):
            raise ValueError(f"metric leaf artifact visibility changed: {relative}")
    if manifest["source_manifest_digest"] != artifacts["source_manifest.json"]:
        raise ValueError("metric leaf source manifest binding changed")
    if manifest["toolset"] != {
        "path": "trusted_tools/toolset.json",
        "digest": artifacts["trusted_tools/toolset.json"],
    }:
        raise ValueError("metric leaf toolset binding changed")
    evaluation = manifest["evaluation_view"]
    if not isinstance(evaluation, Mapping) or (
        evaluation.get("path") != "evaluation_view"
        or evaluation.get("manifest_digest")
        != artifacts["evaluation_view/manifest.json"]
        or evaluation.get("interface_digest")
        != assignment.get("derived_interface_digest")
    ):
        raise ValueError("metric leaf evaluation binding changed")
    scan_evaluation_view(task_root / "evaluation_view")
    if protocol is _V3_PROTOCOL:
        validate_portable_metric_toolset_v3(task_root, metric_spec=spec)
    else:
        validate_portable_metric_toolset(task_root, metric_spec=spec)
    database = task_root / "task.duckdb"
    snapshot_id = assignment.get("derived_snapshot_id")
    if not isinstance(snapshot_id, str):
        raise ValueError("metric derived snapshot identity is invalid")
    assert_bsm_metric_database_safe(
        database,
        spec,
        expected_task_id=task_id,
        expected_snapshot_id=snapshot_id,
    )
    database_contract = manifest["database"]
    derived_content_digest = market_content_digest(database)
    if database_contract != {
        "schema_version": spec.database_schema_version,
        "snapshot_id": snapshot_id,
        "file_digest": digest_file(database),
        "logical_checksum": bsm_metric_logical_checksum(
            database,
            spec,
            expected_task_id=task_id,
            expected_snapshot_id=snapshot_id,
        ),
        "market_content_digest": derived_content_digest,
    }:
        raise ValueError("metric leaf database binding changed")
    if manifest["public_child_snapshot"] != {
        "snapshot_id": snapshot_id,
        "revision": 1,
        "logical_checksum": database_contract["logical_checksum"],
    }:
        raise ValueError("metric leaf public snapshot identity changed")
    if (
        derived_content_digest != assignment.get("source_market_content_digest")
        or digest_file(database) != assignment.get("derived_database_digest")
    ):
        raise ValueError("metric leaf database differs from suite assignment")
    raw_underlyings, raw_options = load_bsm_metric_query_payloads(database, spec)
    expected_underlyings = list(raw_underlyings)
    expected_options = list(raw_options)
    if protocol is _V3_PROTOCOL:
        underlyings = expected_underlyings
        options = expected_options
        payload_directory = task_root / "trusted_tools/payloads"
        if payload_directory.exists() or payload_directory.is_symlink():
            raise ValueError("v3 metric leaf contains a duplicate market payload")
    else:
        underlyings = json.loads(
            (task_root / "trusted_tools/payloads/underlyings.json").read_text(
                encoding="utf-8"
            )
        )
        options = json.loads(
            (task_root / "trusted_tools/payloads/options.json").read_text(
                encoding="utf-8"
            )
        )
        if underlyings != expected_underlyings or options != expected_options:
            raise ValueError("metric trusted-tool payload differs from task.duckdb")
    if (
        len(underlyings) != 8
        or len(options) != 160
        or {str(row["task_id"]) for row in [*underlyings, *options]}
        != {task_id}
    ):
        raise ValueError("metric trusted-tool query identity changed")
    scan_solver_observable_content(
        underlyings, "metric underlying query response", forbid_answer_fields=True
    )
    scan_solver_observable_content(
        options, "metric option query response", forbid_answer_fields=True
    )
    source = load_json_object(task_root / "source_manifest.json")
    if (
        source.get("source_manifest_schema_version")
        != protocol.source_schema_version
        or source.get("target_metric") != target
        or source.get("source_run") != dict(run_summary_identity)
        or source.get("market_content_equal") is not True
    ):
        raise ValueError("metric source manifest identity changed")
    source_package = source.get("source_package")
    derived = source.get("derived_task")
    if not isinstance(source_package, Mapping) or not isinstance(derived, Mapping):
        raise ValueError("metric source/derived database identity is invalid")
    if (
        source_package.get("task_id") != assignment.get("source_task_id")
        or source_package.get("database_digest")
        != assignment.get("source_database_digest")
        or source_package.get("market_content_digest")
        != assignment.get("source_market_content_digest")
        or derived.get("task_id") != task_id
        or derived.get("snapshot_id") != snapshot_id
        or derived.get("database_digest") != digest_file(database)
        or derived.get("market_content_digest") != derived_content_digest
    ):
        raise ValueError("metric source manifest database binding changed")
    if protocol is _V3_PROTOCOL:
        from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
            expected_metric_submission_v3 as expected_submission,
            metric_oracle_config_v3 as oracle_for_spec,
            verify_market_metric_submission_v3 as verify_submission,
        )
    else:
        from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
            expected_metric_submission as expected_submission,
            metric_oracle_config as oracle_for_spec,
            verify_market_metric_submission as verify_submission,
        )

    expected_oracle = oracle_for_spec(spec)
    if load_json_object(task_root / "verifier/oracle_config.json") != expected_oracle:
        raise ValueError("metric verifier oracle config differs from registry")
    canonical_submission = expected_submission(task_root, expected_oracle)
    verify_submission(
        task_root,
        canonical_submission,
        expected_oracle,
    )
    _assert_portable_json(manifest, "metric delivery manifest")
    _assert_portable_json(source, "metric source manifest")
    return manifest


def _assert_suite_tree(
    root: Path,
    manifest: Mapping[str, Any],
    protocol: _SuiteProtocol = _V2_PROTOCOL,
) -> None:
    expected = {"suite_manifest.json"}
    assignments = manifest.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("suite assignments must be an array")
    for target in TARGET_ORDER:
        expected.add(f"targets/{target}/batch_manifest.json")
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            raise ValueError("suite assignment entry must be an object")
        relative_root = assignment.get("relative_path")
        if not isinstance(relative_root, str):
            raise ValueError("suite assignment path is invalid")
        expected.add(f"{relative_root}/delivery_manifest.json")
        expected.add(f"{relative_root}/task.duckdb")
        expected.add(f"{relative_root}/source_manifest.json")
        expected.update(f"{relative_root}/evaluation_view/{name}" for name in _EVALUATION_FILES)
        expected.update(
            f"{relative_root}/trusted_tools/{name}"
            for name in protocol.tool_files
        )
        expected.update(f"{relative_root}/verifier/{name}" for name in _VERIFIER_FILES)
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("portable metric suite cannot contain symlinks")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if set(Path(relative).parts) & _FORBIDDEN_TREE_PARTS:
                raise ValueError("portable metric suite contains a forbidden artifact")
            if path.suffix == ".json":
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError(f"invalid portable JSON artifact: {relative}") from error
                _assert_portable_json(value, relative)
            actual.add(relative)
    if actual != expected:
        raise ValueError("portable metric suite tree differs from the allowlist")


def _verify_portable_bsm_metric_suite(
    root: str | Path, protocol: _SuiteProtocol
) -> dict[str, Any]:
    """Verify every leaf, target batch, global assignment, digest, and tree."""

    suite_root = Path(root)
    manifest_path = suite_root / "suite_manifest.json"
    _require_plain_file(manifest_path)
    manifest = load_json_object(manifest_path)
    expected_fields = {
        "suite_schema_version",
        "suite_id",
        "delivery_id",
        "delivery_status",
        "source_variant_id",
        "source_run",
        "allocation_policy_id",
        "allocation_id",
        "target_order",
        "tasks_per_target",
        "total_task_count",
        "unique_source_task_count",
        "unique_source_database_count",
        "unique_market_content_count",
        "unique_derived_task_count",
        "unique_derived_database_count",
        "assignment_digest",
        "target_batches",
        "assignments",
    }
    if set(manifest) != expected_fields or (
        manifest["suite_schema_version"] != protocol.suite_schema_version
        or manifest["delivery_status"] != PORTABLE_SUITE_STATUS
        or manifest["source_variant_id"] != BSM_MARKET_GREEKS_VARIANT_ID
        or manifest["allocation_policy_id"] != ALLOCATION_POLICY_ID
        or manifest["target_order"] != list(TARGET_ORDER)
    ):
        raise ValueError("portable metric suite identity changed")
    _require_identifier(manifest["suite_id"], "suite_id")
    _require_identifier(manifest["delivery_id"], "delivery_id")
    _require_identifier(manifest["allocation_id"], "allocation_id")
    _require_sha256(manifest["assignment_digest"], "assignment_digest")
    tasks_per_target = manifest["tasks_per_target"]
    total = manifest["total_task_count"]
    assignments = manifest["assignments"]
    if (
        type(tasks_per_target) is not int
        or tasks_per_target < 1
        or total != len(TARGET_ORDER) * tasks_per_target
        or not isinstance(assignments, list)
        or len(assignments) != total
    ):
        raise ValueError("portable metric suite task counts are invalid")
    targets = [item.get("target") for item in assignments if isinstance(item, Mapping)]
    if Counter(targets) != Counter({target: tasks_per_target for target in TARGET_ORDER}):
        raise ValueError("portable metric suite target allocation changed")
    uniqueness = {
        "unique_source_task_count": {item.get("source_task_id") for item in assignments},
        "unique_source_database_count": {item.get("source_database_digest") for item in assignments},
        "unique_market_content_count": {item.get("source_market_content_digest") for item in assignments},
        "unique_derived_task_count": {item.get("derived_task_id") for item in assignments},
        "unique_derived_database_count": {item.get("derived_database_digest") for item in assignments},
    }
    for field, values in uniqueness.items():
        if manifest[field] != total or len(values) != total or None in values:
            raise ValueError(f"portable metric suite {field} is invalid")
    planned = [
        {
            key: item[key]
            for key in (
                "target",
                "round_index",
                "allocation_rank",
                "source_task_id",
                "source_database_digest",
                "source_market_content_digest",
                "derived_task_id",
            )
        }
        for item in assignments
    ]
    if digest_json(planned) != manifest["assignment_digest"]:
        raise ValueError("portable metric suite assignment digest changed")
    source_run = manifest["source_run"]
    if (
        not isinstance(source_run, Mapping)
        or set(source_run) != {"run_schema_version", "run_summary_digest"}
        or source_run["run_schema_version"] != _EXPECTED_RUN_SCHEMA
        or _SHA256.fullmatch(str(source_run["run_summary_digest"])) is None
    ):
        raise ValueError("portable metric source run identity is invalid")
    target_batches = manifest["target_batches"]
    if not isinstance(target_batches, Mapping) or set(target_batches) != set(TARGET_ORDER):
        raise ValueError("portable metric target batch index changed")
    for target in TARGET_ORDER:
        spec = (
            get_metric_spec_db_query_v3(target)
            if protocol is _V3_PROTOCOL
            else get_metric_spec(target)
        )
        target_assignments = [item for item in assignments if item["target"] == target]
        batch_path = suite_root / f"targets/{target}/batch_manifest.json"
        batch = load_json_object(batch_path)
        expected_batch_fields = {
            "batch_schema_version",
            "suite_id",
            "assignment_digest",
            "delivery_status",
            "target_metric",
            "task_family",
            "variant_id",
            "method_id",
            "split_policy",
            "task_count",
            "unique_source_task_count",
            "unique_source_database_count",
            "unique_market_content_count",
            "task_ids",
            "tasks",
        }
        task_ids = [item["derived_task_id"] for item in target_assignments]
        if set(batch) != expected_batch_fields or (
            batch["batch_schema_version"] != protocol.batch_schema_version
            or batch["suite_id"] != manifest["suite_id"]
            or batch["assignment_digest"] != manifest["assignment_digest"]
            or batch["delivery_status"] != PORTABLE_SUITE_STATUS
            or batch["target_metric"] != target
            or batch["task_family"] != _EXPECTED_TASK_FAMILY
            or batch["variant_id"] != spec.variant_id
            or batch["method_id"] != spec.method_id
            or batch["split_policy"]
            != {"group": "evaluation", "policy": "one_metric_per_leaf_task"}
            or batch["task_count"] != tasks_per_target
            or batch["unique_source_task_count"] != tasks_per_target
            or batch["unique_source_database_count"] != tasks_per_target
            or batch["unique_market_content_count"] != tasks_per_target
            or batch["task_ids"] != task_ids
            or batch["tasks"] != target_assignments
        ):
            raise ValueError(f"portable metric batch identity changed: {target}")
        batch_index = target_batches[target]
        if batch_index != {
            "relative_path": f"targets/{target}/batch_manifest.json",
            "manifest_digest": digest_file(batch_path),
        }:
            raise ValueError(f"portable metric batch digest changed: {target}")
        for assignment in target_assignments:
            task_root = suite_root / assignment["relative_path"]
            _verify_metric_leaf(
                task_root,
                assignment,
                run_summary_identity=source_run,
                protocol=protocol,
            )
            if assignment["delivery_manifest_digest"] != digest_file(
                task_root / "delivery_manifest.json"
            ):
                raise ValueError("suite assignment leaf digest changed")
    _assert_portable_json(manifest, "suite_manifest.json")
    _assert_suite_tree(suite_root, manifest, protocol)
    return manifest


def verify_portable_bsm_metric_suite(root: str | Path) -> dict[str, Any]:
    """Verify a legacy v2 static-JSON metric suite only."""

    return _verify_portable_bsm_metric_suite(root, _V2_PROTOCOL)


def verify_portable_bsm_metric_suite_v3(root: str | Path) -> dict[str, Any]:
    """Verify every leaf and binding in one DuckDB-query v3 metric suite."""

    return _verify_portable_bsm_metric_suite(root, _V3_PROTOCOL)


def _build_portable_bsm_metric_suite(
    *,
    source_run: str | Path,
    output_root: str | Path,
    delivery_id: str,
    profile: str | Path,
    allocation_id: str,
    assignment_file: str | Path | None = None,
    expected_source_task_count: int = 24,
    protocol: _SuiteProtocol,
) -> PortableMetricSuite:
    """Build all targets in one staging tree and publish with one rename."""

    delivery_name = _require_identifier(delivery_id, "delivery_id")
    allocation_name = _require_identifier(allocation_id, "allocation_id")
    profile_value = _load_profile(profile)
    tasks_per_target = profile_value["tasks_per_target"]
    source_root = Path(source_run)
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("source_run must be a regular directory")
    summary_path = source_root / "run_summary.json"
    _require_plain_file(summary_path)
    candidates = load_source_metric_candidates(
        source_root,
        expected_source_task_count=expected_source_task_count,
    )
    if assignment_file is None:
        assignments = allocate_metric_candidates(
            candidates,
            allocation_id=allocation_name,
            tasks_per_target=tasks_per_target,
        )
    else:
        assignments = validate_metric_assignments(
            _load_explicit_assignments(assignment_file),
            candidates,
            allocation_id=allocation_name,
            tasks_per_target=tasks_per_target,
        )
    prepared = tuple(
        _prepare_leaf(assignment, protocol) for assignment in assignments
    )
    planned = [_planned_assignment(item) for item in prepared]
    assignment_digest = digest_json(planned)
    suite_id_prefix = (
        "bsm-market-metric-suite-v2-"
        if protocol is _V3_PROTOCOL
        else "bsm-market-metric-suite-v1-"
    )
    suite_id = suite_id_prefix + digest_json(
        {
            "delivery_id": delivery_name,
            "allocation_id": allocation_name,
            "assignment_digest": assignment_digest,
        }
    )[:24]
    output_base = Path(output_root)
    if output_base.exists() and (output_base.is_symlink() or not output_base.is_dir()):
        raise ValueError("output_root must be a regular directory")
    variant_root = output_base / SUITE_VARIANT_ID
    variant_root.mkdir(parents=True, exist_ok=True)
    if variant_root.is_symlink() or not variant_root.is_dir():
        raise ValueError("metric suite output directory is invalid")
    destination = variant_root / delivery_name
    lock_path = variant_root / f".{delivery_name}.build.lock"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"portable metric suite already exists: {destination}")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise FileExistsError(
            f"portable metric suite build is already active: {delivery_name}"
        ) from error
    os.close(descriptor)
    staging = Path(tempfile.mkdtemp(prefix=f".{delivery_name}.staging-", dir=variant_root))
    published = False
    try:
        run_summary_digest = digest_file(summary_path)
        built_assignments: list[dict[str, Any]] = []
        for item in prepared:
            task_root = staging / f"targets/{item.spec.target}/tasks/{item.derived_task_id}"
            built_assignments.append(
                _build_metric_leaf(
                    prepared=item,
                    task_root=task_root,
                    run_summary_digest=run_summary_digest,
                )
            )
        target_batches: dict[str, dict[str, str]] = {}
        for target in TARGET_ORDER:
            spec = (
                get_metric_spec_db_query_v3(target)
                if protocol is _V3_PROTOCOL
                else get_metric_spec(target)
            )
            target_assignments = [
                item for item in built_assignments if item["target"] == target
            ]
            batch = {
                "batch_schema_version": protocol.batch_schema_version,
                "suite_id": suite_id,
                "assignment_digest": assignment_digest,
                "delivery_status": PORTABLE_SUITE_STATUS,
                "target_metric": target,
                "task_family": _EXPECTED_TASK_FAMILY,
                "variant_id": spec.variant_id,
                "method_id": spec.method_id,
                "split_policy": {
                    "group": "evaluation",
                    "policy": "one_metric_per_leaf_task",
                },
                "task_count": tasks_per_target,
                "unique_source_task_count": len(
                    {item["source_task_id"] for item in target_assignments}
                ),
                "unique_source_database_count": len(
                    {item["source_database_digest"] for item in target_assignments}
                ),
                "unique_market_content_count": len(
                    {item["source_market_content_digest"] for item in target_assignments}
                ),
                "task_ids": [item["derived_task_id"] for item in target_assignments],
                "tasks": target_assignments,
            }
            batch_path = staging / f"targets/{target}/batch_manifest.json"
            _assert_portable_json(batch, f"{target} batch manifest")
            _write_json(batch_path, batch)
            target_batches[target] = {
                "relative_path": f"targets/{target}/batch_manifest.json",
                "manifest_digest": digest_file(batch_path),
            }
        total = len(built_assignments)
        suite_manifest = {
            "suite_schema_version": protocol.suite_schema_version,
            "suite_id": suite_id,
            "delivery_id": delivery_name,
            "delivery_status": PORTABLE_SUITE_STATUS,
            "source_variant_id": BSM_MARKET_GREEKS_VARIANT_ID,
            "source_run": {
                "run_schema_version": _EXPECTED_RUN_SCHEMA,
                "run_summary_digest": run_summary_digest,
            },
            "allocation_policy_id": ALLOCATION_POLICY_ID,
            "allocation_id": allocation_name,
            "target_order": list(TARGET_ORDER),
            "tasks_per_target": tasks_per_target,
            "total_task_count": total,
            "unique_source_task_count": len(
                {item["source_task_id"] for item in built_assignments}
            ),
            "unique_source_database_count": len(
                {item["source_database_digest"] for item in built_assignments}
            ),
            "unique_market_content_count": len(
                {item["source_market_content_digest"] for item in built_assignments}
            ),
            "unique_derived_task_count": len(
                {item["derived_task_id"] for item in built_assignments}
            ),
            "unique_derived_database_count": len(
                {item["derived_database_digest"] for item in built_assignments}
            ),
            "assignment_digest": assignment_digest,
            "target_batches": target_batches,
            "assignments": built_assignments,
        }
        _assert_portable_json(suite_manifest, "suite_manifest.json")
        _write_json(staging / "suite_manifest.json", suite_manifest)
        verified = _verify_portable_bsm_metric_suite(staging, protocol)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"portable metric suite already exists: {destination}")
        os.rename(staging, destination)
        published = True
        return PortableMetricSuite(
            delivery_root=destination,
            manifest=verified,
            assignments=tuple(built_assignments),
        )
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
        lock_path.unlink(missing_ok=True)


def build_portable_bsm_metric_suite(
    *,
    source_run: str | Path,
    output_root: str | Path,
    delivery_id: str,
    profile: str | Path,
    allocation_id: str,
    assignment_file: str | Path | None = None,
    expected_source_task_count: int = 24,
) -> PortableMetricSuite:
    """Build a legacy v2 static-JSON suite without changing its contract."""

    return _build_portable_bsm_metric_suite(
        source_run=source_run,
        output_root=output_root,
        delivery_id=delivery_id,
        profile=profile,
        allocation_id=allocation_id,
        assignment_file=assignment_file,
        expected_source_task_count=expected_source_task_count,
        protocol=_V2_PROTOCOL,
    )


def build_portable_bsm_metric_suite_v3(
    *,
    source_run: str | Path,
    output_root: str | Path,
    delivery_id: str,
    profile: str | Path,
    allocation_id: str,
    assignment_file: str | Path | None = None,
    expected_source_task_count: int = 24,
) -> PortableMetricSuite:
    """Build the breaking trusted-DuckDB v3 suite from frozen v2 markets."""

    return _build_portable_bsm_metric_suite(
        source_run=source_run,
        output_root=output_root,
        delivery_id=delivery_id,
        profile=profile,
        allocation_id=allocation_id,
        assignment_file=assignment_file,
        expected_source_task_count=expected_source_task_count,
        protocol=_V3_PROTOCOL,
    )


__all__ = [
    "METRIC_PROJECTION_SCHEMA_VERSION",
    "PORTABLE_SUITE_STATUS",
    "PortableMetricSuite",
    "SUITE_SCHEMA_VERSION",
    "SUITE_SCHEMA_VERSION_V3",
    "SUITE_VARIANT_ID",
    "build_portable_bsm_metric_suite",
    "build_portable_bsm_metric_suite_v3",
    "verify_portable_bsm_metric_suite",
    "verify_portable_bsm_metric_suite_v3",
]
