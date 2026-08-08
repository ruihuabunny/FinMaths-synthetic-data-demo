from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from synthetic_derivatives.authoring.f2a_calibration import (
    CandidateCalibration,
    CalibrationGates,
    TaskCalibrationRecord,
    calibration_report,
    calibration_report_checksum,
    validate_release_calibration_report,
)
from synthetic_derivatives.authoring.schema import (
    assert_no_private_metadata_leakage,
    public_dynamics_projection,
)
from synthetic_derivatives.verifier.f2a_database import sample_underlyings
from synthetic_derivatives.verifier.f2a_v5 import compare_semantic_layers


def test_v5_sampler_is_stable_unique_and_seeded() -> None:
    universe = tuple(f"U{index:02d}" for index in range(22))
    first = sample_underlyings(universe, seed=20260808)
    assert first == sample_underlyings(universe, seed=20260808)
    assert first != sample_underlyings(universe, seed=20260809)
    assert len(first) == len(set(first)) == 8
    assert first == tuple(sorted(first))
    assert set(first) <= set(universe)
    with pytest.raises(ValueError, match="sample_size = 8"):
        sample_underlyings(universe, sample_size=7, seed=1)


def test_public_dynamics_projection_removes_all_node_values() -> None:
    private = {
        "measure": "P",
        "process": "GBM",
        "time_origin": "2026-01-01",
        "time_axis": "calendar_day_offset/Actual365Fixed",
        "drift": 0.05,
        "volatility": 0.2,
        "drift_function": {
            "type": "piecewise_linear",
            "nodes": [{"day_offset": 0, "value": 0.05}, {"day_offset": 30, "value": 0.06}],
            "extrapolation": "flat",
        },
        "volatility_function": {
            "type": "piecewise_linear",
            "nodes": [{"day_offset": 0, "value": 0.2}, {"day_offset": 30, "value": 0.25}],
            "extrapolation": "flat",
        },
    }
    public = public_dynamics_projection(private)
    assert public["drift_function"]["node_offsets_calendar_days"] == [0, 30]
    assert "nodes" not in json.dumps(public)
    assert "0.25" not in json.dumps(public)
    assert_no_private_metadata_leakage(public)
    with pytest.raises(ValueError, match="sampling_seed"):
        assert_no_private_metadata_leakage({"nested": {"sampling_seed": 3}})


def test_task_seed_calibration_conserves_confusion_counts_and_gates() -> None:
    records = [
        TaskCalibrationRecord(
            task_seed_id=f"clean-{index}",
            public_signature="000",
            private_signature="000",
            mutated=False,
            stratum={"horizon": 126},
            candidates=(CandidateCalibration("X", False, False, 0.25),),
        )
        for index in range(100)
    ] + [
        TaskCalibrationRecord(
            task_seed_id=f"mutated-{index}",
            public_signature="111",
            private_signature="111",
            mutated=True,
            stratum={"horizon": 126},
            candidates=(CandidateCalibration("X", True, True, 0.5),),
        )
        for index in range(100)
    ]
    report = calibration_report(
        records,
        gates=CalibrationGates(
            minimum_task_seeds=200,
            clean_task_any_fp_upper_95=0.03,
            mutated_task_any_fn_upper_95=0.03,
            family_fp_upper_95=0.03,
            family_fn_upper_95=0.03,
            exact_signature_accuracy_lower_95=0.97,
        ),
    )
    assert sum(
        sum(row.values()) for row in report["signature_confusion_matrix"].values()
    ) == 200
    assert report["release_gate_passed"] is True
    assert report["bootstrap_maximum_statistic"]["critical_value"] == 0.5
    assert report["stratified_metrics"][0]["task_count"] == 200
    validate_release_calibration_report(
        report,
        gates=CalibrationGates(
            minimum_task_seeds=200,
            clean_task_any_fp_upper_95=0.03,
            mutated_task_any_fn_upper_95=0.03,
            family_fp_upper_95=0.03,
            family_fn_upper_95=0.03,
            exact_signature_accuracy_lower_95=0.97,
        ),
    )
    assert len(calibration_report_checksum(report)) == 64
    failed = {**report, "release_gate_passed": False}
    with pytest.raises(ValueError, match="not marked"):
        validate_release_calibration_report(
            failed,
            gates=CalibrationGates(
                minimum_task_seeds=200,
                clean_task_any_fp_upper_95=0.03,
                mutated_task_any_fn_upper_95=0.03,
                family_fp_upper_95=0.03,
                family_fn_upper_95=0.03,
                exact_signature_accuracy_lower_95=0.97,
            ),
        )
    forged_bound = copy.deepcopy(report)
    forged_bound["clean_any_fp"]["upper_95"] = 0.0
    with pytest.raises(ValueError, match="disagrees with its counts"):
        validate_release_calibration_report(
            forged_bound,
            gates=CalibrationGates(
                minimum_task_seeds=200,
                clean_task_any_fp_upper_95=0.03,
                mutated_task_any_fn_upper_95=0.03,
                family_fp_upper_95=0.03,
                family_fn_upper_95=0.03,
                exact_signature_accuracy_lower_95=0.97,
            ),
        )
    malformed_confusion = copy.deepcopy(report)
    malformed_confusion["signature_confusion_matrix"]["000"]["000"] = True
    with pytest.raises(ValueError, match="nonnegative integer"):
        validate_release_calibration_report(
            malformed_confusion,
            gates=CalibrationGates(
                minimum_task_seeds=200,
                clean_task_any_fp_upper_95=0.03,
                mutated_task_any_fn_upper_95=0.03,
                family_fp_upper_95=0.03,
                family_fn_upper_95=0.03,
                exact_signature_accuracy_lower_95=0.97,
            ),
        )
    with pytest.raises(ValueError, match="unique"):
        calibration_report([records[0], records[0]])


