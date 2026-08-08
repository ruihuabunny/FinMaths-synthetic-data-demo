"""Versioned submission verification for frozen F2A v4 and full v5."""

from __future__ import annotations

from dataclasses import dataclass, field
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
from synthetic_derivatives.verifier.f2a_v5 import (
    build_canonical_answer as build_v5_canonical_answer,
    compare_semantic_layers,
)
from synthetic_derivatives.verifier.f2a_database import (
    iter_market_slices as iter_v5_market_slices,
    load_public_manifest as load_v5_public_manifest,
)
from synthetic_derivatives.verifier.f2a_model_signal import MODEL_SIGNAL_VARIANT_ID


V4_VARIANT_ID = "bsm-arbitrage-finding-f2a-v4"
V4_OUTPUT_ID = "arbitrage-opportunity-type-trajectory-v5"


@dataclass(frozen=True)
class VerificationReport:
    accepted: bool
    expected_orm: Mapping[str, Any]
    submitted_orm: Mapping[str, Any]
    realized_signature: str
    layer_reports: Mapping[str, Any] = field(default_factory=dict)


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


def _verify_v4_submission(
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


def _verify_v5_submission(
    *,
    public_child_path: str | Path,
    submission: Mapping[str, Any],
    schema_root: str | Path,
    variant_path: str | Path | None = None,
) -> VerificationReport:
    schema_directory = Path(schema_root)
    _validator(schema_directory, "submission-v5.schema.json").validate(submission)
    if variant_path is not None:
        variant = json.loads(Path(variant_path).read_text(encoding="utf-8"))
        if (
            variant.get("variant_id") != MODEL_SIGNAL_VARIANT_ID
            or variant.get("status") != "EXECUTABLE"
            or variant.get("runtime_enabled") is not True
        ):
            raise ValueError("public variant is not the executable F2A v5 contract")
    expected = build_v5_canonical_answer(public_child_path)
    layers = compare_semantic_layers(expected, submission)
    accepted = all(layers[name]["accepted"] for name in ("V0", "V1", "V2", "V3"))
    return VerificationReport(
        accepted=accepted,
        expected_orm=expected,
        submitted_orm=submission,
        realized_signature="database-full-scan",
        layer_reports=layers,
    )


def verify_submission(
    *,
    public_child_path: str | Path,
    submission: Mapping[str, Any],
    schema_root: str | Path,
    variant_path: str | Path | None = None,
) -> VerificationReport:
    """Dispatch without changing the frozen v4 verification path."""

    variant_id = submission.get("variant_id")
    if variant_id == V4_VARIANT_ID:
        return _verify_v4_submission(
            public_child_path=public_child_path,
            submission=submission,
            schema_root=schema_root,
            variant_path=variant_path,
        )
    if variant_id == MODEL_SIGNAL_VARIANT_ID:
        return _verify_v5_submission(
            public_child_path=public_child_path,
            submission=submission,
            schema_root=schema_root,
            variant_path=variant_path,
        )
    raise ValueError("unsupported F2A submission variant")


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


def validate_lineage_v5(
    lineage: Mapping[str, Any],
    *,
    schema_root: str | Path,
    public_child_path: str | Path,
) -> None:
    """Validate private authoring QA without using private truth as public truth."""

    _validator(Path(schema_root), "f2a-lineage-v5.schema.json").validate(lineage)
    manifest = load_v5_public_manifest(public_child_path)
    expected = build_v5_canonical_answer(public_child_path)
    if lineage["task_id"] != expected["task_id"]:
        raise ValueError("v5 lineage task id does not match the public child")
    if lineage["child_snapshot_id"] != expected["snapshot_id"]:
        raise ValueError("v5 lineage child identity does not match the public child")
    if lineage["variant_id"] != MODEL_SIGNAL_VARIANT_ID:
        raise ValueError("v5 lineage variant identity is incorrect")
    if lineage["parent_snapshot_id"] != manifest["parent_snapshot_id"]:
        raise ValueError("v5 lineage parent identity does not match the public child")
    if lineage["selected_underlyings"] != manifest["selected_underlyings"]:
        raise ValueError("v5 lineage sample does not match the public child")
    if lineage["public_signal_scan"] != expected["model_signals"]:
        raise ValueError("v5 lineage public signal cache is stale or non-canonical")
    requested = tuple(lineage["requested_signatures"])
    groups = tuple(lineage["mutation_groups"])
    if len(groups) != len(requested):
        raise ValueError("each requested private signature requires one mutation group")
    if tuple(group.get("requested_signature") for group in groups) != requested:
        raise ValueError("private mutation groups do not follow requested signature order")
    private_slices = {
        (item["valuation_date"], item["underlying_id"]): item["signature"]
        for item in lineage["private_truth_scan"].get("slices", [])
    }
    for group, signature in zip(groups, requested):
        key = (group.get("valuation_date"), group.get("underlying_id"))
        realized = private_slices.get(key, "000")
        if group.get("realized_private_signature") != realized:
            raise ValueError("mutation lineage private-truth cache is inconsistent")
        if realized != signature:
            raise ValueError("accepted v5 lineage must realize every requested private signature")

    private_summary = lineage["private_truth_scan"].get("full_scan_summary", {})
    scanned_slice_count = expected["model_signals"]["full_scan_summary"][
        "scanned_slice_count"
    ]
    if private_summary.get("scanned_slice_count") != scanned_slice_count:
        raise ValueError("private truth scan is not a complete child-database scan")
    private_keys = [
        (item["valuation_date"], item["underlying_id"])
        for item in lineage["private_truth_scan"].get("slices", [])
    ]
    if len(private_keys) != len(set(private_keys)):
        raise ValueError("private truth scan contains duplicate slices")

    public_signatures = {
        (item["valuation_date"], item["underlying_id"]): item["signature"]
        for item in expected["model_signals"]["slices"]
    }
    private_signatures = {
        (item["valuation_date"], item["underlying_id"]): item["signature"]
        for item in lineage["private_truth_scan"].get("slices", [])
    }
    audit_rows = lineage["fp_fn_task_audit"].get("slice_audit", [])
    if len(audit_rows) != scanned_slice_count:
        raise ValueError("private FP/FN audit does not cover every public slice")
    seen_audit_keys = set()
    for item in audit_rows:
        key = (item["valuation_date"], item["underlying_id"])
        if key in seen_audit_keys:
            raise ValueError("private FP/FN audit contains duplicate slices")
        seen_audit_keys.add(key)
        public_signature = public_signatures.get(key, "000")
        private_signature = private_signatures.get(key, "000")
        expected_fp = [
            family
            for family, estimate, truth in zip("XUT", public_signature, private_signature)
            if estimate == "1" and truth == "0"
        ]
        expected_fn = [
            family
            for family, estimate, truth in zip("XUT", public_signature, private_signature)
            if estimate == "0" and truth == "1"
        ]
        if (
            item.get("public_signature") != public_signature
            or item.get("private_signature") != private_signature
            or item.get("fp_families") != expected_fp
            or item.get("fn_families") != expected_fn
        ):
            raise ValueError("private FP/FN audit is inconsistent with its scans")
    public_task_signature = "".join(
        "1" if any(item["public_signature"][index] == "1" for item in audit_rows) else "0"
        for index in range(3)
    )
    private_task_signature = "".join(
        "1" if any(item["private_signature"][index] == "1" for item in audit_rows) else "0"
        for index in range(3)
    )
    fp_fn_audit = lineage["fp_fn_task_audit"]
    aggregate_expected = {
        "slice_count": len(audit_rows),
        "exact_signature_count": sum(
            item["public_signature"] == item["private_signature"]
            for item in audit_rows
        ),
        "any_fp": any(item["fp_families"] for item in audit_rows),
        "any_fn": any(item["fn_families"] for item in audit_rows),
        "public_task_signature": public_task_signature,
        "private_task_signature": private_task_signature,
        "task_hamming_error": sum(
            estimate != truth
            for estimate, truth in zip(public_task_signature, private_task_signature)
        ),
    }
    if any(fp_fn_audit.get(key) != value for key, value in aggregate_expected.items()):
        raise ValueError("private FP/FN aggregate fields are inconsistent")

    slices = tuple(
        iter_v5_market_slices(
            public_child_path,
            tuple(manifest["selected_underlyings"]),
        )
    )
    expected_slice_keys = {
        (item.valuation_date, item.underlying_id) for item in slices
    }
    if seen_audit_keys != expected_slice_keys:
        raise ValueError("private FP/FN audit slice keys do not match the public child")
    execution_result = scan_public_child(
        PublicChild(
            task_id=manifest["task_id"],
            snapshot_id=manifest["child_snapshot_id"],
            snapshot_revision=1,
            status="FROZEN",
            slices=slices,
        )
    )
    expected_execution = {
        "signature": execution_result.realized_signature,
        "orm_answer": execution_result.orm_answer(),
        "candidate_count": len(execution_result.candidates),
        "active_candidate_ids": [
            item.candidate_id for item in execution_result.candidates if item.is_arbitrage
        ],
        "claim_scope": "frozen-v4-catalogue-executable-audit",
    }
    if lineage["execution_audit"] != expected_execution:
        raise ValueError("private execution audit does not match the frozen v4 oracle")
    # Deliberately no equality assertion between public and private signatures:
    # their difference is the calibrated FP/FN object required by v5.
