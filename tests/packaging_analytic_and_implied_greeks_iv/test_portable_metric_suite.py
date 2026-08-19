from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import shutil

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
    digest_file,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    load_bsm_greeks_underlying_market,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    TARGET_ORDER,
    get_metric_spec,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    METRIC_VERIFIER_FILENAMES,
    expected_metric_submission,
    metric_verifier_files,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (
    PORTABLE_SUITE_STATUS,
    PortableMetricSuite,
    build_portable_bsm_metric_suite,
    verify_portable_bsm_metric_suite,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
    PortableMetricTools,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
)


@dataclass(frozen=True)
class MetricSuiteFixture:
    source_run: Path
    output_root: Path
    profile: Path
    suite: PortableMetricSuite


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _build_source_run(repository: Path, root: Path, count: int) -> Path:
    legacy = (
        repository
        / "task_packages/deliveries/bsm_market_implied_greeks_v1"
        / "20260813_prompt_v2_100/tasks"
    )
    source_tasks = sorted(path for path in legacy.iterdir() if path.is_dir())[:count]
    assert len(source_tasks) == count
    run = root / "source-run"
    accepted = []
    for index, portable_task in enumerate(source_tasks, start=1):
        task_id = portable_task.name
        package = run / f"packages/bsm_market_implied_greeks_v1/{task_id}"
        public = package / "public"
        public.mkdir(parents=True)
        shutil.copyfile(portable_task / "task.duckdb", public / "task.duckdb")
        for name in ("prompt.md", "runtime_contract.json", "submission.schema.json"):
            shutil.copyfile(
                portable_task / f"evaluation_view/public/{name}",
                public / name,
            )
        delivered = json.loads(
            (portable_task / "delivery_manifest.json").read_text(encoding="utf-8")
        )
        underlyings = list(load_bsm_greeks_underlying_market(public / "task.duckdb"))
        selected = sorted({row["underlying_id"] for row in underlyings})
        package_manifest = {
            "package_schema_version": "agent-task-package-v2.0.0",
            "build_status": "ACCEPTED",
            "task_family": "bsm_greeks",
            "task_version": "2.0.0",
            "variant_id": "bsm_market_implied_greeks_v1",
            "task_id": task_id,
            "selected_underlying_ids": selected,
            "parent_snapshot": delivered["parent_snapshot"],
            "public_child_snapshot": delivered["public_child_snapshot"],
            "artifacts": {
                f"public/{name}": digest_file(public / name)
                for name in (
                    "prompt.md",
                    "runtime_contract.json",
                    "submission.schema.json",
                    "task.duckdb",
                )
            },
        }
        _write_json(package / "manifest.json", package_manifest)
        accepted.append(
            {
                "task_index": index,
                "task_id": task_id,
                "sampling_seed": index,
                "selected_underlying_ids": selected,
                "option_row_count": 160,
            }
        )
    _write_json(
        run / "run_summary.json",
        {
            "run_schema_version": "bsm-market-implied-greeks-batch-run-v2.0.0",
            "status": "completed",
            "task_family": "bsm_greeks",
            "variant_id": "bsm_market_implied_greeks_v1",
            "package_root": "packages",
            "requested_task_count": count,
            "accepted_task_count": count,
            "unique_task_id_count": count,
            "accepted_tasks": accepted,
            "verification": {
                "all_packages_verified_before_dataset_export": True,
                "all_reference_replays_byte_identical": True,
                "all_trusted_quantlib_canonical_exact_match": True,
                "dataset_task_ids_unique": True,
                "dataset_record_count": count,
            },
        },
    )
    return run


