from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb
import QuantLib
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import load_json_object
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import load_bsm_greeks_inputs
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import replay_solver_source
from synthetic_derivatives.verifier.bsm_market_greeks import (
    verify_market_greeks_submission,
)


def _isolated_verifier_run(
    package_root: Path,
    submission: Path,
) -> subprocess.CompletedProcess[str]:
    site_directories = sorted(
        {
            str(Path(module.__file__).resolve().parent.parent)
            for module in (duckdb, QuantLib, pytest)
        }
    )
    runner = f'''import builtins
import sys

for directory in {site_directories!r}:
    sys.path.append(directory)

original_import = builtins.__import__

def isolated_import(name, *args, **kwargs):
    if name.partition(".")[0] == "synthetic_derivatives":
        raise ModuleNotFoundError("isolated package cannot import synthetic_derivatives")
    return original_import(name, *args, **kwargs)

builtins.__import__ = isolated_import
import pytest
raise SystemExit(pytest.main(["-q", "verifier"]))
'''
    environment = {
        "BSM_GREEKS_SUBMISSION": str(submission.resolve()),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", runner],
        cwd=package_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_views_match_exact_allowlists(packaged_bsm_greeks) -> None:
    root = packaged_bsm_greeks.package.package_root
    manifest = load_json_object(root / "manifest.json")
    evaluation = set(manifest["views"]["evaluation"])
    train_dev = set(manifest["views"]["train_dev"])
    authoring = set(manifest["views"]["authoring"])

    assert evaluation == {
        "manifest.json",
        "public/prompt.md",
        "public/runtime_contract.json",
        "public/submission.schema.json",
        "public/task.duckdb",
    }
    assert not any(
        path.startswith(("verifier/", "reference/", "authoring_private/"))
        for path in evaluation
    )
    assert any(path.startswith("reference/") for path in train_dev)
    assert not any(
        path.startswith(("verifier/", "authoring_private/"))
        for path in train_dev
    )
    assert any(path.startswith("verifier/") for path in authoring)
    assert any(path.startswith("authoring_private/") for path in authoring)
    assert "authoring_private/artifact_manifest.json" in authoring
    assert {
        "verifier/runtime.py",
        "verifier/requirements.lock",
        "verifier/README.md",
    } <= authoring
    assert not any(
        path.startswith("verifier/") for path in evaluation | train_dev
    )

    private_manifest = load_json_object(
        root / "authoring_private/artifact_manifest.json"
    )
    source_files = {
        path.relative_to(root).as_posix()
        for directory in ("public", "verifier", "reference", "authoring_private")
        for path in (root / directory).rglob("*")
        if path.is_file()
        and path.name != "artifact_manifest.json"
    }
    assert set(private_manifest["artifacts"]) == source_files

    for view_name, files in manifest["views"].items():
        for relative in files:
            assert (root / "views" / view_name / relative).read_bytes() == (
                root / relative
            ).read_bytes()


def test_packaged_verifier_has_no_project_runtime_dependency(
    packaged_bsm_greeks,
) -> None:
    root = packaged_bsm_greeks.package.package_root
    verifier = root / "verifier"
    for source_path in verifier.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert "synthetic_derivatives" not in imported_roots
    assert (verifier / "requirements.lock").read_bytes() == (
        packaged_bsm_greeks.repository_root
        / "environments/verifier/requirements.lock"
    ).read_bytes()


def test_copied_package_verifier_runs_without_the_project_install(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    delivery = tmp_path / "delivery"
    shutil.copytree(
        packaged_bsm_greeks.package.package_root,
        delivery,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    accepted = _isolated_verifier_run(
        delivery,
        delivery / "reference/final_submission.json",
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "3 passed" in accepted.stdout

    authoring = delivery / "views/authoring"
    authoring_accepted = _isolated_verifier_run(
        authoring,
        authoring / "reference/final_submission.json",
    )
    assert authoring_accepted.returncode == 0, (
        authoring_accepted.stdout + authoring_accepted.stderr
    )

    rejected_submission = json.loads(
        (delivery / "reference/final_submission.json").read_text(encoding="utf-8")
    )
    original = rejected_submission["rows"][0]["unit_delta"]
    rejected_submission["rows"][0]["unit_delta"] = (
        original[:-1] + ("0" if original[-1] != "0" else "1")
    )
    rejected_path = tmp_path / "rejected-submission.json"
    rejected_path.write_text(
        json.dumps(rejected_submission, sort_keys=True),
        encoding="utf-8",
    )
    rejected = _isolated_verifier_run(delivery, rejected_path)
    output = rejected.stdout + rejected.stderr
    assert rejected.returncode != 0
    assert "canonical submission mismatch" in output
    assert "ModuleNotFoundError" not in output
    assert "ImportError while importing" not in output


def test_reference_trajectory_contains_only_observable_events(
    packaged_bsm_greeks,
) -> None:
    path = packaged_bsm_greeks.package.package_root / "reference/trajectory.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [event["step_id"] for event in events] == list(range(1, 11))
    assert events[-1]["event_type"] == "submission"
    assert events[-1]["tool_name"] == "submit_greeks_submission_v1"
    serialized = json.dumps(events, sort_keys=True).casefold()
    assert "chain-of-thought" not in serialized
    assert "hidden reasoning" not in serialized
    assert "oracle_answer" not in serialized


def test_clean_evaluation_view_is_solvable_through_trusted_tools(
    packaged_bsm_greeks, tmp_path: Path
) -> None:
    root = packaged_bsm_greeks.package.package_root
    evaluation = root / "views/evaluation"
    runtime = load_json_object(evaluation / "public/runtime_contract.json")
    result = replay_solver_source(
        source_path=root / "reference/artifacts/solver.py",
        database=evaluation / "public/task.duckdb",
        submission_directory=tmp_path / "evaluation-submission",
        runtime_contract=runtime,
    )

    assert result.submission_bytes == (
        root / "reference/final_submission.json"
    ).read_bytes()
    verify_market_greeks_submission(
        load_bsm_greeks_inputs(evaluation / "public/task.duckdb"),
        result.submission,
    )


def test_checked_in_golden_package_is_self_consistent(
    repository_root,
) -> None:
    packages = list(
        (
            repository_root
            / "task_packages/bsm_market_implied_greeks_v1"
        ).glob("bsm-mig-v1-*")
    )
    assert len(packages) == 1
    manifest = load_json_object(packages[0] / "manifest.json")
    assert manifest["build_status"] == "ACCEPTED"
    assert manifest["task_id"] == packages[0].name
    assert (packages[0] / "verifier/runtime.py").is_file()
    assert (packages[0] / "verifier/requirements.lock").read_bytes() == (
        repository_root / "environments/verifier/requirements.lock"
    ).read_bytes()
