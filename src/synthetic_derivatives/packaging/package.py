"""Build and verify one golden market-implied BSM Greeks agent package."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from synthetic_derivatives.export import (
    SolverDatabaseExportContract,
    canonical_authoring_logical_checksum,
    export_solver_database,
)
from synthetic_derivatives.export.contracts import assert_no_private_leakage
from synthetic_derivatives.packaging.contracts import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_VARIANT_ID,
    BSM_MARKET_GREEKS_VERIFIER_ID,
    EXPECTED_COORDINATES,
    PACKAGE_SCHEMA_VERSION,
    canonical_json_bytes,
    digest_file,
    digest_json,
    load_json_object,
    market_greeks_method_contract,
    oracle_config,
    validate_package_config,
)
from synthetic_derivatives.packaging.database import (
    BSMGreeksDatabaseManifest,
    assert_bsm_greeks_database_safe,
    bsm_greeks_logical_checksum,
    load_bsm_greeks_inputs,
    materialize_bsm_greeks_database,
)
from synthetic_derivatives.packaging.leakage import (
    assert_view_allowlist,
    scan_public_artifacts,
)
from synthetic_derivatives.packaging.prompt_renderer import (
    render_bsm_greeks_prompt,
)
from synthetic_derivatives.packaging.runtime import (
    audit_solver_source,
    compose_runtime_contract,
    replay_solver_source,
)
from synthetic_derivatives.packaging.trajectory import reference_trajectory
from synthetic_derivatives.packaging.views import (
    export_release_views,
    release_view_files,
)
from synthetic_derivatives.tasks.bsm_market_greeks import MarketGreeksSubmission
from synthetic_derivatives.verifier.bsm_market_greeks import (
    trusted_market_greeks_submission,
    verify_market_greeks_submission,
)


@dataclass(frozen=True)
class AgentTaskPackage:
    package_root: Path
    manifest: dict[str, Any]
    database_manifest: BSMGreeksDatabaseManifest


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _write_trajectory(path: Path, events: tuple[Any, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_json_bytes(event.to_dict()) for event in events))


def _source_artifact_paths(package_root: Path) -> list[str]:
    return sorted(
        path.relative_to(package_root).as_posix()
        for directory in ("public", "verifier", "reference", "authoring_private")
        for path in (package_root / directory).rglob("*")
        if path.is_file()
        and path.relative_to(package_root).as_posix()
        != "authoring_private/artifact_manifest.json"
    )


def _write_private_artifact_manifest(package_root: Path) -> None:
    artifacts = {
        relative: digest_file(package_root / relative)
        for relative in _source_artifact_paths(package_root)
    }
    _write_json(
        package_root / "authoring_private/artifact_manifest.json",
        {
            "schema_version": "bsm-greeks-private-artifact-manifest-v1",
            "scope": "all_source_artifacts_except_this_manifest",
            "artifacts": artifacts,
        },
    )


def _verify_private_artifact_manifest(package_root: Path) -> None:
    relative = "authoring_private/artifact_manifest.json"
    manifest = load_json_object(package_root / relative)
    if set(manifest) != {"schema_version", "scope", "artifacts"}:
        raise ValueError("private artifact manifest has missing or extra fields")
    if manifest["schema_version"] != "bsm-greeks-private-artifact-manifest-v1":
        raise ValueError("private artifact manifest version changed")
    if manifest["scope"] != "all_source_artifacts_except_this_manifest":
        raise ValueError("private artifact manifest scope changed")
    expected_paths = _source_artifact_paths(package_root)
    if sorted(manifest["artifacts"]) != expected_paths:
        raise ValueError("private artifact manifest coverage changed")
    for artifact, expected_digest in manifest["artifacts"].items():
        if digest_file(package_root / artifact) != expected_digest:
            raise ValueError(f"private artifact digest mismatch: {artifact}")


def _verifier_sources() -> dict[str, str]:
    return {
        "conftest.py": '''from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def agent_submission() -> dict:
    declared = os.environ.get("BSM_GREEKS_SUBMISSION")
    if not declared:
        raise RuntimeError("BSM_GREEKS_SUBMISSION must identify the agent file")
    return json.loads(Path(declared).read_text(encoding="utf-8"))
''',
        "test_contract.py": '''from synthetic_derivatives.tasks.bsm_market_greeks import MarketGreeksSubmission


def test_submission_contract(agent_submission):
    MarketGreeksSubmission.from_mapping(agent_submission)
''',
        "test_data_identity.py": '''import json

from synthetic_derivatives.packaging.contracts import digest_file
from synthetic_derivatives.packaging.database import bsm_greeks_logical_checksum


def test_public_data_identity(package_root):
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
    database = package_root / "public/task.duckdb"
    assert bsm_greeks_logical_checksum(database) == manifest["public_child_snapshot"]["logical_checksum"]
    assert digest_file(database) == manifest["artifacts"]["public/task.duckdb"]
''',
        "test_semantics.py": '''import json

from synthetic_derivatives.packaging.database import load_bsm_greeks_inputs
from synthetic_derivatives.verifier.bsm_market_greeks import verify_market_greeks_submission


def test_exact_market_greeks(package_root, agent_submission):
    inputs = load_bsm_greeks_inputs(package_root / "public/task.duckdb")
    config = json.loads((package_root / "verifier/oracle_config.json").read_text(encoding="utf-8"))
    verify_market_greeks_submission(inputs, agent_submission, config)
''',
    }


def _write_verifier(package_root: Path) -> None:
    verifier = package_root / "verifier"
    verifier.mkdir(parents=True, exist_ok=True)
    for name, source in _verifier_sources().items():
        (verifier / name).write_text(source, encoding="utf-8")
    _write_json(verifier / "oracle_config.json", oracle_config())


def _public_manifest(
    *,
    repository_root: Path,
    package_root: Path,
    database: BSMGreeksDatabaseManifest,
    runtime: Mapping[str, Any],
    views: dict[str, list[str]],
    build_status: str,
) -> dict[str, Any]:
    public_files = (
        "public/task.duckdb",
        "public/prompt.md",
        "public/runtime_contract.json",
        "public/submission.schema.json",
    )
    runtime_path = package_root / "public/runtime_contract.json"
    oracle_path = package_root / "verifier/oracle_config.json"
    requirements = repository_root / "environments/solver/requirements.lock"
    manifest = {
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "task_id": database.task_id,
        "task_family": "bsm_greeks",
        "task_version": "1.0.0",
        "variant_id": BSM_MARKET_GREEKS_VARIANT_ID,
        "coordinates": EXPECTED_COORDINATES,
        "build_status": build_status,
        "release_profile": "golden-single-task-v1",
        "parent_snapshot": {
            "snapshot_id": database.parent_snapshot_id,
            "revision": database.parent_snapshot_revision,
            "logical_checksum": database.parent_logical_checksum,
        },
        "public_child_snapshot": {
            "snapshot_id": database.child_snapshot_id,
            "revision": 1,
            "logical_checksum": database.public_logical_checksum,
        },
        "valuation_date": database.valuation_date.isoformat(),
        "selected_underlying_ids": list(database.selected_underlyings),
        "joint_market_contract_id": database.joint_market_contract_id,
        "p_dependence_spec_id": database.p_dependence_spec_id,
        "q_dependence_spec_id": database.q_dependence_spec_id,
        "dependence_policy_id": database.dependence_policy_id,
        "method_contract": {
            "id": BSM_MARKET_GREEKS_METHOD_ID,
            "digest": digest_json(market_greeks_method_contract()),
        },
        "submission_schema": {
            "id": BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION,
            "digest": digest_file(package_root / "public/submission.schema.json"),
        },
        "runtime_environment": {
            "environment_id": runtime["environment_id"],
            "profile_id": runtime["profile_id"],
            "lock_id": "solver-requirements-sha256-" + digest_file(requirements),
            "contract_digest": digest_file(runtime_path),
        },
        "verifier": {
            "id": BSM_MARKET_GREEKS_VERIFIER_ID,
            "digest": digest_file(oracle_path),
        },
        "artifacts": {
            relative: digest_file(package_root / relative) for relative in public_files
        },
        "views": views,
    }
    assert_no_private_leakage(manifest, "manifest")
    return manifest


def _validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    expected = {
        "package_schema_version",
        "task_id",
        "task_family",
        "task_version",
        "variant_id",
        "coordinates",
        "build_status",
        "release_profile",
        "parent_snapshot",
        "public_child_snapshot",
        "valuation_date",
        "selected_underlying_ids",
        "joint_market_contract_id",
        "p_dependence_spec_id",
        "q_dependence_spec_id",
        "dependence_policy_id",
        "method_contract",
        "submission_schema",
        "runtime_environment",
        "verifier",
        "artifacts",
        "views",
    }
    if set(manifest) != expected:
        raise ValueError("package manifest has missing or extra fields")
    if (
        manifest["package_schema_version"] != PACKAGE_SCHEMA_VERSION
        or manifest["variant_id"] != BSM_MARKET_GREEKS_VARIANT_ID
        or manifest["coordinates"] != EXPECTED_COORDINATES
        or manifest["build_status"] not in {"ACCEPTED", "RELEASED"}
    ):
        raise ValueError("package manifest changes a frozen identity")
    underlyings = manifest["selected_underlying_ids"]
    if (
        not isinstance(underlyings, list)
        or len(underlyings) != 8
        or underlyings != sorted(set(underlyings))
    ):
        raise ValueError("package manifest requires eight ordered underlyings")


def build_bsm_greeks_package(
    *,
    repository_root: str | Path,
    parent_database: str | Path,
    output_root: str | Path,
    package_config_path: str | Path,
    build_status: str = "ACCEPTED",
) -> AgentTaskPackage:
    """Build, replay, verify, scan, and atomically publish one golden package."""

    repository = Path(repository_root).resolve()
    parent = Path(parent_database).resolve()
    output = Path(output_root).resolve()
    config = load_json_object(package_config_path)
    validate_package_config(config)
    if build_status not in {"ACCEPTED", "RELEASED"}:
        raise ValueError("golden package build status must be ACCEPTED or RELEASED")
    global_profile = load_json_object(repository / config["global_capability_profile"])
    overlay = load_json_object(repository / config["task_capability_overlay"])
    runtime = compose_runtime_contract(global_profile, overlay)
    prompt = render_bsm_greeks_prompt(market_greeks_method_contract(), runtime)
    source_solver = repository / "src/synthetic_derivatives/packaging/reference_solver.py"
    audit_solver_source(source_solver.read_text(encoding="utf-8"), runtime)
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bsm-greeks-package-", dir=output))
    parent_bytes_before = digest_file(parent)
    try:
        for directory in ("public", "reference/artifacts", "authoring_private"):
            (staging / directory).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bsm-greeks-public-market-") as temp:
            # Avoid a DuckDB catalog name that collides with the parent's
            # private ``market`` schema while the source is attached read-only.
            market_database = Path(temp) / "solver_public.duckdb"
            source_manifest = export_solver_database(
                parent,
                market_database,
                SolverDatabaseExportContract(
                    variant_id=BSM_MARKET_GREEKS_VARIANT_ID,
                    valuation_date=date.fromisoformat(config["valuation_date"]),
                    sampling_seed=config["private_selection"]["sampling_seed"],
                    sample_size=config["private_selection"]["underlying_count"],
                ),
            )
            database_manifest = materialize_bsm_greeks_database(
                market_database,
                staging / "public/task.duckdb",
                source_manifest,
            )
        if digest_file(parent) != parent_bytes_before:
            raise RuntimeError("packaging changed the frozen parent bytes")
        (staging / "public/prompt.md").write_text(prompt, encoding="utf-8")
        _write_json(staging / "public/runtime_contract.json", runtime)
        shutil.copyfile(
            repository / config["submission_schema"],
            staging / "public/submission.schema.json",
        )
        shutil.copyfile(source_solver, staging / "reference/artifacts/solver.py")
        _write_verifier(staging)

        with tempfile.TemporaryDirectory(prefix="bsm-greeks-replay-") as replay_root:
            first = replay_solver_source(
                source_path=staging / "reference/artifacts/solver.py",
                database=staging / "public/task.duckdb",
                submission_directory=Path(replay_root) / "first",
                runtime_contract=runtime,
            )
            second = replay_solver_source(
                source_path=staging / "reference/artifacts/solver.py",
                database=staging / "public/task.duckdb",
                submission_directory=Path(replay_root) / "second",
                runtime_contract=runtime,
            )
        if first.submission_bytes != second.submission_bytes or first != second:
            raise RuntimeError("independent solver replays are not byte-identical")
        (staging / "reference/final_submission.json").write_bytes(
            first.submission_bytes
        )
        inputs = load_bsm_greeks_inputs(staging / "public/task.duckdb")
        trusted = trusted_market_greeks_submission(inputs, oracle_config())
        if first.submission != trusted.to_dict():
            raise RuntimeError("reference solver differs from the QuantLib verifier")
        verify_market_greeks_submission(inputs, first.submission, oracle_config())
        _write_json(staging / "authoring_private/oracle_answer.json", trusted.to_dict())
        _write_trajectory(
            staging / "reference/trajectory.jsonl", reference_trajectory(first)
        )
        _write_json(
            staging / "reference/artifacts/input_digest.json",
            {"task_id": database_manifest.task_id, "input_digest": first.input_digest},
        )
        _write_json(
            staging / "reference/artifacts/self_check.json",
            {
                "status": "passed",
                "row_count": len(inputs),
                "tool_calls": first.tool_calls,
                "submission_digest": first.submission_digest,
                "schema_exact": True,
                "row_order_exact": True,
            },
        )
        _write_json(
            staging / "authoring_private/parent_identity.json",
            {
                "snapshot_id": database_manifest.parent_snapshot_id,
                "snapshot_revision": database_manifest.parent_snapshot_revision,
                "logical_checksum": database_manifest.parent_logical_checksum,
                "byte_digest_before_after": parent_bytes_before,
            },
        )
        _write_json(
            staging / "authoring_private/sample_manifest.json",
            {
                "private_selection": config["private_selection"],
                "generic_public_child": source_manifest.to_dict(),
                "task_database": database_manifest.to_dict(),
            },
        )
        _write_json(
            staging / "authoring_private/build_report.json",
            {
                "status": "accepted",
                "reference_replay_count": 2,
                "reference_replay_byte_identical": True,
                "trusted_quantlib_exact_match": True,
                "runtime_policy_valid": True,
                "contract_digest": first.contract_digest,
                "input_digest": first.input_digest,
                "submission_digest": first.submission_digest,
            },
        )
        leakage = scan_public_artifacts(staging)
        _write_json(staging / "authoring_private/leakage_report.json", leakage)
        _write_private_artifact_manifest(staging)
        views = release_view_files(staging)
        manifest = _public_manifest(
            repository_root=repository,
            package_root=staging,
            database=database_manifest,
            runtime=runtime,
            views=views,
            build_status=build_status,
        )
        _validate_manifest_shape(manifest)
        _write_json(staging / "manifest.json", manifest)
        export_release_views(staging, views)
        final = output / BSM_MARKET_GREEKS_VARIANT_ID / database_manifest.task_id
        if final.exists():
            raise FileExistsError(f"refusing to overwrite task package: {final}")
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
        return AgentTaskPackage(final, manifest, database_manifest)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_bsm_greeks_package(
    package_root: str | Path,
    *,
    repository_root: str | Path,
) -> None:
    """Recheck identities, drift, reference semantics, hashes, and release views."""

    root = Path(package_root)
    repository = Path(repository_root)
    manifest = load_json_object(root / "manifest.json")
    _validate_manifest_shape(manifest)
    for relative, expected in manifest["artifacts"].items():
        if digest_file(root / relative) != expected:
            raise ValueError(f"package artifact digest mismatch: {relative}")
    _verify_private_artifact_manifest(root)
    database = root / "public/task.duckdb"
    assert_bsm_greeks_database_safe(database)
    if bsm_greeks_logical_checksum(database) != manifest["public_child_snapshot"][
        "logical_checksum"
    ]:
        raise ValueError("package database logical checksum mismatch")
    runtime = load_json_object(root / "public/runtime_contract.json")
    config = load_json_object(
        repository / "configs/task_packages/bsm_market_implied_greeks_v1.json"
    )
    expected_runtime = compose_runtime_contract(
        load_json_object(repository / config["global_capability_profile"]),
        load_json_object(repository / config["task_capability_overlay"]),
    )
    if runtime != expected_runtime:
        raise ValueError("effective runtime contract drifted")
    expected_prompt = render_bsm_greeks_prompt(
        market_greeks_method_contract(), runtime
    )
    if (root / "public/prompt.md").read_text(encoding="utf-8") != expected_prompt:
        raise ValueError("prompt drifted from method/runtime contracts")
    reference = load_json_object(root / "reference/final_submission.json")
    inputs = load_bsm_greeks_inputs(database)
    verify_market_greeks_submission(inputs, reference, oracle_config())
    if MarketGreeksSubmission.from_mapping(reference).to_dict() != reference:
        raise ValueError("reference submission is not canonical")
    scan_public_artifacts(root)
    for view_name, files in manifest["views"].items():
        view_root = root / "views" / view_name
        assert_view_allowlist(view_root, files)
        for relative in files:
            if (view_root / relative).read_bytes() != (root / relative).read_bytes():
                raise ValueError(
                    f"release view artifact differs from source: {view_name}/{relative}"
                )
    evaluation_files = set(manifest["views"]["evaluation"])
    if any(
        item.startswith(("verifier/", "reference/", "authoring_private/"))
        for item in evaluation_files
    ):
        raise ValueError("evaluation view exposes a hidden package directory")


__all__ = [
    "AgentTaskPackage",
    "build_bsm_greeks_package",
    "verify_bsm_greeks_package",
]
