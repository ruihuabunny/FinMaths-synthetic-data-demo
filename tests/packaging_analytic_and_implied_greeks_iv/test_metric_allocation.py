from __future__ import annotations

from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
    digest_file,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    market_content_digest,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_allocation import (
    ALLOCATION_POLICY_ID,
    TARGET_ORDER,
    MetricSourceCandidate,
    allocate_metric_candidates,
    load_source_metric_candidates,
    metric_allocation_rank,
    validate_metric_assignments,
)


_ALLOCATION_ID = "metric-allocation-test-v1"


def _copy_database(source: Path, destination: Path) -> Path:
    shutil.copyfile(source, destination)
    return destination


def _execute(database: Path, *statements: str) -> None:
    connection = duckdb.connect(str(database))
    try:
        for statement in statements:
            connection.execute(statement)
    finally:
        connection.close()


def _candidates(count: int = 30) -> tuple[MetricSourceCandidate, ...]:
    return tuple(
        MetricSourceCandidate(
            source_task_id=f"source-task-{index:03d}",
            source_database_path=Path(f"tasks/source-task-{index:03d}/task.duckdb"),
            source_database_file_digest=sha256(
                f"source-file-{index}".encode("utf-8")
            ).hexdigest(),
            source_market_content_digest=sha256(
                f"source-market-{index}".encode("utf-8")
            ).hexdigest(),
        )
        for index in range(count)
    )


def _explicit(assignments) -> list[dict[str, str]]:
    return [
        {"target": item.target, "source_task_id": item.source_task_id}
        for item in assignments
    ]


