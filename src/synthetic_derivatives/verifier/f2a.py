"""Submission and private-lineage verification for executable F2A v4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from synthetic_derivatives.verifier.f2a_oracle import (
    CALENDAR_FAMILY_ID,
    PublicChild,
    load_public_child,
    scan_public_child,
)


V4_VARIANT_ID = "bsm-arbitrage-finding-f2a-v4"
V4_OUTPUT_ID = "arbitrage-opportunity-type-trajectory-v5"


@dataclass(frozen=True)
class VerificationReport:
    accepted: bool
    expected_orm: Mapping[str, Any]
    submitted_orm: Mapping[str, Any]
    realized_signature: str


def _validator(schema_root: Path, schema_name: str) -> Draft202012Validator:
    schema = json.loads((schema_root / schema_name).read_text(encoding="utf-8"))
    trajectory = json.loads(
        (schema_root / "trajectory.schema.json").read_text(encoding="utf-8")
    )
    registry = Registry().with_resource(
        "trajectory.schema.json",
        Resource.from_contents(trajectory),
    )
    return Draft202012Validator(schema, registry=registry)


def verify_submission(
    *,
    public_child_path: str | Path,
    submission: Mapping[str, Any],
    schema_root: str | Path,
    variant_path: str | Path | None = None,
) -> VerificationReport:
    """Validate shape and exact ORM projection against an independent oracle."""

    schema_directory = Path(schema_root)
    _validator(schema_directory, "submission-v4.schema.json").validate(submission)
    if variant_path is not None:
        variant = json.loads(Path(variant_path).read_text(encoding="utf-8"))
        if (
            variant["variant_id"] != V4_VARIANT_ID
            or variant["status"] != "EXECUTABLE"
            or variant["runtime_enabled"] is not True
            or variant["candidate_catalogue"]["runtime_enabled"] is not True
            or variant["candidate_catalogue"]["calendar_family"]
            != CALENDAR_FAMILY_ID
        ):
            raise ValueError("public variant is not the executable calendar-enabled v4")

    child = load_public_child(public_child_path)
    if child.status != "FROZEN":
        raise ValueError("trusted verifier accepts only frozen public children")
    for key, expected in (
        ("task_id", child.task_id),
        ("snapshot_id", child.snapshot_id),
        ("snapshot_revision", child.snapshot_revision),
        ("variant_id", V4_VARIANT_ID),
        ("output_contract_id", V4_OUTPUT_ID),
    ):
        if submission[key] != expected:
            raise ValueError(f"submission {key} does not match the public task")
    oracle = scan_public_child(child)
    expected_orm = oracle.orm_answer()
    submitted_orm = submission["trajectory"]["Outcome"]["orm_answer"]
    if submitted_orm != expected_orm:
        return VerificationReport(
            accepted=False,
            expected_orm=expected_orm,
            submitted_orm=submitted_orm,
            realized_signature=oracle.realized_signature,
        )
    return VerificationReport(
        accepted=True,
        expected_orm=expected_orm,
        submitted_orm=submitted_orm,
        realized_signature=oracle.realized_signature,
    )


def _signature_from_orm(orm: Mapping[str, Any]) -> str:
    types = tuple(orm["arbitrage_type"])
    family_order = ("cross-sectional", "cross-asset", "calendar")
    return "".join("1" if family in types else "0" for family in family_order)


def _calendar_evidence_semantics(
    evidence: Mapping[str, Any],
    child: PublicChild | None,
) -> None:
    if evidence["template_id"] != CALENDAR_FAMILY_ID:
        raise ValueError("calendar evidence uses the wrong template")
    if evidence["setup_boundary_kind"] != "closed":
        raise ValueError("calendar setup boundary must be closed")
    multiplier = float(evidence["contract_multiplier"])
    if not isfinite(multiplier) or multiplier <= 0.0:
        raise ValueError("calendar multiplier must be finite and positive")
    if float(evidence["delta_1_low"]) != 0.0:
        raise ValueError("calendar low-state rebalance must be zero")
    if float(evidence["delta_1_high"]) != -multiplier:
        raise ValueError("calendar high-state rebalance must equal -M")
    if date.fromisoformat(evidence["T1"]) >= date.fromisoformat(evidence["T2"]):
        raise ValueError("calendar evidence requires T1 < T2")
    finite_fields = (
        "strike",
        "contract_multiplier",
        "early_bid_amount_after_fee",
        "late_ask_amount_after_fee",
        "beta_12",
        "funding_factor_12",
        "exposure_buffer_ratio",
        "delta_0",
        "delta_1_low",
        "delta_1_high",
        "initial_surplus_usd",
    )
    if not all(isfinite(float(evidence[field])) for field in finite_fields):
        raise ValueError("calendar numeric evidence must be finite")
    terminal = evidence["terminal_certificate"]
    boolean_fields = (
        "beta_positive",
        "beta_at_most_one",
        "funding_factor_at_least_one",
        "left_boundary_nonnegative",
        "actual_boundary_nonnegative",
        "low_x_cell_nonnegative",
        "high_x_low_y_cell_nonnegative",
        "high_x_high_y_cell_nonnegative",
        "strict_gain_open_set",
        "unsimplified_ledger_replayed",
    )
    if not all(terminal[field] is True for field in boolean_fields):
        raise ValueError("calendar terminal certificate is incomplete or false")
    if child is None:
        return
    matches = [
        market_slice
        for market_slice in child.slices
        if market_slice.valuation_date == evidence["valuation_date"]
        and market_slice.underlying_id == evidence["underlying_id"]
    ]
    if len(matches) != 1:
        raise ValueError("calendar evidence does not resolve one public slice")
    market = matches[0]
    by_id = {quote.option_id: quote for quote in market.quotes}
    try:
        early = by_id[evidence["early_option_id"]]
        late = by_id[evidence["late_option_id"]]
    except KeyError as error:
        raise ValueError("calendar option id is not public") from error
    if (
        early.call_put != "call"
        or late.call_put != "call"
        or early.expiry != evidence["T1"]
        or late.expiry != evidence["T2"]
        or float(early.strike) != float(evidence["strike"])
        or float(late.strike) != float(evidence["strike"])
        or early.multiplier != multiplier
        or late.multiplier != multiplier
        or early.underlying_id != evidence["underlying_id"]
        or late.underlying_id != evidence["underlying_id"]
    ):
        raise ValueError("calendar evidence contracts do not share the frozen key")


def validate_lineage_v4(
    lineage: Mapping[str, Any],
    *,
    schema_root: str | Path,
    public_child: PublicChild | None = None,
) -> None:
    """Apply JSON Schema and cross-field semantic validation to private lineage."""

    _validator(Path(schema_root), "f2a-lineage-v4.schema.json").validate(lineage)
    if lineage["requested_signature"] != lineage["realized_signature"]:
        raise ValueError("accepted lineage must realize its requested signature")
    oracle_signature = _signature_from_orm(lineage["oracle_result"])
    if oracle_signature != lineage["realized_signature"]:
        raise ValueError("lineage signature must be derived from the oracle result")
    evidence = lineage["authoring_guard_evidence"]["candidate_evidence"]
    calendar_entries = [item for item in evidence if item["family"] == "calendar"]
    if len(calendar_entries) != 1:
        raise ValueError("lineage must contain one representative calendar candidate")
    _calendar_evidence_semantics(calendar_entries[0], public_child)
    calendar_bit = lineage["realized_signature"][2] == "1"
    if bool(calendar_entries[0]["is_arbitrage"]) != calendar_bit:
        raise ValueError("calendar bit must come from verified candidate evidence")