def test_v5_configs_schemas_and_solver_are_independent(repository_root: Path) -> None:
    variant = json.loads(
        (repository_root / "configs/variants/bsm_model_reconstruction_xut_signal_f2a_v5.json").read_text()
    )
    dataset = json.loads(
        (repository_root / "authoring/configs/f2a_dataset_v5.json").read_text()
    )
    parent = json.loads(
        (
            repository_root
            / "configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json"
        ).read_text()
    )
    submission = json.loads(
        (repository_root / "schemas/submission-v5.schema.json").read_text()
    )
    lineage = json.loads(
        (repository_root / "schemas/f2a-lineage-v5.schema.json").read_text()
    )
    Draft202012Validator.check_schema(submission)
    Draft202012Validator.check_schema(lineage)
    assert variant["variant_id"] == dataset["variant_id"]
    assert dataset["source"]["preferred_parent_snapshot_id"] == parent["snapshot_id"]
    assert dataset["pilot_parent_reachability"]["reachable_signatures"] == [
        "001", "010", "100", "101", "110", "111"
    ]
    assert dataset["pilot_parent_reachability"]["unreachable_signatures"] == ["011"]
    assert parent["business_days"] == 126
    assert parent["option_minimum_price_increment"] == "0.01"
    assert parent["underlying_minimum_price_increment"] == "0.01"
    for underlying in parent["underlyings"]:
        drift_offsets = [item["day_offset"] for item in underlying["physical_drift"]["nodes"]]
        diffusion_offsets = [
            item["day_offset"] for item in underlying["physical_volatility"]["nodes"]
        ]
        assert drift_offsets == diffusion_offsets
        assert len(drift_offsets) == 3
        assert drift_offsets[-1] <= 175
    assert variant["execution_audit_contract"]["variant_id"] == "bsm-arbitrage-finding-f2a-v4"
    assert variant["physical_fitting_contract"]["default_diffusion_node_count"] == 3
    solver_sources = "\n".join(
        path.read_text()
        for path in (repository_root / "src/synthetic_derivatives/solver").glob("f2a_v5*.py")
    )
    assert "synthetic_derivatives.verifier" not in solver_sources
    assert "synthetic_derivatives.authoring" not in solver_sources


def test_v5_semantic_layers_reject_exact_nested_perturbations_and_nonfinite_values() -> None:
    expected = {
        "task_id": "task",
        "snapshot_id": "child",
        "snapshot_revision": 1,
        "variant_id": "bsm_model_reconstruction_xut_signal_f2a_v5",
        "output_contract_id": "model-reconstruction-xut-full-trajectory-v1",
        "verifier_digest": "0" * 64,
        "underlying_fits": [{"fitted_diffusion_node_values": [0.2, 0.21, 0.22]}],
        "option_fits": [{"fitted_counterfactual_prices_by_row_id": {"row": 1.25}}],
        "mutation_diagnosis": [{"affected_row_ids": ["row"]}],
        "model_signals": {
            "slices": [{"signature": "100", "active_signals": [{"candidate_id": "X|1"}]}]
        },
        "execution_audit": {
            "required": False,
            "scope": "independent-v4-catalogue-audit-not-scored-by-v5",
        },
    }
    assert all(
        item["accepted"] for item in compare_semantic_layers(expected, expected).values()
    )
    for layer, path, replacement in (
        ("V0", ("verifier_digest",), "1" * 64),
        ("V1", ("underlying_fits", 0, "fitted_diffusion_node_values", 1), 0.2100000001),
        ("V2", ("option_fits", 0, "fitted_counterfactual_prices_by_row_id", "row"), 1.26),
        ("V2", ("mutation_diagnosis", 0, "affected_row_ids", 0), "other-row"),
        ("V3", ("model_signals", "slices", 0, "active_signals", 0, "candidate_id"), "X|2"),
        ("V_exec_audit", ("execution_audit", "required"), True),
    ):
        submitted = copy.deepcopy(expected)
        target = submitted
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
        report = compare_semantic_layers(expected, submitted)
        assert report[layer]["accepted"] is False
        assert report[layer]["failure"] == f"{layer} canonical output mismatch"

    nonfinite = copy.deepcopy(expected)
    nonfinite["model_signals"]["nonfinite"] = math.nan
    with pytest.raises(ValueError, match="non-finite"):
        compare_semantic_layers(expected, nonfinite)
