"""Order-insensitive v3 leaf runtime built from the frozen BSM oracle.

The financial calculation is deliberately inherited byte-for-byte from the
v2 leaf source.  The appended v3 layer changes only protocol identities and
submission row handling.  ``leaf_runtime_source`` returns one self-contained
module that can be copied to a task as ``verifier/runtime.py``.
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv import (
    bsm_market_metric_verifier_runtime as _v2_runtime,
)


_V3_RUNTIME_SUFFIX = r'''
# DuckDB-query v3 changes the solver ABI, not the financial calculation.
_DATABASE_SCHEMA_VERSION = "bsm-market-metric-task-duckdb-v2.0.0"
_TASK_VERSION = "2.0.0"

_METRIC_SPECS = (
    _MetricSpec(
        "iv",
        "market_implied_volatility",
        "bsm_market_implied_iv_v1",
        "bsm-mid-iv-bisection80-v1",
        "bsm-market-implied-iv-submission-v2.0.0",
        "quantlib-bsm-market-implied-iv-verifier-v2",
        r"^bsm-market-iv-v2-[0-9a-f]{24}$",
        "positive_decimal8",
        True,
    ),
    _MetricSpec(
        "delta",
        "unit_delta",
        "bsm_market_implied_delta_v1",
        "bsm-mid-iv-bisection80-analytic-delta-v1",
        "bsm-market-implied-delta-submission-v2.0.0",
        "quantlib-bsm-market-implied-delta-verifier-v2",
        r"^bsm-market-delta-v2-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    _MetricSpec(
        "gamma",
        "unit_gamma",
        "bsm_market_implied_gamma_v1",
        "bsm-mid-iv-bisection80-analytic-gamma-v1",
        "bsm-market-implied-gamma-submission-v2.0.0",
        "quantlib-bsm-market-implied-gamma-verifier-v2",
        r"^bsm-market-gamma-v2-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    _MetricSpec(
        "vega_1volpt",
        "unit_vega_1volpt",
        "bsm_market_implied_vega_1volpt_v1",
        "bsm-mid-iv-bisection80-analytic-vega-1volpt-v1",
        "bsm-market-implied-vega-1volpt-submission-v2.0.0",
        "quantlib-bsm-market-implied-vega-1volpt-verifier-v2",
        r"^bsm-market-vega-1volpt-v2-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    _MetricSpec(
        "theta_1calendar_day",
        "unit_theta_1calendar_day",
        "bsm_market_implied_theta_1calendar_day_v1",
        "bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1",
        "bsm-market-implied-theta-1calendar-day-submission-v2.0.0",
        "quantlib-bsm-market-implied-theta-1calendar-day-verifier-v2",
        r"^bsm-market-theta-1calendar-day-v2-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    _MetricSpec(
        "rho_1pct",
        "unit_rho_1pct",
        "bsm_market_implied_rho_1pct_v1",
        "bsm-mid-iv-bisection80-analytic-rho-1pct-v1",
        "bsm-market-implied-rho-1pct-submission-v2.0.0",
        "quantlib-bsm-market-implied-rho-1pct-verifier-v2",
        r"^bsm-market-rho-1pct-v2-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
)
_METRIC_SPEC_BY_TARGET = {spec.target: spec for spec in _METRIC_SPECS}


def _parse_submission_v3(
    submission: Mapping[str, Any],
    spec: _MetricSpec,
    expected_row_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(submission, Mapping) or set(submission) != set(_SUBMISSION_FIELDS):
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
    if not isinstance(rows, list):
        raise ValueError("single-metric submission rows must be an array")
    expected_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_fields.add("iv_status")

    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("single-metric result row has missing or extra fields")
        row_id = row["row_id"]
        if not isinstance(row_id, str) or _ROW_ID_PATTERN.fullmatch(row_id) is None:
            raise ValueError("submission row_id must use row_000001 format")
        if row_id in rows_by_id:
            raise ValueError("single-metric submission contains duplicate row_id")
        if spec.needs_iv_status and row["iv_status"] != _SUCCESS_IV_STATUS:
            raise ValueError("single-metric IV status is invalid")
        _validate_decimal8(row[spec.output_field], spec)
        rows_by_id[row_id] = dict(row)

    if expected_row_ids is None:
        aligned_ids = tuple(rows_by_id)
    else:
        aligned_ids = tuple(expected_row_ids)
        if len(set(aligned_ids)) != len(aligned_ids):
            raise ValueError("trusted expected row IDs are duplicated")
        if set(rows_by_id) != set(aligned_ids):
            raise ValueError("submission row_id set differs from the public task")

    return {
        "task_id": submission["task_id"],
        "submission_schema_version": submission["submission_schema_version"],
        "method_id": submission["method_id"],
        "status": submission["status"],
        "rows": [rows_by_id[row_id] for row_id in aligned_ids],
    }


def validate_market_metric_submission_contract(
    submission: Mapping[str, Any], method_config: Mapping[str, Any]
) -> None:
    """Validate strict v3 fields, identities, values, and row-id uniqueness."""

    spec = _spec_from_config(method_config)
    _parse_submission_v3(submission, spec)


def verify_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any],
) -> None:
    """Key-align by row_id and require exact canonical v3 equality."""

    spec = _spec_from_config(method_config)
    expected = _expected_submission(task_inputs, spec)
    expected_row_ids = tuple(row["row_id"] for row in expected["rows"])
    try:
        actual = _parse_submission_v3(submission, spec, expected_row_ids)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the single-metric schema") from error
    if actual != expected:
        raise ValueError("single-metric canonical submission mismatch")
'''.strip()


def leaf_runtime_source() -> str:
    """Return the standalone v3 runtime source copied into leaf verifiers."""

    base = Path(_v2_runtime.__file__).read_text(encoding="utf-8").rstrip()
    return f"{base}\n\n{_V3_RUNTIME_SUFFIX}\n"


_RUNTIME_MODULE_NAME = f"{__name__}._standalone"
_RUNTIME_MODULE = ModuleType(_RUNTIME_MODULE_NAME)
_RUNTIME_MODULE.__file__ = str(Path(_v2_runtime.__file__))
sys.modules[_RUNTIME_MODULE_NAME] = _RUNTIME_MODULE
exec(
    compile(leaf_runtime_source(), _RUNTIME_MODULE.__file__, "exec"),
    _RUNTIME_MODULE.__dict__,
)
_RUNTIME_NAMESPACE: dict[str, Any] = _RUNTIME_MODULE.__dict__

BSMMarketMetricInput = _RUNTIME_NAMESPACE["BSMMarketMetricInput"]
bsm_metric_logical_checksum = _RUNTIME_NAMESPACE["bsm_metric_logical_checksum"]
digest_file = _RUNTIME_NAMESPACE["digest_file"]
expected_market_metric_submission = _RUNTIME_NAMESPACE[
    "expected_market_metric_submission"
]
load_bsm_market_metric_inputs = _RUNTIME_NAMESPACE["load_bsm_market_metric_inputs"]
validate_market_metric_submission_contract = _RUNTIME_NAMESPACE[
    "validate_market_metric_submission_contract"
]
verify_market_metric_submission = _RUNTIME_NAMESPACE[
    "verify_market_metric_submission"
]

# Authoring helpers intentionally inspect these frozen private identities in the
# same way the existing v2 renderer does.
_METRIC_SPEC_BY_TARGET = _RUNTIME_NAMESPACE["_METRIC_SPEC_BY_TARGET"]
_oracle_config = _RUNTIME_NAMESPACE["_oracle_config"]
_spec_from_config = _RUNTIME_NAMESPACE["_spec_from_config"]


__all__ = [
    "BSMMarketMetricInput",
    "bsm_metric_logical_checksum",
    "digest_file",
    "expected_market_metric_submission",
    "leaf_runtime_source",
    "load_bsm_market_metric_inputs",
    "validate_market_metric_submission_contract",
    "verify_market_metric_submission",
]