@pytest.fixture(scope="module")
def portable_metric_suite(
    tmp_path_factory: pytest.TempPathFactory,
) -> MetricSuiteFixture:
    repository = Path(__file__).resolve().parents[2]
    root = tmp_path_factory.mktemp("portable-metric-suite")
    source_run = _build_source_run(repository, root, len(TARGET_ORDER))
    profile = root / "profile.json"
    _write_json(
        profile,
        {
            "profile_schema_version": "bsm-market-metric-delivery-profile-v1.0.0",
            "source_variant_id": "bsm_market_implied_greeks_v1",
            "suite_variant_id": "bsm_market_implied_metric_suite_v1",
            "targets": list(TARGET_ORDER),
            "tasks_per_target": 1,
            "total_task_count": len(TARGET_ORDER),
            "allocation_policy_id": (
                "sha256-rank-round-robin-without-replacement-v1"
            ),
            "require_unique_source_task": True,
            "require_unique_source_database": True,
            "require_unique_market_content": True,
            "require_unique_derived_task": True,
            "require_unique_derived_database": True,
        },
    )
    output = root / "deliveries"
    suite = build_portable_bsm_metric_suite(
        source_run=source_run,
        output_root=output,
        delivery_id="metric_suite_test",
        profile=profile,
        allocation_id="metric_suite_test_allocation",
        expected_source_task_count=len(TARGET_ORDER),
    )
    return MetricSuiteFixture(source_run, output, profile, suite)


def test_suite_is_six_unique_single_metric_leaf_tasks(
    portable_metric_suite: MetricSuiteFixture,
) -> None:
    manifest = verify_portable_bsm_metric_suite(
        portable_metric_suite.suite.delivery_root
    )
    assert manifest["delivery_status"] == PORTABLE_SUITE_STATUS
    assert manifest["target_order"] == list(TARGET_ORDER)
    assert manifest["tasks_per_target"] == 1
    assert manifest["total_task_count"] == 6
    assert {
        manifest[field]
        for field in (
            "unique_source_task_count",
            "unique_source_database_count",
            "unique_market_content_count",
            "unique_derived_task_count",
            "unique_derived_database_count",
        )
    } == {6}


def test_new_suite_binds_the_exact_modular_verifier_tree(
    portable_metric_suite: MetricSuiteFixture,
) -> None:
    root = portable_metric_suite.suite.delivery_root
    expected_verifier_artifacts = {
        f"verifier/{name}" for name in METRIC_VERIFIER_FILENAMES
    }
    assert "verifier/_runtime/submission.py" in expected_verifier_artifacts

    for assignment in portable_metric_suite.suite.manifest["assignments"]:
        leaf = root / assignment["relative_path"]
        verifier = leaf / "verifier"
        actual_files = {
            path.relative_to(verifier).as_posix()
            for path in verifier.rglob("*")
            if path.is_file()
        }
        assert actual_files == set(METRIC_VERIFIER_FILENAMES)

        expected_files = metric_verifier_files(assignment["target"])
        assert all(
            (verifier / name).read_bytes() == payload
            for name, payload in expected_files.items()
        )

        leaf_manifest = json.loads(
            (leaf / "delivery_manifest.json").read_text(encoding="utf-8")
        )
        bound_artifacts = {
            name
            for name in leaf_manifest["artifacts"]
            if name.startswith("verifier/")
        }
        bound_visibility = {
            name
            for name in leaf_manifest["artifact_visibility"]
            if name.startswith("verifier/")
        }
        assert bound_artifacts == expected_verifier_artifacts
        assert bound_visibility == expected_verifier_artifacts
        assert {
            leaf_manifest["artifact_visibility"][name]
            for name in expected_verifier_artifacts
        } == {"verifier_only"}


