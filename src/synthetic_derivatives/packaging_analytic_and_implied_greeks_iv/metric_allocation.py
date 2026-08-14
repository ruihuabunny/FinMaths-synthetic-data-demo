"""Deterministic, without-replacement allocation of BSM metric source markets."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

import duckdb

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    BSM_MARKET_GREEKS_VARIANT_ID,
    BSM_MARKET_GREEKS_TASK_VERSION,
    PACKAGE_SCHEMA_VERSION,
    canonical_json_bytes,
    digest_file,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    assert_bsm_greeks_database_safe,
    market_content_digest,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    TARGET_ORDER,
)


ALLOCATION_POLICY_ID = "sha256-rank-round-robin-without-replacement-v1"
DEFAULT_TASKS_PER_TARGET = 4
DEFAULT_SOURCE_TASK_COUNT = 24

_EXPECTED_TASK_FAMILY = "bsm_greeks"
_EXPECTED_RUN_SCHEMA_VERSION = "bsm-market-implied-greeks-batch-run-v2.0.0"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class MetricSourceCandidate:
    """One authenticated source database eligible for metric projection."""

    source_task_id: str
    source_database_path: Path
    source_database_file_digest: str
    source_market_content_digest: str


@dataclass(frozen=True, slots=True)
class MetricAssignment:
    """One source market assigned to exactly one metric target."""

    target: str
    round_index: int
    allocation_rank: str
    source_task_id: str
    source_database_path: Path
    source_database_file_digest: str
    source_market_content_digest: str

    def to_dict(self) -> dict[str, Any]:
        """Return the portable audit fields, excluding the host filesystem path."""

        return {
            "target": self.target,
            "round_index": self.round_index,
            "allocation_rank": self.allocation_rank,
            "source_task_id": self.source_task_id,
            "source_database_digest": self.source_database_file_digest,
            "source_market_content_digest": self.source_market_content_digest,
        }


def _require_regular_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be a regular directory")


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file")


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is not a valid identifier")
    if value in {".", ".."}:
        raise ValueError(f"{label} is not a valid identifier")
    return value


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_candidate_pool(
    candidates: Sequence[MetricSourceCandidate],
) -> tuple[MetricSourceCandidate, ...]:
    materialized = tuple(candidates)
    if not materialized:
        raise ValueError("metric source candidate pool cannot be empty")
    for candidate in materialized:
        if not isinstance(candidate, MetricSourceCandidate):
            raise TypeError("metric source candidates must be MetricSourceCandidate values")
        _require_identifier(candidate.source_task_id, "source_task_id")
        if not isinstance(candidate.source_database_path, Path):
            raise TypeError("source_database_path must be a pathlib.Path")
        _require_sha256(
            candidate.source_database_file_digest,
            "source_database_file_digest",
        )
        _require_sha256(
            candidate.source_market_content_digest,
            "source_market_content_digest",
        )
    uniqueness_fields = (
        (
            "source task IDs",
            [candidate.source_task_id for candidate in materialized],
        ),
        (
            "source database file digests",
            [candidate.source_database_file_digest for candidate in materialized],
        ),
        (
            "source market-content digests",
            [candidate.source_market_content_digest for candidate in materialized],
        ),
    )
    for label, values in uniqueness_fields:
        if len(values) != len(set(values)):
            raise ValueError(f"metric source candidate {label} must be unique")
    return materialized


def load_source_metric_candidates(
    source_run: str | Path,
    *,
    expected_source_task_count: int = DEFAULT_SOURCE_TASK_COUNT,
) -> tuple[MetricSourceCandidate, ...]:
    """Load the authenticated candidate pool from one completed v2 source run."""

    if (
        type(expected_source_task_count) is not int
        or expected_source_task_count < 1
    ):
        raise ValueError("expected_source_task_count must be a positive integer")
    root = Path(source_run)
    _require_regular_directory(root, "source_run")
    summary_path = root / "run_summary.json"
    _require_regular_file(summary_path, "source run summary")
    summary = load_json_object(summary_path)
    if (
        summary.get("run_schema_version") != _EXPECTED_RUN_SCHEMA_VERSION
        or summary.get("status") != "completed"
        or summary.get("task_family") != _EXPECTED_TASK_FAMILY
        or summary.get("variant_id") != BSM_MARKET_GREEKS_VARIANT_ID
        or summary.get("package_root") != "packages"
    ):
        raise ValueError("source run summary identity is invalid")
    requested_count = summary.get("requested_task_count")
    accepted_count = summary.get("accepted_task_count")
    unique_count = summary.get("unique_task_id_count")
    if (
        type(requested_count) is not int
        or type(accepted_count) is not int
        or type(unique_count) is not int
        or requested_count != expected_source_task_count
        or accepted_count != expected_source_task_count
        or unique_count != expected_source_task_count
    ):
        raise ValueError("source run task counts are invalid")
    verification = summary.get("verification")
    required_verification = {
        "all_packages_verified_before_dataset_export": True,
        "all_reference_replays_byte_identical": True,
        "all_trusted_quantlib_canonical_exact_match": True,
        "dataset_task_ids_unique": True,
        "dataset_record_count": expected_source_task_count,
    }
    if not isinstance(verification, Mapping) or any(
        verification.get(field) != expected
        for field, expected in required_verification.items()
    ):
        raise ValueError("source run verification is incomplete")

    entries = summary.get("accepted_tasks")
    if not isinstance(entries, list) or len(entries) != expected_source_task_count:
        raise ValueError("source run accepted task index is invalid")
    by_task_id: dict[str, Mapping[str, Any]] = {}
    task_indexes: set[int] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "task_index",
            "task_id",
            "sampling_seed",
            "selected_underlying_ids",
            "option_row_count",
        }:
            raise ValueError("source run accepted task entry shape is invalid")
        task_id = _require_identifier(entry["task_id"], "accepted source task ID")
        if task_id in by_task_id:
            raise ValueError("source run contains duplicate accepted task IDs")
        task_index = entry["task_index"]
        selected_underlyings = entry["selected_underlying_ids"]
        if (
            type(task_index) is not int
            or type(entry["sampling_seed"]) is not int
            or entry["sampling_seed"] < 0
            or type(entry["option_row_count"]) is not int
            or entry["option_row_count"] != 160
            or not isinstance(selected_underlyings, list)
            or len(selected_underlyings) != 8
            or len(selected_underlyings) != len(set(selected_underlyings))
            or not all(isinstance(item, str) and item for item in selected_underlyings)
        ):
            raise ValueError("source run accepted task metadata is invalid")
        task_indexes.add(task_index)
        by_task_id[task_id] = entry
    if task_indexes != set(range(1, expected_source_task_count + 1)):
        raise ValueError("source run accepted task indexes are not contiguous")
    task_ids = sorted(by_task_id)

    packages_root = root / "packages" / BSM_MARKET_GREEKS_VARIANT_ID
    _require_regular_directory(packages_root, "source package directory")
    actual_package_ids = sorted(
        path.name
        for path in packages_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    )
    if actual_package_ids != task_ids or any(
        path.is_symlink() or not path.is_dir() for path in packages_root.iterdir()
    ):
        raise ValueError("source package directories differ from accepted task IDs")

    candidates: list[MetricSourceCandidate] = []
    for task_id in task_ids:
        package_root = packages_root / task_id
        package_manifest_path = package_root / "manifest.json"
        database_path = package_root / "public/task.duckdb"
        _require_regular_file(package_manifest_path, "source package manifest")
        _require_regular_file(database_path, "source task database")
        package_manifest = load_json_object(package_manifest_path)
        if (
            package_manifest.get("package_schema_version") != PACKAGE_SCHEMA_VERSION
            or package_manifest.get("build_status") != "ACCEPTED"
            or package_manifest.get("task_family") != _EXPECTED_TASK_FAMILY
            or package_manifest.get("task_version")
            != BSM_MARKET_GREEKS_TASK_VERSION
            or package_manifest.get("variant_id") != BSM_MARKET_GREEKS_VARIANT_ID
            or package_manifest.get("task_id") != task_id
            or package_manifest.get("selected_underlying_ids")
            != by_task_id[task_id]["selected_underlying_ids"]
        ):
            raise ValueError(f"source package manifest identity is invalid: {task_id}")
        artifacts = package_manifest.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise ValueError(f"source package artifact map is invalid: {task_id}")
        database_digest = digest_file(database_path)
        if artifacts.get("public/task.duckdb") != database_digest:
            raise ValueError(f"source task database digest mismatch: {task_id}")

        assert_bsm_greeks_database_safe(database_path)
        connection = duckdb.connect(str(database_path), read_only=True)
        try:
            database_task_id = connection.execute(
                "SELECT task_id FROM metadata.public_task"
            ).fetchone()[0]
        finally:
            connection.close()
        if database_task_id != task_id:
            raise ValueError(f"source task database identity mismatch: {task_id}")
        candidates.append(
            MetricSourceCandidate(
                source_task_id=task_id,
                source_database_path=database_path,
                source_database_file_digest=database_digest,
                source_market_content_digest=market_content_digest(database_path),
            )
        )

    return _validate_candidate_pool(candidates)


def metric_allocation_rank(
    *,
    allocation_id: str,
    source_task_id: str,
    source_market_content_digest: str,
) -> str:
    """Return the frozen SHA-256 candidate rank for one source market."""

    _require_identifier(allocation_id, "allocation_id")
    _require_identifier(source_task_id, "source_task_id")
    _require_sha256(source_market_content_digest, "source_market_content_digest")
    return sha256(
        canonical_json_bytes(
            {
                "allocation_policy_id": ALLOCATION_POLICY_ID,
                "allocation_id": allocation_id,
                "source_task_id": source_task_id,
                "source_market_content_digest": source_market_content_digest,
            }
        )
    ).hexdigest()


def _ranked_candidates(
    candidates: Sequence[MetricSourceCandidate],
    allocation_id: str,
) -> tuple[tuple[str, MetricSourceCandidate], ...]:
    validated = _validate_candidate_pool(candidates)
    ranked = (
        (
            metric_allocation_rank(
                allocation_id=allocation_id,
                source_task_id=candidate.source_task_id,
                source_market_content_digest=candidate.source_market_content_digest,
            ),
            candidate,
        )
        for candidate in validated
    )
    return tuple(sorted(ranked, key=lambda item: (item[0], item[1].source_task_id)))


def validate_metric_assignments(
    assignments: Sequence[MetricAssignment | Mapping[str, Any]],
    candidates: Sequence[MetricSourceCandidate],
    *,
    allocation_id: str,
    tasks_per_target: int = DEFAULT_TASKS_PER_TARGET,
) -> tuple[MetricAssignment, ...]:
    """Validate and canonically order an explicit metric assignment."""

    if type(tasks_per_target) is not int or tasks_per_target < 1:
        raise ValueError("tasks_per_target must be a positive integer")
    _require_identifier(allocation_id, "allocation_id")
    candidate_pool = _validate_candidate_pool(candidates)
    by_task_id = {candidate.source_task_id: candidate for candidate in candidate_pool}
    expected_count = len(TARGET_ORDER) * tasks_per_target
    materialized = tuple(assignments)
    if len(materialized) != expected_count:
        raise ValueError(f"metric assignment must contain exactly {expected_count} entries")

    selected: list[tuple[str, MetricSourceCandidate]] = []
    for assignment in materialized:
        if isinstance(assignment, MetricAssignment):
            target = assignment.target
            source_task_id = assignment.source_task_id
        elif isinstance(assignment, Mapping):
            if set(assignment) != {"target", "source_task_id"}:
                raise ValueError("explicit metric assignment entry shape is invalid")
            target = assignment["target"]
            source_task_id = assignment["source_task_id"]
        else:
            raise TypeError("metric assignments must be mappings or MetricAssignment values")
        if target not in TARGET_ORDER:
            raise ValueError(f"unsupported metric assignment target: {target!r}")
        _require_identifier(source_task_id, "assigned source_task_id")
        candidate = by_task_id.get(source_task_id)
        if candidate is None:
            raise ValueError(f"assigned source task is not in the candidate pool: {source_task_id}")
        if isinstance(assignment, MetricAssignment):
            expected_rank = metric_allocation_rank(
                allocation_id=allocation_id,
                source_task_id=source_task_id,
                source_market_content_digest=candidate.source_market_content_digest,
            )
            if (
                assignment.source_database_path != candidate.source_database_path
                or assignment.source_database_file_digest
                != candidate.source_database_file_digest
                or assignment.source_market_content_digest
                != candidate.source_market_content_digest
                or assignment.allocation_rank != expected_rank
            ):
                raise ValueError("metric assignment source identity is invalid")
        selected.append((target, candidate))

    task_ids = [candidate.source_task_id for _, candidate in selected]
    database_digests = [
        candidate.source_database_file_digest for _, candidate in selected
    ]
    content_digests = [
        candidate.source_market_content_digest for _, candidate in selected
    ]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("metric assignment source task IDs must be unique")
    if len(database_digests) != len(set(database_digests)):
        raise ValueError("metric assignment source database digests must be unique")
    if len(content_digests) != len(set(content_digests)):
        raise ValueError("metric assignment market-content digests must be unique")
    counts = Counter(target for target, _ in selected)
    if counts != Counter({target: tasks_per_target for target in TARGET_ORDER}):
        raise ValueError("metric assignment must contain the required count per target")

    grouped: dict[str, list[tuple[str, MetricSourceCandidate]]] = {
        target: [] for target in TARGET_ORDER
    }
    for target, candidate in selected:
        rank = metric_allocation_rank(
            allocation_id=allocation_id,
            source_task_id=candidate.source_task_id,
            source_market_content_digest=candidate.source_market_content_digest,
        )
        grouped[target].append((rank, candidate))
    for target in TARGET_ORDER:
        grouped[target].sort(key=lambda item: (item[0], item[1].source_task_id))

    result: list[MetricAssignment] = []
    for round_index in range(1, tasks_per_target + 1):
        for target in TARGET_ORDER:
            rank, candidate = grouped[target][round_index - 1]
            result.append(
                MetricAssignment(
                    target=target,
                    round_index=round_index,
                    allocation_rank=rank,
                    source_task_id=candidate.source_task_id,
                    source_database_path=candidate.source_database_path,
                    source_database_file_digest=(
                        candidate.source_database_file_digest
                    ),
                    source_market_content_digest=(
                        candidate.source_market_content_digest
                    ),
                )
            )
    return tuple(result)


def allocate_metric_candidates(
    candidates: Sequence[MetricSourceCandidate],
    *,
    allocation_id: str,
    tasks_per_target: int = DEFAULT_TASKS_PER_TARGET,
) -> tuple[MetricAssignment, ...]:
    """SHA-256-rank candidates and assign the first N by round robin."""

    if type(tasks_per_target) is not int or tasks_per_target < 1:
        raise ValueError("tasks_per_target must be a positive integer")
    ranked = _ranked_candidates(candidates, allocation_id)
    required = len(TARGET_ORDER) * tasks_per_target
    if len(ranked) < required:
        raise ValueError(
            f"at least {required} unique source candidates are required for allocation"
        )
    selected = ranked[:required]
    explicit = [
        {
            "target": TARGET_ORDER[index % len(TARGET_ORDER)],
            "source_task_id": candidate.source_task_id,
        }
        for index, (_, candidate) in enumerate(selected)
    ]
    return validate_metric_assignments(
        explicit,
        candidates,
        allocation_id=allocation_id,
        tasks_per_target=tasks_per_target,
    )


__all__ = [
    "ALLOCATION_POLICY_ID",
    "DEFAULT_SOURCE_TASK_COUNT",
    "DEFAULT_TASKS_PER_TARGET",
    "MetricAssignment",
    "MetricSourceCandidate",
    "TARGET_ORDER",
    "allocate_metric_candidates",
    "load_source_metric_candidates",
    "metric_allocation_rank",
    "validate_metric_assignments",
]
