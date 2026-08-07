from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from synthetic_derivatives.authoring.f2a_child_materializer import (
    authoring_scan_market_slice,
    find_spec_for_signature,
    load_frozen_parent_fixture,
    load_frozen_parent_duckdb,
    materialize_verified_child,
)
from synthetic_derivatives.mutation.f2a import (
    MutationSpec,
    apply_mutation,
)
from synthetic_derivatives.solver.f2a import build_submission, solve_public_child
from synthetic_derivatives.verifier.f2a import (
    validate_lineage_v4,
    verify_submission,
)
from synthetic_derivatives.verifier.f2a_oracle import scan_market_slice


def _parent(repository_root: Path):
    return load_frozen_parent_fixture(
        repository_root / "tests/fixtures/f2a/v4_parent.json",
        repository_root / "tests/fixtures/f2a/v4_parent.manifest.json",
    )


def _materialize_001(repository_root: Path, tmp_path: Path):
    parent = _parent(repository_root)
    spec = find_spec_for_signature(parent.slices[0], "001")
    child_path = tmp_path / "child.json"
    lineage_path = tmp_path / "lineage.json"
    child, lineage = materialize_verified_child(
        parent=parent,
        spec=spec,
        requested_signature="001",
        task_id="f2a-v4-ci-smoke-a",
        output_path=child_path,
        lineage_path=lineage_path,
    )
    return child, lineage, child_path, lineage_path


def test_real_tick_audit_records_all_observed_signatures_and_windows(
    repository_root: Path,
) -> None:
    artifact = json.loads(
        (repository_root / "datasets/manifests/splits/f2a_v4.json").read_text(
            encoding="utf-8"
        )
    )
    audit = artifact["reachability_audit"]
    assert artifact["publication_task_count"] == 8
    assert artifact["publication_signatures"] == [
        "000",
        "100",
        "010",
        "001",
        "110",
        "101",
        "011",
        "111",
    ]
    assert all(result["reachable"] for result in audit["results"].values())
    assert all(
        result["trusted_verifier_signature"] == signature
        for signature, result in audit["results"].items()
    )
    assert audit["results"]["001"]["operator"] == (
        "mutate_call_put_pair_equal_shift_v2"
    )
    assert audit["results"]["001"]["target_ids"] == [
        "F2A-CI-2026-09-02-100-CALL",
        "F2A-CI-2026-09-02-100-PUT",
    ]
    assert audit["results"]["001"]["exact_integer_tick_intervals"] == [
        {
            "signature": "001",
            "minimum_absolute_tick": 132,
            "maximum_absolute_tick": 290,
        }
    ]


def test_production_parent_selection_fails_without_explicit_files(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="explicit frozen F2A parent"):
        load_frozen_parent_duckdb(
            tmp_path / "parent.duckdb",
            tmp_path / "parent.manifest.json",
            valuation_date="2026-08-03",
            underlying_id="SYNTH-METAL-GOLD",
        )


def test_001_is_found_by_real_grouped_quote_mutation_and_full_oracles(
    repository_root: Path,
) -> None:
    parent = _parent(repository_root)
    market = parent.slices[0]
    spec = find_spec_for_signature(market, "001")
    assert spec.kind == "call_put_pair_equal_shift"
    assert spec.delta_ticks == 256
    mutated = apply_mutation(market, spec).market_slice
    authoring = authoring_scan_market_slice(mutated)
    trusted = scan_market_slice(mutated)
    assert authoring.realized_signature == "001"
    assert trusted.realized_signature == "001"
    assert trusted.orm_answer() == {
        "arbitrage_opportunity": True,
        "arbitrage_type": ["calendar"],
    }
    active_calendar = [
        item
        for item in trusted.candidates
        if item.family == "calendar" and item.is_arbitrage
    ]
    assert active_calendar
    assert not any(
        item.is_arbitrage and item.family != "calendar"
        for item in trusted.candidates
    )


@pytest.mark.parametrize("signature", ["101", "011", "111"])
def test_mixed_calendar_signatures_are_real_full_oracle_results(
    repository_root: Path,
    signature: str,
) -> None:
    market = _parent(repository_root).slices[0]
    spec = find_spec_for_signature(market, signature)
    mutated = apply_mutation(market, spec).market_slice
    assert authoring_scan_market_slice(mutated).realized_signature == signature
    assert scan_market_slice(mutated).realized_signature == signature