@pytest.mark.parametrize("target", TARGET_ORDER)
def test_each_leaf_tools_accept_only_its_exact_metric_submission(
    portable_metric_suite: MetricSuiteFixture,
    target: str,
) -> None:
    manifest = portable_metric_suite.suite.manifest
    assignment = next(item for item in manifest["assignments"] if item["target"] == target)
    task_root = portable_metric_suite.suite.delivery_root / assignment["relative_path"]
    expected = expected_metric_submission(task_root, target)
    tools = PortableMetricTools(task_root)
    assert len(tools.query_greeks_underlying_market_v2()) == 8
    assert len(tools.query_greeks_option_quotes_v2()) == 160
    tools.submit_greeks_submission_v2(expected)
    assert tools.result().submission == expected

    wrong = deepcopy(expected)
    wrong["method_id"] = get_metric_spec(
        "gamma" if target != "gamma" else "delta"
    ).method_id
    with pytest.raises(CapabilityViolation, match="public schema"):
        PortableMetricTools(task_root).submit_greeks_submission_v2(wrong)

    combined = deepcopy(expected)
    extra_field = (
        "unit_delta" if target == "iv" else "market_implied_volatility"
    )
    combined["rows"][0][extra_field] = "0.00000000"
    with pytest.raises(CapabilityViolation, match="public schema"):
        PortableMetricTools(task_root).submit_greeks_submission_v2(combined)


def test_suite_verifier_rejects_and_recovers_from_artifact_tampering(
    portable_metric_suite: MetricSuiteFixture,
) -> None:
    root = portable_metric_suite.suite.delivery_root
    assignment = portable_metric_suite.suite.manifest["assignments"][0]
    prompt = root / assignment["relative_path"] / "evaluation_view/public/prompt.md"
    original = prompt.read_bytes()
    try:
        prompt.write_bytes(original + b"tampered\n")
        with pytest.raises(ValueError, match="artifact digest mismatch"):
            verify_portable_bsm_metric_suite(root)
    finally:
        prompt.write_bytes(original)
    verify_portable_bsm_metric_suite(root)

    nested_module = (
        root
        / assignment["relative_path"]
        / "verifier/_runtime/oracle.py"
    )
    original = nested_module.read_bytes()
    try:
        nested_module.write_bytes(original + b"# tampered\n")
        with pytest.raises(
            ValueError,
            match=r"artifact digest mismatch: verifier/_runtime/oracle\.py",
        ):
            verify_portable_bsm_metric_suite(root)
    finally:
        nested_module.write_bytes(original)
    verify_portable_bsm_metric_suite(root)

    unbound = (
        root
        / assignment["relative_path"]
        / "verifier/_runtime/unbound.py"
    )
    try:
        unbound.write_text("# unbound\n", encoding="utf-8")
        with pytest.raises(ValueError, match="suite tree differs from the allowlist"):
            verify_portable_bsm_metric_suite(root)
    finally:
        unbound.unlink()
    verify_portable_bsm_metric_suite(root)


def test_suite_builder_refuses_to_overwrite_destination(
    portable_metric_suite: MetricSuiteFixture,
) -> None:
    with pytest.raises(FileExistsError, match="already exists"):
        build_portable_bsm_metric_suite(
            source_run=portable_metric_suite.source_run,
            output_root=portable_metric_suite.output_root,
            delivery_id="metric_suite_test",
            profile=portable_metric_suite.profile,
            allocation_id="metric_suite_test_allocation",
            expected_source_task_count=len(TARGET_ORDER),
        )


def test_suite_build_failure_leaves_no_destination_or_staging(
    portable_metric_suite: MetricSuiteFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv import (
        portable_metric_suite as module,
    )

    def fail_leaf(**_: object) -> dict[str, object]:
        raise RuntimeError("injected leaf failure")

    monkeypatch.setattr(module, "_build_metric_leaf", fail_leaf)
    with pytest.raises(RuntimeError, match="injected leaf failure"):
        module.build_portable_bsm_metric_suite(
            source_run=portable_metric_suite.source_run,
            output_root=portable_metric_suite.output_root,
            delivery_id="metric_suite_failed",
            profile=portable_metric_suite.profile,
            allocation_id="metric_suite_test_allocation",
            expected_source_task_count=len(TARGET_ORDER),
        )
    variant = portable_metric_suite.output_root / "bsm_market_implied_metric_suite_v1"
    assert not (variant / "metric_suite_failed").exists()
    assert not (variant / ".metric_suite_failed.build.lock").exists()
    assert list(variant.glob(".metric_suite_failed.staging-*")) == []