def test_market_content_digest_ignores_task_snapshot_and_row_identity(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    source = packaged_bsm_greeks.package.package_root / "public/task.duckdb"
    baseline = market_content_digest(source)

    renamed_task = _copy_database(source, tmp_path / "renamed-task.duckdb")
    _execute(
        renamed_task,
        "UPDATE metadata.public_task SET task_id = 'renamed-task'",
        (
            "UPDATE solver_visible.underlying_market_inputs "
            "SET task_id = 'renamed-task'"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs "
            "SET task_id = 'renamed-task'"
        ),
    )
    assert digest_file(renamed_task) != digest_file(source)
    assert market_content_digest(renamed_task) == baseline

    renamed_snapshot = _copy_database(source, tmp_path / "renamed-snapshot.duckdb")
    _execute(
        renamed_snapshot,
        "UPDATE metadata.public_task SET snapshot_id = 'renamed-snapshot'",
        (
            "UPDATE solver_visible.underlying_market_inputs "
            "SET snapshot_id = 'renamed-snapshot'"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs "
            "SET snapshot_id = 'renamed-snapshot'"
        ),
    )
    assert market_content_digest(renamed_snapshot) == baseline

    renamed_rows = _copy_database(source, tmp_path / "renamed-rows.duckdb")
    _execute(
        renamed_rows,
        (
            "UPDATE solver_visible.option_quote_inputs "
            "SET row_id = 'position_' || row_id"
        ),
    )
    assert market_content_digest(renamed_rows) == baseline


def test_market_content_digest_ignores_physical_row_order(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    source = packaged_bsm_greeks.package.package_root / "public/task.duckdb"
    reordered = _copy_database(source, tmp_path / "reordered.duckdb")
    _execute(
        reordered,
        (
            "CREATE TABLE solver_visible.reordered_underlyings AS "
            "SELECT * FROM solver_visible.underlying_market_inputs "
            "ORDER BY valuation_date DESC, underlying_id DESC"
        ),
        "DROP TABLE solver_visible.underlying_market_inputs",
        (
            "ALTER TABLE solver_visible.reordered_underlyings "
            "RENAME TO underlying_market_inputs"
        ),
        (
            "CREATE TABLE solver_visible.reordered_options AS "
            "SELECT * FROM solver_visible.option_quote_inputs "
            "ORDER BY valuation_date DESC, underlying_id DESC, expiry DESC, "
            "strike DESC, call_put DESC, option_id DESC"
        ),
        "DROP TABLE solver_visible.option_quote_inputs",
        (
            "ALTER TABLE solver_visible.reordered_options "
            "RENAME TO option_quote_inputs"
        ),
    )
    assert digest_file(reordered) != digest_file(source)
    assert market_content_digest(reordered) == market_content_digest(source)


@pytest.mark.parametrize(
    "statement",
    (
        (
            "UPDATE solver_visible.underlying_market_inputs "
            "SET spot = spot + 0.01000000 WHERE underlying_id = "
            "(SELECT min(underlying_id) FROM "
            "solver_visible.underlying_market_inputs)"
        ),
        (
            "UPDATE solver_visible.underlying_market_inputs "
            "SET risk_free_rate = risk_free_rate + 0.0001 WHERE underlying_id = "
            "(SELECT min(underlying_id) FROM "
            "solver_visible.underlying_market_inputs)"
        ),
        (
            "UPDATE solver_visible.underlying_market_inputs "
            "SET dividend_yield = dividend_yield + 0.0001 WHERE underlying_id = "
            "(SELECT min(underlying_id) FROM "
            "solver_visible.underlying_market_inputs)"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs SET strike = strike + 0.01000000 "
            "WHERE row_id = 'row_000001'"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs SET bid = bid + 0.01000000 "
            "WHERE row_id = 'row_000001'"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs SET ask = ask + 0.01000000 "
            "WHERE row_id = 'row_000001'"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs SET expiry = expiry + 1 "
            "WHERE row_id = 'row_000001'"
        ),
        (
            "UPDATE solver_visible.option_quote_inputs "
            "SET contract_multiplier = contract_multiplier + 1.00000000 "
            "WHERE row_id = 'row_000001'"
        ),
    ),
)
def test_market_content_digest_changes_with_economic_or_contract_fields(
    packaged_bsm_greeks,
    tmp_path: Path,
    statement: str,
) -> None:
    source = packaged_bsm_greeks.package.package_root / "public/task.duckdb"
    changed = _copy_database(
        source,
        tmp_path / f"changed-{sha256(statement.encode()).hexdigest()[:8]}.duckdb",
    )
    _execute(changed, statement)
    assert market_content_digest(changed) != market_content_digest(source)


def test_market_content_digest_rejects_solver_visible_schema_drift(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    source = packaged_bsm_greeks.package.package_root / "public/task.duckdb"
    changed = _copy_database(source, tmp_path / "schema-drift.duckdb")
    _execute(
        changed,
        "ALTER TABLE solver_visible.option_quote_inputs DROP COLUMN settlement_type",
    )
    with pytest.raises(ValueError, match="column contract changed"):
        market_content_digest(changed)


def test_sha256_rank_and_round_robin_allocation_are_exact_and_deterministic() -> None:
    candidates = _candidates()
    first = allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)
    second = allocate_metric_candidates(
        tuple(reversed(candidates)), allocation_id=_ALLOCATION_ID
    )

    expected_ranked = sorted(
        candidates,
        key=lambda candidate: (
            sha256(
                canonical_json_bytes(
                    {
                        "allocation_policy_id": ALLOCATION_POLICY_ID,
                        "allocation_id": _ALLOCATION_ID,
                        "source_task_id": candidate.source_task_id,
                        "source_market_content_digest": (
                            candidate.source_market_content_digest
                        ),
                    }
                )
            ).hexdigest(),
            candidate.source_task_id,
        ),
    )[:24]

    assert first == second
    assert len(first) == 24
    assert [item.source_task_id for item in first] == [
        item.source_task_id for item in expected_ranked
    ]
    assert [item.target for item in first] == list(TARGET_ORDER) * 4
    assert [item.round_index for item in first] == [
        round_index for round_index in range(1, 5) for _ in TARGET_ORDER
    ]
    assert Counter(item.target for item in first) == Counter(
        {target: 4 for target in TARGET_ORDER}
    )
    assert len({item.source_task_id for item in first}) == 24
    assert len({item.source_database_file_digest for item in first}) == 24
    assert len({item.source_market_content_digest for item in first}) == 24


def test_metric_allocation_rank_matches_frozen_formula() -> None:
    candidate = _candidates(1)[0]
    expected = sha256(
        canonical_json_bytes(
            {
                "allocation_policy_id": ALLOCATION_POLICY_ID,
                "allocation_id": _ALLOCATION_ID,
                "source_task_id": candidate.source_task_id,
                "source_market_content_digest": (
                    candidate.source_market_content_digest
                ),
            }
        )
    ).hexdigest()
    assert metric_allocation_rank(
        allocation_id=_ALLOCATION_ID,
        source_task_id=candidate.source_task_id,
        source_market_content_digest=candidate.source_market_content_digest,
    ) == expected


def test_metric_allocator_fails_closed_with_fewer_than_24_candidates() -> None:
    with pytest.raises(ValueError, match="at least 24 unique source candidates"):
        allocate_metric_candidates(_candidates(23), allocation_id=_ALLOCATION_ID)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("source_task_id", "source task IDs"),
        ("source_database_file_digest", "source database file digests"),
        ("source_market_content_digest", "source market-content digests"),
    ),
)
def test_metric_allocator_rejects_duplicate_source_identities(
    field: str,
    message: str,
) -> None:
    candidates = list(_candidates())
    candidates[-1] = replace(
        candidates[-1],
        **{field: getattr(candidates[0], field)},
    )
    with pytest.raises(ValueError, match=message):
        allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)


