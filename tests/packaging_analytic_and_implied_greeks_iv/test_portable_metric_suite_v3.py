from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    TARGET_ORDER,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    expected_metric_submission,
    expected_metric_submission_v3,
    verify_market_metric_submission_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (
    build_portable_bsm_metric_suite_v3,
    verify_portable_bsm_metric_suite,
    verify_portable_bsm_metric_suite_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools import (
    validate_portable_metric_toolset,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools_v3 import (
    PortableMetricToolsV3,
    validate_portable_metric_toolset_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
)


@pytest.fixture(scope="module")
def portable_metric_suite_v3(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    repository = Path(__file__).resolve().parents[2]
    root = tmp_path_factory.mktemp("portable-metric-suite-v3")
    profile = root / "profile.json"
    profile.write_bytes(
        canonical_json_bytes(
            {
                "profile_schema_version": (
                    "bsm-market-metric-delivery-profile-v1.0.0"
                ),
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
            }
        )
    )
    source_run = (
        repository
        / "runs/bsm_market_implied_greeks"
        / "20260814_24_tasks_prompt_v2_parent_seed_20260806_selector_seed_0"
    )
    suite = build_portable_bsm_metric_suite_v3(
        source_run=source_run,
        output_root=root / "deliveries",
        delivery_id="metric_suite_db_query_v3_test",
        profile=profile,
        allocation_id="20260814_metric_6x4_v1",
        expected_source_task_count=24,
    )
    return repository, suite.delivery_root


def _leaf_by_target(root: Path, target: str) -> Path:
    tasks = tuple((root / f"targets/{target}/tasks").iterdir())
    assert len(tasks) == 1
    return tasks[0]


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v3_suite_has_one_database_source_and_explicit_version_dispatch(
    portable_metric_suite_v3: tuple[Path, Path],
) -> None:
    _, root = portable_metric_suite_v3
    manifest = verify_portable_bsm_metric_suite_v3(root)
    assert manifest["suite_schema_version"] == "bsm-market-metric-suite-v2.0.0"
    assert manifest["total_task_count"] == len(TARGET_ORDER)
    for assignment in manifest["assignments"]:
        leaf = root / assignment["relative_path"]
        assert sorted(
            path.relative_to(leaf / "trusted_tools").as_posix()
            for path in (leaf / "trusted_tools").rglob("*")
            if path.is_file()
        ) == ["toolset.json"]
        assert not (leaf / "trusted_tools/payloads").exists()
        assert "payload_path" not in (
            leaf / "trusted_tools/toolset.json"
        ).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="suite identity"):
        verify_portable_bsm_metric_suite(root)


def test_v3_public_contracts_validate_against_their_schemas(
    portable_metric_suite_v3: tuple[Path, Path],
) -> None:
    repository, root = portable_metric_suite_v3
    package_schema = _json(repository / "schemas/agent-task-package-v3.schema.json")
    toolset_schema = _json(
        repository / "schemas/agent-task-portable-toolset-v3.schema.json"
    )
    runtime_schema = _json(
        repository / "schemas/agent-task-runtime-contract-v3.schema.json"
    )
    for target in TARGET_ORDER:
        leaf = _leaf_by_target(root, target)
        submission_schema = _json(
            repository
            / "schemas"
            / f"bsm-market-implied-{target.replace('_', '-')}-submission-v2.schema.json"
        )
        Draft202012Validator(package_schema).validate(
            _json(leaf / "delivery_manifest.json")
        )
        Draft202012Validator(toolset_schema).validate(
            _json(leaf / "trusted_tools/toolset.json")
        )
        Draft202012Validator(runtime_schema).validate(
            _json(leaf / "evaluation_view/public/runtime_contract.json")
        )
        Draft202012Validator(submission_schema).validate(
            expected_metric_submission_v3(leaf, target)
        )


@pytest.mark.parametrize("target", TARGET_ORDER)
def test_v3_query_join_and_unordered_submission_replay(
    portable_metric_suite_v3: tuple[Path, Path], target: str
) -> None:
    _, root = portable_metric_suite_v3
    leaf = _leaf_by_target(root, target)
    tools = PortableMetricToolsV3(leaf)
    metadata = tools.query_public_duckdb_v3(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name IN ('metadata', 'solver_visible') ORDER BY schema_name"
    )
    joined = tools.query_public_duckdb_v3(
        "SELECT o.row_id, u.spot, o.strike, o.bid, o.ask "
        "FROM solver_visible.option_quote_inputs AS o "
        "JOIN solver_visible.underlying_market_inputs AS u "
        "USING (task_id, snapshot_id, valuation_date, underlying_id) "
        "ORDER BY o.row_id"
    )
    assert metadata == {
        "columns": ["schema_name"],
        "rows": [["metadata"], ["solver_visible"]],
        "row_count": 2,
        "truncated": False,
    }
    assert joined["row_count"] == 160
    assert joined["truncated"] is False
    assert all(isinstance(row[1], str) for row in joined["rows"])
    expected = expected_metric_submission_v3(leaf, target)
    expected["rows"].reverse()
    tools.submit_greeks_submission_v3(expected)
    result = tools.result()
    verify_market_metric_submission_v3(leaf, result.submission, target)
    assert result.tool_calls == {
        "query_public_duckdb_v3": 2,
        "submit_greeks_submission_v3": 1,
    }
    with pytest.raises(CapabilityViolation, match="closed after submission"):
        tools.query_public_duckdb_v3("SELECT 1")


def test_v3_host_enforces_dynamic_row_identity_and_minimum_query(
    portable_metric_suite_v3: tuple[Path, Path],
) -> None:
    _, root = portable_metric_suite_v3
    leaf = _leaf_by_target(root, "iv")
    expected = expected_metric_submission_v3(leaf, "iv")
    no_query = PortableMetricToolsV3(leaf)
    no_query.submit_greeks_submission_v3(expected)
    with pytest.raises(CapabilityViolation, match="call policy"):
        no_query.result()

    duplicate = deepcopy(expected)
    duplicate["rows"][-1] = deepcopy(duplicate["rows"][0])
    queried = PortableMetricToolsV3(leaf)
    queried.query_public_duckdb_v3("SELECT 1")
    with pytest.raises(CapabilityViolation, match="public contract"):
        queried.submit_greeks_submission_v3(duplicate)


def test_v3_market_rows_and_canonical_answers_equal_frozen_v2(
    portable_metric_suite_v3: tuple[Path, Path],
) -> None:
    repository, root = portable_metric_suite_v3
    v2_root = (
        repository
        / "task_packages/deliveries/bsm_market_implied_metric_suite_v1"
        / "20260814_metric_6x4_unique_db"
    )
    v2_manifest = _json(v2_root / "suite_manifest.json")
    v3_manifest = _json(root / "suite_manifest.json")
    v2_by_source = {
        (item["target"], item["source_task_id"]): item
        for item in v2_manifest["assignments"]
    }
    for v3_item in v3_manifest["assignments"]:
        v2_item = v2_by_source[(v3_item["target"], v3_item["source_task_id"])]
        assert (
            v3_item["source_market_content_digest"]
            == v2_item["source_market_content_digest"]
        )
        v2_leaf = v2_root / v2_item["relative_path"]
        v3_leaf = root / v3_item["relative_path"]
        assert expected_metric_submission(v2_leaf, v3_item["target"])["rows"] == (
            expected_metric_submission_v3(v3_leaf, v3_item["target"])["rows"]
        )


def test_v3_prompt_hides_database_layout_and_legacy_protocol(
    portable_metric_suite_v3: tuple[Path, Path],
) -> None:
    _, root = portable_metric_suite_v3
    forbidden = {
        "solver_visible",
        "underlying_market_inputs",
        "option_quote_inputs",
        "query_greeks_underlying_market_v2",
        "query_greeks_option_quotes_v2",
        "preserve the queried option row order",
        "d1 =",
        "d2 =",
    }
    for target in TARGET_ORDER:
        prompt = (
            _leaf_by_target(root, target)
            / "evaluation_view/public/prompt.md"
        ).read_text(encoding="utf-8")
        assert all(token not in prompt for token in forbidden)
        assert "query_public_duckdb_v3" in prompt
        assert "submit_greeks_submission_v3" in prompt
        assert "Row order is not semantically significant." in prompt


def test_v2_and_v3_toolset_validators_do_not_cross_accept(
    portable_metric_suite_v3: tuple[Path, Path],
) -> None:
    repository, root = portable_metric_suite_v3
    v3_leaf = _leaf_by_target(root, "delta")
    with pytest.raises(ValueError):
        validate_portable_metric_toolset(v3_leaf)

    v2_tasks = (
        repository
        / "task_packages/deliveries/bsm_market_implied_metric_suite_v1"
        / "20260814_metric_6x4_unique_db/targets/delta/tasks"
    )
    v2_leaf = next(v2_tasks.iterdir())
    with pytest.raises(ValueError):
        validate_portable_metric_toolset_v3(v2_leaf)
