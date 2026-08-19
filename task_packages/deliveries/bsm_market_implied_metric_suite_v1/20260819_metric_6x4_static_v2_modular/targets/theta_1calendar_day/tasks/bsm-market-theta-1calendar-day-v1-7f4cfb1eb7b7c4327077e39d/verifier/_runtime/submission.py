"""Ordered static-v2 submission contract for one BSM metric leaf."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from .models import (
    BSMMarketMetricInput,
    _SUBMISSION_FIELDS,
    _validate_decimal8,
)
from .oracle import _expected_submission
from .profile import (
    STATIC_V2_PROFILE,
    MetricSpec,
    _SUCCESS_IV_STATUS,
    _spec_from_config,
)


def _parse_submission(
    submission: Mapping[str, Any], spec: MetricSpec
) -> dict[str, Any]:
    if not isinstance(submission, Mapping) or set(submission) != set(
        _SUBMISSION_FIELDS
    ):
        raise ValueError("single-metric submission has missing or extra fields")
    if (
        not isinstance(submission["task_id"], str)
        or re.fullmatch(spec.task_id_pattern, submission["task_id"]) is None
        or submission["submission_schema_version"] != spec.submission_schema_version
        or submission["method_id"] != spec.method_id
        or submission["status"] != "completed"
    ):
        raise ValueError("single-metric submission identity is invalid")
    rows = submission["rows"]
    if not isinstance(rows, list) or len(rows) != 160:
        raise ValueError("single-metric submission must contain exactly 160 rows")
    expected_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_fields.add("iv_status")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("single-metric result row has missing or extra fields")
        if row["row_id"] != f"row_{index:06d}":
            raise ValueError("submission rows are missing, duplicated, or reordered")
        if spec.needs_iv_status and row["iv_status"] != _SUCCESS_IV_STATUS:
            raise ValueError("single-metric IV status is invalid")
        _validate_decimal8(row[spec.output_field], spec)
    return {
        "task_id": submission["task_id"],
        "submission_schema_version": submission["submission_schema_version"],
        "method_id": submission["method_id"],
        "status": submission["status"],
        "rows": [dict(row) for row in rows],
    }


def validate_market_metric_submission_contract(
    submission: Mapping[str, Any], method_config: Mapping[str, Any]
) -> None:
    """Validate exact static-v2 fields, identity, row count, and order."""

    spec = _spec_from_config(method_config, profile=STATIC_V2_PROFILE)
    _parse_submission(submission, spec)


def verify_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any],
) -> None:
    """Recompute one ordered target and require exact canonical equality."""

    spec = _spec_from_config(method_config, profile=STATIC_V2_PROFILE)
    expected = _expected_submission(task_inputs, spec)
    try:
        actual = _parse_submission(submission, spec)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the single-metric schema") from error
    if actual != expected:
        raise ValueError("single-metric canonical submission mismatch")


__all__ = [
    "validate_market_metric_submission_contract",
    "verify_market_metric_submission",
]
