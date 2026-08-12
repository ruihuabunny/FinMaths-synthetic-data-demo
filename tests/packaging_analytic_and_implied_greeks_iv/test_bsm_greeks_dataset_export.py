from __future__ import annotations

import json

from synthetic_derivatives.training import export_bsm_market_greeks_dataset


def test_verified_package_exports_one_self_contained_nine_field_record(
    packaged_bsm_greeks,
    tmp_path,
) -> None:
    exported = export_bsm_market_greeks_dataset(
        [packaged_bsm_greeks.package.package_root],
        repository_root=packaged_bsm_greeks.repository_root,
        output_directory=tmp_path / "dataset",
        dataset_id="test-bsm-greeks-dataset",
    )
    manifest = json.loads(exported.manifest_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in exported.records_path.read_text(encoding="utf-8").splitlines()
    ]

    assert manifest["task_count"] == 1
    assert manifest["contains_private_oracle"] is False
    assert manifest["contains_private_sampling_seed"] is False
    assert len(records) == 1
    assert tuple(records[0]) == (
        "Assumptions",
        "Confidence",
        "Context",
        "Evidence",
        "Intermediate Reasoning",
        "Outcome",
        "Problem",
        "Skills",
        "Verification",
    )
    assert len(records[0]["Evidence"]["public_inputs"]) == 160
    assert len(records[0]["Outcome"]["rows"]) == 160
    assert records[0]["Verification"]["status"] == "passed"