def test_tick_window_boundaries_are_observed_not_affine_trigger_predictions(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    option_ids = (
        "F2A-CI-2026-09-02-100-CALL",
        "F2A-CI-2026-09-02-100-PUT",
    )
    for tick, expected in ((131, "000"), (132, "001"), (290, "001"), (291, "101")):
        spec = MutationSpec(
            operator_id="mutate_call_put_pair_equal_shift_v2",
            kind="call_put_pair_equal_shift",
            delta_ticks=tick,
            option_ids=option_ids,
            valuation_date=market.valuation_date,
            underlying_id=market.underlying_id,
        )
        mutated = apply_mutation(market, spec).market_slice
        assert scan_market_slice(mutated).realized_signature == expected


def test_end_to_end_child_solver_and_independent_verifier_accept_calendar(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    child, lineage, child_path, _ = _materialize_001(repository_root, tmp_path)
    public_payload = child.to_dict()
    serialized = json.dumps(public_payload)
    assert "requested_signature" not in serialized
    assert "realized_signature" not in serialized
    assert "mutation" not in serialized
    assert "lineage" not in serialized
    assert child.status == "FROZEN"
    assert solve_public_child(public_payload) == {
        "arbitrage_opportunity": True,
        "arbitrage_type": ["calendar"],
    }
    submission = build_submission(public_payload)
    report = verify_submission(
        public_child_path=child_path,
        submission=submission,
        schema_root=repository_root / "schemas",
        variant_path=(
            repository_root
            / "configs/variants/bsm_arbitrage_finding_f2a_v4.json"
        ),
    )
    assert report.accepted is True
    assert report.realized_signature == "001"
    validate_lineage_v4(
        lineage,
        schema_root=repository_root / "schemas",
        public_child=child,
    )


def test_verifier_rejects_wrong_solver_projection(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    child, _, child_path, _ = _materialize_001(repository_root, tmp_path)
    submission = build_submission(child.to_dict())
    submission["trajectory"]["Outcome"]["orm_answer"] = {
        "arbitrage_opportunity": False,
        "arbitrage_type": [],
    }
    report = verify_submission(
        public_child_path=child_path,
        submission=submission,
        schema_root=repository_root / "schemas",
    )
    assert report.accepted is False
    assert report.expected_orm["arbitrage_type"] == ["calendar"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record["authoring_guard_evidence"]["candidate_evidence"][
            2
        ]["terminal_certificate"].update({"actual_boundary_nonnegative": None}),
        lambda record: record["authoring_guard_evidence"]["candidate_evidence"][
            2
        ].update({"setup_boundary_kind": "open"}),
        lambda record: record["authoring_guard_evidence"]["candidate_evidence"][
            2
        ].pop("early_bid_amount_after_fee"),
    ],
)
def test_lineage_schema_rejects_incomplete_calendar_evidence(
    repository_root: Path,
    tmp_path: Path,
    mutation,
) -> None:
    child, lineage, _, _ = _materialize_001(repository_root, tmp_path)
    broken = deepcopy(lineage)
    mutation(broken)
    with pytest.raises(ValidationError):
        validate_lineage_v4(
            broken,
            schema_root=repository_root / "schemas",
            public_child=child,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda record: record["authoring_guard_evidence"][
                "candidate_evidence"
            ][2].update({"delta_1_high": -99.0}),
            "-M",
        ),
        (
            lambda record: record["authoring_guard_evidence"][
                "candidate_evidence"
            ][2].update({"T1": "2027-02-01"}),
            "T1 < T2",
        ),
        (
            lambda record: record.update({"requested_signature": "101"}),
            "requested signature",
        ),
        (
            lambda record: record["authoring_guard_evidence"][
                "candidate_evidence"
            ][2].update(
                {"late_option_id": "F2A-CI-2026-10-02-110-CALL"}
            ),
            "frozen key",
        ),
    ],
)
def test_lineage_semantics_reject_cross_field_calendar_corruption(
    repository_root: Path,
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    child, lineage, _, _ = _materialize_001(repository_root, tmp_path)
    broken = deepcopy(lineage)
    mutation(broken)
    with pytest.raises(ValueError, match=message):
        validate_lineage_v4(
            broken,
            schema_root=repository_root / "schemas",
            public_child=child,
        )


def test_lineage_rejects_calendar_bit_copied_without_verified_candidate(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    child, lineage, _, _ = _materialize_001(repository_root, tmp_path)
    broken = deepcopy(lineage)
    broken["authoring_guard_evidence"]["candidate_evidence"][2][
        "is_arbitrage"
    ] = False
    with pytest.raises(ValueError, match="calendar bit"):
        validate_lineage_v4(
            broken,
            schema_root=repository_root / "schemas",
            public_child=child,
        )


def test_submission_schema_rejects_maximal_spread(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    child, _, child_path, _ = _materialize_001(repository_root, tmp_path)
    submission = build_submission(child.to_dict())
    submission["trajectory"]["Outcome"]["orm_answer"]["maximal_spread"] = 1.0
    with pytest.raises(ValidationError):
        verify_submission(
            public_child_path=child_path,
            submission=submission,
            schema_root=repository_root / "schemas",
        )


def test_solver_implementation_has_no_forbidden_runtime_imports(
    repository_root: Path,
) -> None:
    source = (
        repository_root / "src/synthetic_derivatives/solver/f2a.py"
    ).read_text(encoding="utf-8")
    assert "synthetic_derivatives.verifier" not in source
    assert "synthetic_derivatives.authoring" not in source
    assert "QuantLib" not in source
    assert "spot_grid" not in source
    assert "Monte Carlo" not in source
