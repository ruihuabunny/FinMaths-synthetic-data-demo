"""Build a portable delivery from a completed BSM Greeks batch run.

The delivery is an agent-task artifact, not an authoring package.  It contains
only the four public task files, static trusted-tool payloads, and the
package-local verifier.  Authoring, reference, view, and dataset artifacts are
intentionally outside the copy allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import tempfile
from typing import Any, Mapping, Sequence

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
    digest_file,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    load_bsm_greeks_contract,
    load_bsm_greeks_inputs,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
    validate_portable_toolset,
)


PORTABLE_BATCH_SCHEMA_VERSION = "bsm-greeks-portable-delivery-batch-v1.0.0"
PORTABLE_TASK_SCHEMA_VERSION = "bsm-greeks-portable-delivery-task-v1.0.0"
PORTABLE_SOURCE_SCHEMA_VERSION = "bsm-greeks-portable-source-manifest-v1.0.0"
PORTABLE_DELIVERY_STATUS = "PORTABLE_VERIFIED"

_EXPECTED_FAMILY = "bsm_greeks"
_EXPECTED_VARIANT = "bsm_market_implied_greeks_v1"
_EXPECTED_RUN_SCHEMA = "bsm-market-implied-greeks-batch-run-v1.0.0"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PUBLIC_FILES = (
    "prompt.md",
    "runtime_contract.json",
    "submission.schema.json",
    "task.duckdb",
)
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
_TOOL_FILES = (
    "toolset.json",
    "payloads/contract.json",
    "payloads/inputs.json",
)
_FORBIDDEN_DELIVERY_PARTS = {
    "authoring_private",
    "dataset",
    "private",
    "reference",
    "views",
}


@dataclass(frozen=True)
class PortableDeliveryBatch:
    """Result of one atomically published portable-delivery build."""

    delivery_root: Path
    manifest: dict[str, Any]
    task_ids: tuple[str, ...]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _require_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is not a portable path identifier")
    if value in {".", ".."}:
        raise ValueError(f"{field} is not a portable path identifier")
    return value


def _require_plain_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required regular file is missing: {path}")


def _require_exact_files(directory: Path, expected: Sequence[str]) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"required directory is missing: {directory}")
    actual: list[str] = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"allowlisted directory contains a symlink: {path}")
        if path.is_file():
            actual.append(path.relative_to(directory).as_posix())
    actual.sort()
    if actual != sorted(expected):
        raise ValueError(f"file allowlist mismatch in {directory}")
    for relative in expected:
        _require_plain_file(directory / relative)


def _copy_allowed_file(source: Path, destination: Path) -> None:
    _require_plain_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _adapt_portable_verifier(task_root: Path) -> None:
    """Point the otherwise unchanged verifier at the portable task manifest."""

    path = task_root / "verifier/test_data_identity.py"
    source = path.read_text(encoding="utf-8")
    old = 'package_root / "manifest.json"'
    new = 'package_root / "delivery_manifest.json"'
    if source.count(old) != 1:
        raise ValueError("source verifier manifest lookup changed")
    path.write_text(source.replace(old, new), encoding="utf-8")


def _artifact_visibility(relative: str) -> str:
    if relative.startswith("public/"):
        return "agent_visible"
    if relative.startswith("trusted_tools/"):
        return "tool_host_only"
    if relative.startswith("verifier/"):
        return "verifier_only"
    if relative == "source_manifest.json":
        return "delivery_audit_only"
    raise ValueError(f"artifact has no declared visibility: {relative}")


def _json_contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _json_contains_absolute_path(key) or _json_contains_absolute_path(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_json_contains_absolute_path(item) for item in value)
    if isinstance(value, str):
        return value.startswith("/") or PureWindowsPath(value).is_absolute()
    return False


def _assert_portable_json(payload: Any, label: str) -> None:
    if _json_contains_absolute_path(payload):
        raise ValueError(f"absolute path leaked into {label}")
    serialized = json.dumps(payload, sort_keys=True)
    if "selector_seed" in serialized or "sampling_seed" in serialized:
        raise ValueError(f"private selector provenance leaked into {label}")


def _source_tasks(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = summary.get("accepted_tasks")
    if not isinstance(raw, list):
        raise ValueError("run summary accepted_tasks must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("accepted task entry must be an object")
        task_id = _require_identifier(item.get("task_id"), "task_id")
        if task_id in result:
            raise ValueError("run summary contains duplicate task IDs")
        if type(item.get("option_row_count")) is not int:
            raise ValueError("accepted task option_row_count must be an integer")
        result[task_id] = item
    return result


def _validate_run_summary(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    expected = {
        "run_schema_version": _EXPECTED_RUN_SCHEMA,
        "status": "completed",
        "task_family": _EXPECTED_FAMILY,
        "variant_id": _EXPECTED_VARIANT,
        "package_root": "packages",
    }
    for field, value in expected.items():
        if summary.get(field) != value:
            raise ValueError(f"source run has an unsupported {field}")
    tasks = _source_tasks(summary)
    requested = summary.get("requested_task_count")
    accepted = summary.get("accepted_task_count")
    unique = summary.get("unique_task_id_count")
    if (
        type(requested) is not int
        or type(accepted) is not int
        or type(unique) is not int
        or requested < 1
        or accepted != requested
        or unique != requested
        or len(tasks) != requested
    ):
        raise ValueError("source run is not a complete unique accepted batch")
    verification = summary.get("verification")
    required_verification = (
        "all_packages_verified_before_dataset_export",
        "all_reference_replays_byte_identical",
        "all_trusted_quantlib_canonical_exact_match",
        "dataset_task_ids_unique",
    )
    if not isinstance(verification, Mapping) or any(
        verification.get(field) is not True for field in required_verification
    ):
        raise ValueError("source run verification is incomplete")
    return tasks


def _select_task_ids(
    source_tasks: Mapping[str, Any],
    requested: Sequence[str] | None,
    expected_task_count: int | None,
) -> tuple[str, ...]:
    if requested is None:
        selected = tuple(sorted(source_tasks))
    else:
        selected = tuple(sorted(_require_identifier(item, "task_id") for item in requested))
        if not selected:
            raise ValueError("task_ids cannot be empty")
        if len(selected) != len(set(selected)):
            raise ValueError("task_ids must be unique")
        missing = sorted(set(selected) - set(source_tasks))
        if missing:
            raise ValueError(f"task_ids are not accepted source tasks: {missing}")
    if expected_task_count is not None:
        if type(expected_task_count) is not int or expected_task_count < 1:
            raise ValueError("expected_task_count must be a positive integer")
        if len(selected) != expected_task_count:
            raise ValueError("selected task count differs from expected_task_count")
    return selected


def _source_manifest(
    *,
    run_summary_path: Path,
    source_package: Path,
    source_package_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    public_artifacts = source_package_manifest.get("artifacts")
    expected_public = {f"public/{name}" for name in _PUBLIC_FILES}
    if not isinstance(public_artifacts, Mapping) or set(public_artifacts) != expected_public:
        raise ValueError("source package public artifact manifest changed")
    payload = {
        "source_manifest_schema_version": PORTABLE_SOURCE_SCHEMA_VERSION,
        "task_id": source_package_manifest["task_id"],
        "source_run": {
            "run_schema_version": _EXPECTED_RUN_SCHEMA,
            "run_summary_digest": digest_file(run_summary_path),
        },
        "source_package": {
            "relative_path": (
                f"packages/{_EXPECTED_VARIANT}/{source_package.name}"
            ),
            "manifest_digest": digest_file(source_package / "manifest.json"),
            "package_schema_version": source_package_manifest["package_schema_version"],
            "build_status": source_package_manifest["build_status"],
        },
        "parent_snapshot": source_package_manifest["parent_snapshot"],
        "public_child_snapshot": source_package_manifest["public_child_snapshot"],
        "public_artifacts": dict(sorted(public_artifacts.items())),
    }
    _assert_portable_json(payload, "source_manifest.json")
    return payload


def _write_trusted_tools(source_package: Path, task_root: Path) -> None:
    """Materialize the package-defined static tool ABI and canonical payloads."""

    # Kept as a local import so the portable-tool ABI module remains independent
    # of delivery orchestration.
    from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
        export_portable_greeks_toolset,
    )

    export_portable_greeks_toolset(
        database=source_package / "public/task.duckdb",
        runtime_contract=source_package / "public/runtime_contract.json",
        submission_schema=source_package / "public/submission.schema.json",
        output_directory=task_root / "trusted_tools",
    )


def _build_task(
    *,
    run_summary_path: Path,
    source_package: Path,
    task_root: Path,
    expected_option_rows: int,
) -> dict[str, Any]:
    source_manifest_path = source_package / "manifest.json"
    _require_plain_file(source_manifest_path)
    manifest = load_json_object(source_manifest_path)
    task_id = source_package.name
    if (
        manifest.get("task_id") != task_id
        or manifest.get("task_family") != _EXPECTED_FAMILY
        or manifest.get("variant_id") != _EXPECTED_VARIANT
        or manifest.get("build_status") not in {"ACCEPTED", "RELEASED"}
    ):
        raise ValueError(f"source package identity is invalid: {task_id}")

    source_public = source_package / "public"
    source_verifier = source_package / "verifier"
    _require_exact_files(source_public, _PUBLIC_FILES)
    _require_exact_files(source_verifier, _VERIFIER_FILES)

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("source package artifacts must be an object")
    for name in _PUBLIC_FILES:
        relative = f"public/{name}"
        if artifacts.get(relative) != digest_file(source_package / relative):
            raise ValueError(f"source public artifact digest mismatch: {relative}")
        _copy_allowed_file(source_package / relative, task_root / relative)
    for name in _VERIFIER_FILES:
        _copy_allowed_file(source_verifier / name, task_root / "verifier" / name)
    _adapt_portable_verifier(task_root)

    _write_trusted_tools(source_package, task_root)
    _require_exact_files(task_root / "trusted_tools", _TOOL_FILES)

    database = task_root / "public/task.duckdb"
    contract_payload = load_json_object(task_root / "trusted_tools/payloads/contract.json")
    inputs_payload = json.loads(
        (task_root / "trusted_tools/payloads/inputs.json").read_text(encoding="utf-8")
    )
    expected_contract = load_bsm_greeks_contract(database)
    expected_inputs = [row.to_tool_mapping() for row in load_bsm_greeks_inputs(database)]
    if contract_payload != expected_contract or inputs_payload != expected_inputs:
        raise ValueError("portable trusted-tool payload differs from task.duckdb")
    if len(expected_inputs) != expected_option_rows:
        raise ValueError("portable input row count differs from the source run")
    if any(row.get("task_id") != task_id for row in expected_inputs):
        raise ValueError("portable inputs contain a different task ID")

    source_payload = _source_manifest(
        run_summary_path=run_summary_path,
        source_package=source_package,
        source_package_manifest=manifest,
    )
    _write_json(task_root / "source_manifest.json", source_payload)

    artifact_paths = sorted(
        path.relative_to(task_root).as_posix()
        for path in task_root.rglob("*")
        if path.is_file()
    )
    expected_paths = sorted(
        [f"public/{name}" for name in _PUBLIC_FILES]
        + [f"verifier/{name}" for name in _VERIFIER_FILES]
        + [f"trusted_tools/{name}" for name in _TOOL_FILES]
        + ["source_manifest.json"]
    )
    if artifact_paths != expected_paths:
        raise ValueError("portable task contains an artifact outside the allowlist")
    artifact_digests = {
        relative: digest_file(task_root / relative) for relative in artifact_paths
    }
    artifact_visibility = {
        relative: _artifact_visibility(relative) for relative in artifact_paths
    }
    delivery_manifest = {
        "task_delivery_schema_version": PORTABLE_TASK_SCHEMA_VERSION,
        "delivery_status": PORTABLE_DELIVERY_STATUS,
        "task_id": task_id,
        "task_family": _EXPECTED_FAMILY,
        "task_version": manifest["task_version"],
        "variant_id": _EXPECTED_VARIANT,
        "source_package_manifest_digest": digest_file(source_manifest_path),
        "parent_snapshot": manifest["parent_snapshot"],
        "public_child_snapshot": manifest["public_child_snapshot"],
        "toolset": {
            "path": "trusted_tools/toolset.json",
            "digest": artifact_digests["trusted_tools/toolset.json"],
        },
        "verifier": manifest["verifier"],
        "artifacts": artifact_digests,
        "artifact_visibility": artifact_visibility,
    }
    _assert_portable_json(delivery_manifest, "delivery_manifest.json")
    _write_json(task_root / "delivery_manifest.json", delivery_manifest)
    return delivery_manifest


def _assert_delivery_tree(delivery_root: Path, manifest: Mapping[str, Any]) -> None:
    task_ids = manifest.get("task_ids")
    if not isinstance(task_ids, list) or task_ids != sorted(task_ids):
        raise ValueError("batch task_ids must be a sorted list")
    expected_files = {"batch_manifest.json"}
    for task_id in task_ids:
        prefix = f"tasks/{task_id}/"
        expected_files.add(prefix + "delivery_manifest.json")
        expected_files.add(prefix + "source_manifest.json")
        expected_files.update(prefix + f"public/{name}" for name in _PUBLIC_FILES)
        expected_files.update(prefix + f"verifier/{name}" for name in _VERIFIER_FILES)
        expected_files.update(prefix + f"trusted_tools/{name}" for name in _TOOL_FILES)
    actual_files: set[str] = set()
    for path in delivery_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("portable delivery cannot contain symlinks")
        if path.is_file():
            relative = path.relative_to(delivery_root).as_posix()
            if set(Path(relative).parts) & _FORBIDDEN_DELIVERY_PARTS:
                raise ValueError("portable delivery contains a forbidden artifact class")
            if path.suffix == ".json":
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"portable JSON artifact is invalid: {relative}"
                    ) from error
                _assert_portable_json(payload, relative)
            actual_files.add(relative)
    if actual_files != expected_files:
        raise ValueError("portable delivery file tree differs from the allowlist")


def verify_portable_bsm_greeks_delivery(
    delivery_root: str | Path,
) -> dict[str, Any]:
    """Verify identities, digests, payload equivalence, and the copy allowlist."""

    root = Path(delivery_root)
    manifest_path = root / "batch_manifest.json"
    _require_plain_file(manifest_path)
    manifest = load_json_object(manifest_path)
    expected_top_level = {
        "batch_delivery_schema_version",
        "delivery_id",
        "delivery_status",
        "task_family",
        "variant_id",
        "source_run",
        "parent_snapshot",
        "split_policy",
        "task_count",
        "task_ids",
        "tasks",
    }
    if set(manifest) != expected_top_level:
        raise ValueError("batch manifest has missing or extra fields")
    if (
        manifest["batch_delivery_schema_version"] != PORTABLE_BATCH_SCHEMA_VERSION
        or manifest["delivery_status"] != PORTABLE_DELIVERY_STATUS
        or manifest["task_family"] != _EXPECTED_FAMILY
        or manifest["variant_id"] != _EXPECTED_VARIANT
    ):
        raise ValueError("batch manifest identity changed")
    _require_identifier(manifest["delivery_id"], "delivery_id")
    _assert_portable_json(manifest, "batch_manifest.json")
    source_run = manifest["source_run"]
    if (
        not isinstance(source_run, dict)
        or set(source_run) != {"run_schema_version", "run_summary_digest"}
        or source_run["run_schema_version"] != _EXPECTED_RUN_SCHEMA
        or not isinstance(source_run["run_summary_digest"], str)
        or not _SHA256.fullmatch(source_run["run_summary_digest"])
    ):
        raise ValueError("portable source run identity is invalid")
    task_ids = manifest["task_ids"]
    if (
        type(manifest["task_count"]) is not int
        or manifest["task_count"] != len(task_ids)
        or len(task_ids) != len(set(task_ids))
        or not task_ids
    ):
        raise ValueError("batch task count or identity is invalid")
    tasks = manifest["tasks"]
    if not isinstance(tasks, list) or len(tasks) != len(task_ids):
        raise ValueError("batch task index is invalid")
    task_entries = {item.get("task_id"): item for item in tasks if isinstance(item, dict)}
    if set(task_entries) != set(task_ids):
        raise ValueError("batch task index does not cover task_ids")

    common_parent = manifest["parent_snapshot"]
    from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
        validate_portable_toolset,
    )

    for task_id in task_ids:
        _require_identifier(task_id, "task_id")
        task_root = root / "tasks" / task_id
        task_manifest_path = task_root / "delivery_manifest.json"
        task_manifest = load_json_object(task_manifest_path)
        if (
            task_manifest.get("task_delivery_schema_version")
            != PORTABLE_TASK_SCHEMA_VERSION
            or task_manifest.get("delivery_status") != PORTABLE_DELIVERY_STATUS
            or task_manifest.get("task_id") != task_id
            or task_manifest.get("parent_snapshot") != common_parent
        ):
            raise ValueError(f"portable task manifest identity changed: {task_id}")
        _assert_portable_json(task_manifest, f"{task_id}/delivery_manifest.json")
        artifacts = task_manifest.get("artifacts")
        visibility = task_manifest.get("artifact_visibility")
        if not isinstance(artifacts, dict) or not isinstance(visibility, dict):
            raise ValueError("portable task artifact maps are invalid")
        if set(artifacts) != set(visibility):
            raise ValueError("portable task artifact visibility coverage changed")
        for relative, expected_digest in artifacts.items():
            path = task_root / relative
            _require_plain_file(path)
            if digest_file(path) != expected_digest:
                raise ValueError(f"portable artifact digest mismatch: {task_id}/{relative}")
            if visibility[relative] != _artifact_visibility(relative):
                raise ValueError(f"portable artifact visibility changed: {task_id}/{relative}")
        validate_portable_toolset(task_root)

        source = load_json_object(task_root / "source_manifest.json")
        _assert_portable_json(source, f"{task_id}/source_manifest.json")
        if source.get("task_id") != task_id:
            raise ValueError("portable source manifest task identity changed")
        if source.get("source_run") != manifest["source_run"]:
            raise ValueError("portable source run identity changed")
        if source.get("parent_snapshot") != common_parent:
            raise ValueError("portable source manifest parent identity changed")
        public_artifacts = source.get("public_artifacts")
        if not isinstance(public_artifacts, dict):
            raise ValueError("portable source public artifacts are invalid")
        for name in _PUBLIC_FILES:
            relative = f"public/{name}"
            if public_artifacts.get(relative) != digest_file(task_root / relative):
                raise ValueError(f"copied public artifact differs from source: {task_id}/{relative}")

        contract = load_json_object(task_root / "trusted_tools/payloads/contract.json")
        inputs = json.loads(
            (task_root / "trusted_tools/payloads/inputs.json").read_text(encoding="utf-8")
        )
        expected_contract = load_bsm_greeks_contract(task_root / "public/task.duckdb")
        expected_inputs = [
            row.to_tool_mapping()
            for row in load_bsm_greeks_inputs(task_root / "public/task.duckdb")
        ]
        if contract != expected_contract or inputs != expected_inputs:
            raise ValueError(f"portable tool payload identity changed: {task_id}")
        if not inputs or any(row.get("task_id") != task_id for row in inputs):
            raise ValueError(f"portable input task identity changed: {task_id}")

        entry = task_entries[task_id]
        if set(entry) != {"task_id", "relative_path", "delivery_manifest_digest"}:
            raise ValueError("batch task entry shape changed")
        if (
            entry["relative_path"] != f"tasks/{task_id}"
            or entry["delivery_manifest_digest"] != digest_file(task_manifest_path)
        ):
            raise ValueError(f"batch task entry identity changed: {task_id}")

    _assert_delivery_tree(root, manifest)
    return manifest


def build_portable_bsm_greeks_delivery(
    *,
    source_run: str | Path,
    output_root: str | Path,
    delivery_id: str,
    task_ids: Sequence[str] | None = None,
    expected_task_count: int | None = None,
) -> PortableDeliveryBatch:
    """Build and atomically publish one non-overwriting portable batch."""

    run_root = Path(source_run)
    if run_root.is_symlink() or not run_root.is_dir():
        raise ValueError("source_run must be a regular directory")
    _require_identifier(run_root.name, "source_run_id")
    run_summary_path = run_root / "run_summary.json"
    _require_plain_file(run_summary_path)
    summary = load_json_object(run_summary_path)
    source_tasks = _validate_run_summary(summary)
    selected = _select_task_ids(source_tasks, task_ids, expected_task_count)
    delivery_name = _require_identifier(delivery_id, "delivery_id")

    packages_root = run_root / "packages" / _EXPECTED_VARIANT
    if packages_root.is_symlink() or not packages_root.is_dir():
        raise ValueError("source run package directory is missing")
    available_package_ids = sorted(
        path.name for path in packages_root.iterdir() if path.is_dir() and not path.is_symlink()
    )
    if available_package_ids != sorted(source_tasks):
        raise ValueError("source run package directories differ from accepted task IDs")

    variant_output = Path(output_root) / _EXPECTED_VARIANT
    output_base = Path(output_root)
    if output_base.exists() and (output_base.is_symlink() or not output_base.is_dir()):
        raise ValueError("output_root must be a regular directory")
    variant_output.mkdir(parents=True, exist_ok=True)
    if variant_output.is_symlink() or not variant_output.is_dir():
        raise ValueError("variant output must be a regular directory")
    destination = variant_output / delivery_name
    lock_path = variant_output / f".{delivery_name}.build.lock"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"portable delivery already exists: {destination}")
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise FileExistsError(f"portable delivery build is already active: {delivery_name}") from error
    os.close(lock_descriptor)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{delivery_name}.staging-", dir=variant_output)
    )
    published = False
    try:
        common_parent: dict[str, Any] | None = None
        task_index: list[dict[str, str]] = []
        for task_id in selected:
            task_manifest = _build_task(
                run_summary_path=run_summary_path,
                source_package=packages_root / task_id,
                task_root=staging / "tasks" / task_id,
                expected_option_rows=source_tasks[task_id]["option_row_count"],
            )
            parent = task_manifest["parent_snapshot"]
            if common_parent is None:
                common_parent = parent
            elif parent != common_parent:
                raise ValueError("portable tasks do not share one parent snapshot")
            task_manifest_path = staging / "tasks" / task_id / "delivery_manifest.json"
            task_index.append(
                {
                    "task_id": task_id,
                    "relative_path": f"tasks/{task_id}",
                    "delivery_manifest_digest": digest_file(task_manifest_path),
                }
            )
        if common_parent is None:
            raise ValueError("portable delivery cannot be empty")
        batch_manifest = {
            "batch_delivery_schema_version": PORTABLE_BATCH_SCHEMA_VERSION,
            "delivery_id": delivery_name,
            "delivery_status": PORTABLE_DELIVERY_STATUS,
            "task_family": _EXPECTED_FAMILY,
            "variant_id": _EXPECTED_VARIANT,
            "source_run": {
                "run_schema_version": summary["run_schema_version"],
                "run_summary_digest": digest_file(run_summary_path),
            },
            "parent_snapshot": common_parent,
            "split_policy": {
                "group": "evaluation",
                "policy": "unsplit_shared_parent_snapshot",
            },
            "task_count": len(selected),
            "task_ids": list(selected),
            "tasks": task_index,
        }
        _assert_portable_json(batch_manifest, "batch_manifest.json")
        _write_json(staging / "batch_manifest.json", batch_manifest)
        verify_portable_bsm_greeks_delivery(staging)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"portable delivery already exists: {destination}")
        os.rename(staging, destination)
        published = True
        verified = verify_portable_bsm_greeks_delivery(destination)
        return PortableDeliveryBatch(
            delivery_root=destination,
            manifest=verified,
            task_ids=selected,
        )
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
        lock_path.unlink(missing_ok=True)
