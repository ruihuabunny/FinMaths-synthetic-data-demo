from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path, PureWindowsPath
import shutil
from typing import Any

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.package import (
    build_bsm_greeks_package,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_delivery import (
    PORTABLE_DELIVERY_STATUS,
    PortableDeliveryBatch,
    build_portable_bsm_greeks_delivery,
    verify_portable_bsm_greeks_delivery,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
    PortableGreeksTools,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
    TrustedGreeksTools,
)


_VARIANT = "bsm_market_implied_greeks_v1"
_EVALUATION_PUBLIC_FILES = {
    "prompt.md",
    "runtime_contract.json",
    "submission.schema.json",
}
_TRUSTED_TOOL_FILES = {
    "toolset.json",
    "payloads/underlyings.json",
    "payloads/options.json",
}
_VERIFIER_FILES = {
    "README.md",
    "__init__.py",
    "conftest.py",
    "oracle_config.json",
    "requirements.lock",
    "runtime.py",
    "test_contract.py",
    "test_data_identity.py",
    "test_semantics.py",
}


@dataclass(frozen=True)
class PortableDeliveryFixture:
    source_run: Path
    output_root: Path
    delivery: PortableDeliveryBatch
    source_packages: dict[str, Path]
    submissions: dict[str, dict[str, Any]]


@pytest.fixture(scope="module")
def portable_bsm_greeks_delivery(
    packaged_bsm_greeks,
    tmp_path_factory: pytest.TempPathFactory,
) -> PortableDeliveryFixture:
    """Make a self-contained two-task source run without a checked-in golden."""

    root = tmp_path_factory.mktemp("portable-bsm-greeks-delivery")
    source_run = root / "completed-source-run"
    packages_root = source_run / "packages" / _VARIANT
    packages_root.mkdir(parents=True)

    first = packaged_bsm_greeks.package.package_root
    shutil.copytree(first, packages_root / first.name)

    config = json.loads(
        (
            packaged_bsm_greeks.repository_root
            / "configs/task_packages/bsm_market_implied_greeks_v1.json"
        ).read_text(encoding="utf-8")
    )
    second = None
    for sampling_seed in range(27, 100):
        config["private_selection"]["sampling_seed"] = sampling_seed
        second_config = root / f"second-task-config-{sampling_seed}.json"
        second_config.write_bytes(canonical_json_bytes(config))
        try:
            second = build_bsm_greeks_package(
                repository_root=packaged_bsm_greeks.repository_root,
                parent_database=packaged_bsm_greeks.parent_database,
                output_root=source_run / "packages",
                package_config_path=second_config,
            ).package_root
        except ValueError as error:
            if str(error) == (
                "selected row is too close to a decimal rounding boundary"
            ):
                continue
            raise
        break
    assert second is not None

    source_packages = {path.name: path for path in (packages_root / first.name, second)}
    assert len(source_packages) == 2
    accepted = [
        {
            "task_index": index,
            "task_id": task_id,
            "option_row_count": 160,
        }
        for index, task_id in enumerate(sorted(source_packages), start=1)
    ]
    summary = {
        "run_schema_version": "bsm-market-implied-greeks-batch-run-v2.0.0",
        "status": "completed",
        "task_family": "bsm_greeks",
        "variant_id": _VARIANT,
        "requested_task_count": 2,
        "accepted_task_count": 2,
        "unique_task_id_count": 2,
        "package_root": "packages",
        "accepted_tasks": accepted,
        "verification": {
            "all_packages_verified_before_dataset_export": True,
            "all_reference_replays_byte_identical": True,
            "all_trusted_quantlib_canonical_exact_match": True,
            "dataset_task_ids_unique": True,
        },
    }
    (source_run / "run_summary.json").write_bytes(canonical_json_bytes(summary))

    output_root = root / "deliveries"
    delivery = build_portable_bsm_greeks_delivery(
        source_run=source_run,
        output_root=output_root,
        delivery_id="two-task-smoke",
        task_ids=tuple(reversed(sorted(source_packages))),
        expected_task_count=2,
    )
    submissions = {
        task_id: json.loads(
            (package / "reference/final_submission.json").read_text(encoding="utf-8")
        )
        for task_id, package in source_packages.items()
    }
    return PortableDeliveryFixture(
        source_run=source_run,
        output_root=output_root,
        delivery=delivery,
        source_packages=source_packages,
        submissions=submissions,
    )


def _task_root(fixture: PortableDeliveryFixture, task_id: str | None = None) -> Path:
    selected = task_id or fixture.delivery.task_ids[0]
    return fixture.delivery.delivery_root / "tasks" / selected


def _all_strings(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _all_strings(key)
            yield from _all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, str):
        yield value


def test_converter_smoke_builds_two_sorted_verified_tasks(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
) -> None:
    fixture = portable_bsm_greeks_delivery
    delivery = fixture.delivery

    assert delivery.delivery_root == (
        fixture.output_root / _VARIANT / "two-task-smoke"
    )
    assert delivery.task_ids == tuple(sorted(fixture.source_packages))
    assert delivery.manifest["delivery_status"] == PORTABLE_DELIVERY_STATUS
    assert delivery.manifest["task_count"] == 2
    assert delivery.manifest["task_ids"] == list(delivery.task_ids)
    assert delivery.manifest["split_policy"] == {
        "group": "evaluation",
        "policy": "unsplit_shared_parent_snapshot",
    }
    assert set(delivery.manifest["source_run"]) == {
        "run_schema_version",
        "run_summary_digest",
    }
    assert verify_portable_bsm_greeks_delivery(delivery.delivery_root) == (
        delivery.manifest
    )

    for task_id in delivery.task_ids:
        root = _task_root(fixture, task_id)
        assert {
            path.relative_to(root / "evaluation_view/public").as_posix()
            for path in (root / "evaluation_view/public").rglob("*")
            if path.is_file()
        } == _EVALUATION_PUBLIC_FILES
        assert (root / "task.duckdb").is_file()
        assert {
            path.relative_to(root / "evaluation_view").as_posix()
            for path in (root / "evaluation_view").rglob("*")
            if path.is_file()
        } == {
            "manifest.json",
            *(f"public/{name}" for name in _EVALUATION_PUBLIC_FILES),
        }
        assert {
            path.relative_to(root / "trusted_tools").as_posix()
            for path in (root / "trusted_tools").rglob("*")
            if path.is_file()
        } == _TRUSTED_TOOL_FILES
        assert {
            path.relative_to(root / "verifier").as_posix()
            for path in (root / "verifier").rglob("*")
            if path.is_file()
        } == _VERIFIER_FILES


def test_declarative_toolsets_match_their_published_json_schema(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
    repository_root: Path,
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (
            repository_root / "schemas/agent-task-portable-toolset-v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    validator.check_schema(schema)
    for task_id in portable_bsm_greeks_delivery.delivery.task_ids:
        toolset = json.loads(
            (
                _task_root(portable_bsm_greeks_delivery, task_id)
                / "trusted_tools/toolset.json"
            ).read_text(encoding="utf-8")
        )
        validator.validate(toolset)


def test_task_and_batch_can_be_loaded_after_relocation(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
    tmp_path: Path,
) -> None:
    fixture = portable_bsm_greeks_delivery
    task_id = fixture.delivery.task_ids[0]
    relocated_task = tmp_path / "unrelated" / "nested" / "renamed-task-root"
    shutil.copytree(_task_root(fixture, task_id), relocated_task)

    tools = PortableGreeksTools(relocated_task)
    underlyings = tools.query_greeks_underlying_market_v2()
    options = tools.query_greeks_option_quotes_v2()
    assert len(underlyings) == 8
    assert len(options) == 160
    assert {row["task_id"] for row in (*underlyings, *options)} == {task_id}

    relocated_batch = tmp_path / "another-parent" / fixture.delivery.delivery_root.name
    shutil.copytree(fixture.delivery.delivery_root, relocated_batch)
    assert verify_portable_bsm_greeks_delivery(relocated_batch)["task_ids"] == list(
        fixture.delivery.task_ids
    )


def test_portable_payloads_and_result_are_exactly_equivalent_to_database_tools(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
) -> None:
    fixture = portable_bsm_greeks_delivery
    task_id = fixture.delivery.task_ids[0]
    root = _task_root(fixture, task_id)
    runtime = json.loads(
        (root / "evaluation_view/public/runtime_contract.json").read_text(encoding="utf-8")
    )
    database_tools = TrustedGreeksTools(root / "task.duckdb", runtime)
    portable_tools = PortableGreeksTools(root)

    assert portable_tools.query_greeks_underlying_market_v2() == (
        database_tools.query_greeks_underlying_market_v2()
    )
    assert portable_tools.query_greeks_option_quotes_v2() == (
        database_tools.query_greeks_option_quotes_v2()
    )
    submission = fixture.submissions[task_id]
    portable_tools.submit_greeks_submission_v2(submission)
    database_tools.submit_greeks_submission_v2(submission)

    assert portable_tools.result() == database_tools.result()
    assert portable_tools.result().tool_calls == {
        "query_greeks_underlying_market_v2": 1,
        "query_greeks_option_quotes_v2": 1,
        "submit_greeks_submission_v2": 1,
    }


@pytest.mark.parametrize(
    "query_name",
    ["query_greeks_underlying_market_v2", "query_greeks_option_quotes_v2"],
)
def test_portable_query_tools_enforce_exact_one_call_budgets(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
    query_name: str,
) -> None:
    root = _task_root(portable_bsm_greeks_delivery)
    tools = PortableGreeksTools(root)
    query = getattr(tools, query_name)
    query()
    with pytest.raises(CapabilityViolation, match="call budget exceeded"):
        query()


def test_portable_result_requires_the_frozen_complete_schedule(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
) -> None:
    fixture = portable_bsm_greeks_delivery
    task_id = fixture.delivery.task_ids[0]
    tools = PortableGreeksTools(_task_root(fixture, task_id))
    tools.query_greeks_underlying_market_v2()
    tools.query_greeks_option_quotes_v2()

    with pytest.raises(CapabilityViolation, match="did not submit"):
        tools.result()

    tools.submit_greeks_submission_v2(fixture.submissions[task_id])
    with pytest.raises(CapabilityViolation, match="call budget exceeded"):
        tools.submit_greeks_submission_v2(fixture.submissions[task_id])


def test_portable_submission_rejects_schema_task_id_and_size_violations(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
) -> None:
    fixture = portable_bsm_greeks_delivery
    task_id, other_task_id = fixture.delivery.task_ids
    root = _task_root(fixture, task_id)
    submission = fixture.submissions[task_id]

    malformed = deepcopy(submission)
    malformed["unexpected"] = True
    with pytest.raises(CapabilityViolation, match="public schema"):
        PortableGreeksTools(root).submit_greeks_submission_v2(malformed)

    wrong_task = deepcopy(submission)
    wrong_task["task_id"] = other_task_id
    with pytest.raises(CapabilityViolation, match="task ID differs"):
        PortableGreeksTools(root).submit_greeks_submission_v2(wrong_task)

    oversized = deepcopy(submission)
    template = oversized["rows"][0]
    oversized["rows"] = [
        dict(template, row_id=f"row_{index:06d}")
        for index in range(1, 21_001)
    ]
    assert len(canonical_json_bytes(oversized)) > 5_242_880
    with pytest.raises(CapabilityViolation, match="byte budget"):
        PortableGreeksTools(root).submit_greeks_submission_v2(oversized)


def test_manifests_freeze_visibility_and_do_not_leak_local_paths_or_hidden_data(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
    repository_root: Path,
) -> None:
    fixture = portable_bsm_greeks_delivery
    delivery = fixture.delivery.delivery_root
    all_files = {
        path.relative_to(delivery).as_posix()
        for path in delivery.rglob("*")
        if path.is_file()
    }
    forbidden_parts = {"authoring_private", "private", "reference", "views"}
    assert not any(forbidden_parts.intersection(Path(path).parts) for path in all_files)

    manifests = [
        json.loads((delivery / "batch_manifest.json").read_text(encoding="utf-8"))
    ]
    for task_id in fixture.delivery.task_ids:
        root = _task_root(fixture, task_id)
        task_manifest = json.loads(
            (root / "delivery_manifest.json").read_text(encoding="utf-8")
        )
        manifests.extend(
            [
                task_manifest,
                json.loads(
                    (root / "source_manifest.json").read_text(encoding="utf-8")
                ),
                json.loads(
                    (root / "trusted_tools/toolset.json").read_text(encoding="utf-8")
                ),
            ]
        )
        visibility = task_manifest["artifact_visibility"]
        assert {
            path for path, scope in visibility.items()
            if scope == "solver_evaluation_view"
        } == {
            "evaluation_view/manifest.json",
            *(f"evaluation_view/public/{name}" for name in _EVALUATION_PUBLIC_FILES),
        }
        assert {
            path for path, scope in visibility.items() if scope == "tool_host_only"
        } == {
            "task.duckdb",
            *(f"trusted_tools/{name}" for name in _TRUSTED_TOOL_FILES),
        }
        assert {
            path for path, scope in visibility.items() if scope == "verifier_only"
        } == {f"verifier/{name}" for name in _VERIFIER_FILES}
        assert {
            path for path, scope in visibility.items() if scope == "delivery_audit_only"
        } == {"source_manifest.json"}

    repository_text = str(repository_root.resolve())
    for manifest in manifests:
        assert "run_id" not in manifest.get("source_run", {})
        for value in _all_strings(manifest):
            assert not value.startswith("/")
            assert not PureWindowsPath(value).is_absolute()
            assert "/tmp/" not in value
            assert repository_text not in value
            assert "selector_seed" not in value
            assert "sampling_seed" not in value


def test_payload_tampering_is_rejected_by_task_host_and_batch_verifier(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
    tmp_path: Path,
) -> None:
    fixture = portable_bsm_greeks_delivery
    copied = tmp_path / fixture.delivery.delivery_root.name
    shutil.copytree(fixture.delivery.delivery_root, copied)
    task_id = fixture.delivery.task_ids[0]
    options_path = copied / "tasks" / task_id / "trusted_tools/payloads/options.json"
    options = json.loads(options_path.read_text(encoding="utf-8"))
    options[0]["bid"] = "0.00000001"
    options_path.write_bytes(canonical_json_bytes(options))

    with pytest.raises(ValueError, match="static query binding changed"):
        PortableGreeksTools(copied / "tasks" / task_id)
    with pytest.raises(ValueError, match="artifact digest mismatch"):
        verify_portable_bsm_greeks_delivery(copied)


def test_converter_rejects_unknown_counts_and_overwrite(
    portable_bsm_greeks_delivery: PortableDeliveryFixture,
    tmp_path: Path,
) -> None:
    fixture = portable_bsm_greeks_delivery
    task_ids = fixture.delivery.task_ids
    with pytest.raises(ValueError, match="selected task count"):
        build_portable_bsm_greeks_delivery(
            source_run=fixture.source_run,
            output_root=tmp_path / "wrong-count",
            delivery_id="wrong-count",
            task_ids=task_ids,
            expected_task_count=1,
        )
    with pytest.raises(ValueError, match="not accepted source tasks"):
        build_portable_bsm_greeks_delivery(
            source_run=fixture.source_run,
            output_root=tmp_path / "unknown-task",
            delivery_id="unknown-task",
            task_ids=("bsm-mig-v2-000000000000000000000000",),
        )
    with pytest.raises(FileExistsError, match="already exists"):
        build_portable_bsm_greeks_delivery(
            source_run=fixture.source_run,
            output_root=fixture.output_root,
            delivery_id=fixture.delivery.delivery_root.name,
            task_ids=task_ids,
            expected_task_count=2,
        )
