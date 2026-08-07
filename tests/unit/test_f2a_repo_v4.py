from __future__ import annotations

import json
from pathlib import Path


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v4_identity_is_executable_calendar_enabled_and_consistent(
    repository_root: Path,
) -> None:
    variant = _json(
        repository_root / "configs/variants/bsm_arbitrage_finding_f2a_v4.json"
    )
    mutation = _json(repository_root / "configs/mutations/f2a_complete_v4.json")
    dataset = _json(repository_root / "authoring/configs/f2a_dataset_v4.json")
    lineage = _json(repository_root / "schemas/f2a-lineage-v4.schema.json")
    submission = _json(repository_root / "schemas/submission-v4.schema.json")

    assert variant["variant_id"] == "bsm-arbitrage-finding-f2a-v4"
    assert variant["status"] == "EXECUTABLE"
    assert variant["runtime_enabled"] is True
    assert variant["blocking_reasons"] == []
    catalogue = variant["candidate_catalogue"]
    assert catalogue["candidate_catalogue_id"] == "bsm-f2a-candidate-catalogue-v5"
    assert catalogue["status"] == "EXECUTABLE"
    assert catalogue["runtime_enabled"] is True
    assert catalogue["calendar_family"] == (
        "transaction-cost-aware-two-expiry-call-stock-flip-v1"
    )
    assert catalogue["calendar_contract"][
        "candidate_count_per_four_expiry_seven_strike_complete_chain"
    ] == 42
    assert mutation["engine_id"] == "f2a-complete-mutation-v4"
    assert mutation["runtime_enabled"] is True
    assert dataset["dataset_config_id"] == "f2a-dataset-v4"
    assert dataset["variant_id"] == variant["variant_id"]
    assert dataset["candidate_catalogue_id"] == catalogue["candidate_catalogue_id"]
    assert dataset["mutation_engine_id"] == mutation["engine_id"]
    assert dataset["profiles"]["smoke"]["publication_task_count"] > 0
    assert lineage["properties"]["variant_id"]["const"] == variant["variant_id"]
    assert lineage["properties"]["engine_id"]["const"] == mutation["engine_id"]
    assert submission["properties"]["variant_id"]["const"] == variant["variant_id"]
    assert submission["properties"]["output_contract_id"]["const"] == (
        variant["output_contract"]["output_contract_id"]
    )


def test_v4_runtime_files_and_reproducible_fixture_are_tracked(
    repository_root: Path,
) -> None:
    paths = (
        "src/synthetic_derivatives/verifier/f2a_oracle.py",
        "src/synthetic_derivatives/verifier/f2a.py",
        "src/synthetic_derivatives/authoring/f2a_child_materializer.py",
        "src/synthetic_derivatives/mutation/f2a.py",
        "src/synthetic_derivatives/solver/f2a.py",
        "scripts/materialize_f2a.py",
        "tests/fixtures/f2a/v4_parent.json",
        "tests/fixtures/f2a/v4_parent.manifest.json",
        "datasets/manifests/splits/f2a_v4.json",
    )
    assert all((repository_root / path).is_file() for path in paths)
    manifest = _json(repository_root / "tests/fixtures/f2a/v4_parent.manifest.json")
    assert manifest["status"] == "FROZEN"
    assert manifest["complete_chain"] == {
        "expiry_count": 4,
        "strike_count_per_expiry": 7,
        "option_type_count": 2,
        "quote_count": 56,
        "calendar_candidate_count": 42,
    }
    assert manifest["materialization_command"] == (
        "python scripts/materialize_f2a.py build-fixture"
    )
    assert manifest["fixture_role"] == (
        "tracked_ci_only_not_production_parent_fallback"
    )


def test_v4_dependency_lock_installs_jsonschema_and_referencing(
    repository_root: Path,
) -> None:
    root_lock = (repository_root / "requirements.lock").read_text(encoding="utf-8")
    authoring_lock = (
        repository_root / "environments/authoring/requirements.lock"
    ).read_text(encoding="utf-8")
    for lock in (root_lock, authoring_lock):
        assert "jsonschema==4.25.0" in lock
        assert "referencing==0.37.0" in lock
        assert "jsonschema-specifications==2025.9.1" in lock
        assert "rpds-py==0.30.0" in lock
        assert "typing-extensions==4.16.0" in lock


def test_v4_config_has_no_blocked_calendar_sentinels(repository_root: Path) -> None:
    active_paths = (
        repository_root / "configs/variants/bsm_arbitrage_finding_f2a_v4.json",
        repository_root / "authoring/configs/f2a_dataset_v4.json",
    )
    forbidden = (
        '"runtime_enabled": false',
        '"calendar_family": null',
        "BLOCKED_CALENDAR_FAMILY_NULL",
        '"calendar_terminal_guard": null',
        "calendar is specified but not implemented",
    )
    for path in active_paths:
        text = path.read_text(encoding="utf-8")
        assert all(token not in text for token in forbidden)