def test_explicit_assignment_is_validated_and_canonically_ordered() -> None:
    candidates = _candidates()
    generated = allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)
    validated = validate_metric_assignments(
        list(reversed(_explicit(generated))),
        candidates,
        allocation_id=_ALLOCATION_ID,
    )
    assert validated == generated


def test_explicit_assignment_rejects_cross_target_source_reuse() -> None:
    candidates = _candidates()
    explicit = _explicit(
        allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)
    )
    explicit[-1]["source_task_id"] = explicit[0]["source_task_id"]
    with pytest.raises(ValueError, match="source task IDs must be unique"):
        validate_metric_assignments(
            explicit,
            candidates,
            allocation_id=_ALLOCATION_ID,
        )


def test_explicit_assignment_rejects_wrong_target_counts() -> None:
    candidates = _candidates()
    explicit = _explicit(
        allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)
    )
    explicit[0]["target"] = "delta"
    with pytest.raises(ValueError, match="required count per target"):
        validate_metric_assignments(
            explicit,
            candidates,
            allocation_id=_ALLOCATION_ID,
        )


def test_explicit_assignment_rejects_unknown_source_task() -> None:
    candidates = _candidates()
    explicit = _explicit(
        allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)
    )
    explicit[0]["source_task_id"] = "source-task-missing"
    with pytest.raises(ValueError, match="not in the candidate pool"):
        validate_metric_assignments(
            explicit,
            candidates,
            allocation_id=_ALLOCATION_ID,
        )


def test_metric_assignment_object_cannot_tamper_with_authenticated_digests() -> None:
    candidates = _candidates()
    assignments = list(
        allocate_metric_candidates(candidates, allocation_id=_ALLOCATION_ID)
    )
    assignments[0] = replace(
        assignments[0],
        source_database_file_digest=assignments[1].source_database_file_digest,
    )
    with pytest.raises(ValueError, match="source identity is invalid"):
        validate_metric_assignments(
            assignments,
            candidates,
            allocation_id=_ALLOCATION_ID,
        )


def test_completed_v2_run_loader_authenticates_package_manifest_and_database(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    source_package = packaged_bsm_greeks.package.package_root
    source_manifest = json.loads(
        (source_package / "manifest.json").read_text(encoding="utf-8")
    )
    task_id = source_manifest["task_id"]
    source_run = tmp_path / "completed-run"
    destination = (
        source_run
        / "packages/bsm_market_implied_greeks_v1"
        / task_id
    )
    shutil.copytree(source_package, destination)
    summary = {
        "run_schema_version": "bsm-market-implied-greeks-batch-run-v2.0.0",
        "status": "completed",
        "task_family": "bsm_greeks",
        "variant_id": "bsm_market_implied_greeks_v1",
        "requested_task_count": 1,
        "accepted_task_count": 1,
        "unique_task_id_count": 1,
        "package_root": "packages",
        "accepted_tasks": [
            {
                "task_index": 1,
                "task_id": task_id,
                "sampling_seed": 0,
                "selected_underlying_ids": source_manifest[
                    "selected_underlying_ids"
                ],
                "option_row_count": 160,
            }
        ],
        "verification": {
            "all_packages_verified_before_dataset_export": True,
            "all_reference_replays_byte_identical": True,
            "all_trusted_quantlib_canonical_exact_match": True,
            "dataset_task_ids_unique": True,
            "dataset_record_count": 1,
        },
    }
    (source_run / "run_summary.json").write_bytes(canonical_json_bytes(summary))
    candidates = load_source_metric_candidates(
        source_run,
        expected_source_task_count=1,
    )
    assert len(candidates) == 1
    assert candidates[0].source_task_id == task_id
    assert candidates[0].source_database_file_digest == digest_file(
        destination / "public/task.duckdb"
    )
    assert candidates[0].source_market_content_digest == market_content_digest(
        destination / "public/task.duckdb"
    )

    source_manifest["artifacts"]["public/task.duckdb"] = "0" * 64
    (destination / "manifest.json").write_bytes(
        canonical_json_bytes(source_manifest)
    )
    with pytest.raises(ValueError, match="source task database digest mismatch"):
        load_source_metric_candidates(
            source_run,
            expected_source_task_count=1,
        )
