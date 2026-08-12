from __future__ import annotations

import json
from pathlib import Path

from synthetic_derivatives.packaging.contracts import load_json_object
from synthetic_derivatives.packaging.database import load_bsm_greeks_inputs
from synthetic_derivatives.packaging.runtime import replay_solver_source
from synthetic_derivatives.verifier.bsm_market_greeks import (
    verify_market_greeks_submission,
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


def test_checked_in_golden_package_is_self_consistent(repository_root) -> None:
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
