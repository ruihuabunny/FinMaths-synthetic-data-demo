from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    METRIC_VERIFIER_FILENAMES,
    expected_metric_submission,
    expected_metric_submission_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (
    verify_portable_bsm_metric_suite,
    verify_portable_bsm_metric_suite_v3,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "tests/fixtures"
_BASELINE = json.loads(
    (_FIXTURE_ROOT / "bsm_metric_leaf_verifier_20260819_baseline.json").read_text(
        encoding="utf-8"
    )
)
_MODULAR = json.loads(
    (
        _FIXTURE_ROOT
        / "bsm_metric_leaf_verifier_20260819_modular_deliveries.json"
    ).read_text(encoding="utf-8")
)
_PROFILES = tuple(_MODULAR["deliveries"])
_CHANGED_LEAF_PATHS = {
    "delivery_manifest.json",
    "verifier/README.md",
    "verifier/runtime.py",
    "verifier/test_contract.py",
    "verifier/test_data_identity.py",
    "verifier/test_semantics.py",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _tree_identity(root: Path) -> tuple[str, int]:
    hasher = sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(_file_digest(path).encode("ascii"))
        hasher.update(b"\n")
    return hasher.hexdigest(), len(files)


def _suite_root(fixture: dict[str, Any], profile: str) -> Path:
    return _REPOSITORY_ROOT / fixture["deliveries"][profile]["relative_path"]


def _file_map(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def _without_delivery_manifest_digest(
    assignment: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in assignment.items()
        if key != "delivery_manifest_digest"
    }


@pytest.mark.parametrize("profile", _PROFILES)
def test_modular_delivery_has_frozen_identity_and_passes_full_validation(
    profile: str,
) -> None:
    expected = _MODULAR["deliveries"][profile]
    root = _suite_root(_MODULAR, profile)
    manifest_path = root / "suite_manifest.json"
    manifest = _load_json(manifest_path)

    assert _file_digest(manifest_path) == expected["suite_manifest_sha256"]
    assert _tree_identity(root) == (
        expected["tree_sha256"],
        expected["file_count"],
    )
    assert manifest["delivery_id"] == expected["delivery_id"]
    assert manifest["suite_id"] == expected["suite_id"]
    assert manifest["suite_schema_version"] == expected["suite_schema_version"]
    assert manifest["assignment_digest"] == expected["assignment_digest"]
    assert manifest["total_task_count"] == 24
    assert manifest["source_run"]["run_summary_digest"] == _MODULAR[
        "source_run_summary_digest"
    ]

    validator = (
        verify_portable_bsm_metric_suite_v3
        if profile == "duckdb_query_v3"
        else verify_portable_bsm_metric_suite
    )
    verified = validator(root)
    assert verified == manifest


@pytest.mark.parametrize("profile", _PROFILES)
def test_new_delivery_changes_only_modular_verifier_and_binding_paths(
    profile: str,
) -> None:
    old_root = _suite_root(_BASELINE, profile)
    new_root = _suite_root(_MODULAR, profile)
    old_manifest = _load_json(old_root / "suite_manifest.json")
    new_manifest = _load_json(new_root / "suite_manifest.json")

    assert new_manifest["assignment_digest"] == old_manifest["assignment_digest"]
    assert new_manifest["source_run"] == old_manifest["source_run"]
    assert len(old_manifest["assignments"]) == len(new_manifest["assignments"]) == 24

    expected_submission: Callable[[Path, dict[str, Any]], dict[str, Any]] = (
        expected_metric_submission_v3
        if profile == "duckdb_query_v3"
        else expected_metric_submission
    )
    added = {f"verifier/{path}" for path in _MODULAR["added_verifier_paths"]}
    for old_assignment, new_assignment in zip(
        old_manifest["assignments"],
        new_manifest["assignments"],
        strict=True,
    ):
        assert _without_delivery_manifest_digest(
            new_assignment
        ) == _without_delivery_manifest_digest(old_assignment)

        old_leaf = old_root / old_assignment["relative_path"]
        new_leaf = new_root / new_assignment["relative_path"]
        old_files = _file_map(old_leaf)
        new_files = _file_map(new_leaf)
        common = set(old_files) & set(new_files)
        changed = {
            relative
            for relative in common
            if old_files[relative].read_bytes() != new_files[relative].read_bytes()
        }
        assert changed == _CHANGED_LEAF_PATHS
        assert set(new_files) - set(old_files) == added
        assert set(old_files) - set(new_files) == set()

        # These bytes are the market/input and protocol contracts Phase 6 freezes.
        for relative in common - _CHANGED_LEAF_PATHS:
            assert new_files[relative].read_bytes() == old_files[relative].read_bytes()

        config = _load_json(new_leaf / "verifier/oracle_config.json")
        assert config == _load_json(old_leaf / "verifier/oracle_config.json")
        assert expected_submission(new_leaf, config) == expected_submission(
            old_leaf, config
        )

        delivery_manifest = _load_json(new_leaf / "delivery_manifest.json")
        for verifier_path in METRIC_VERIFIER_FILENAMES:
            relative = f"verifier/{verifier_path}"
            assert delivery_manifest["artifacts"][relative] == _file_digest(
                new_leaf / relative
            )
            assert (
                delivery_manifest["artifact_visibility"][relative]
                == "verifier_only"
            )


@pytest.mark.parametrize("profile", _PROFILES)
def test_representative_modular_delivery_leaf_runs_isolated(
    tmp_path: Path, profile: str
) -> None:
    root = _suite_root(_MODULAR, profile)
    assignment = _load_json(root / "suite_manifest.json")["assignments"][0]
    leaf = root / assignment["relative_path"]
    config = _load_json(leaf / "verifier/oracle_config.json")
    expected_submission = (
        expected_metric_submission_v3
        if profile == "duckdb_query_v3"
        else expected_metric_submission
    )
    submission = expected_submission(leaf, config)
    if profile == "duckdb_query_v3":
        submission = deepcopy(submission)
        submission["rows"].reverse()
    submission_path = tmp_path / f"{profile}-submission.json"
    submission_path.write_bytes(canonical_json_bytes(submission))

    environment = os.environ.copy()
    environment["BSM_GREEKS_SUBMISSION"] = str(submission_path)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "verifier",
        ],
        cwd=leaf,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "3 passed" in completed.stdout
