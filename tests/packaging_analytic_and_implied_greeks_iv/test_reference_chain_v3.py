from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    TARGET_ORDER,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    expected_metric_submission_v3,
    verify_market_metric_submission_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (
    build_portable_bsm_metric_suite_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools_v3 import (
    PortableMetricToolsV3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    replay_solver_source_with_tools,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.trajectory import (
    TrajectoryEvent,
    reference_trajectory_v3,
)


@pytest.fixture(scope="module")
def v3_reference_suite(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    repository = Path(__file__).resolve().parents[2]
    source_run = (
        repository
        / "runs/bsm_market_implied_greeks"
        / "20260814_24_tasks_prompt_v2_parent_seed_20260806_selector_seed_0"
    )
    assert len(tuple(source_run.glob("packages/bsm_market_implied_greeks_v1/*"))) == 24

    root = tmp_path_factory.mktemp("reference-chain-v3")
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
    suite = build_portable_bsm_metric_suite_v3(
        source_run=source_run,
        output_root=root / "deliveries",
        delivery_id="reference_chain_v3_test",
        profile=profile,
        allocation_id="20260814_metric_6x4_v1",
        expected_source_task_count=24,
    )
    return repository, suite.delivery_root


def _leaf_by_target(root: Path, target: str) -> Path:
    tasks = tuple((root / f"targets/{target}/tasks").iterdir())
    assert len(tasks) == 1
    return tasks[0]


@pytest.mark.parametrize("target", TARGET_ORDER)
def test_v3_reference_solver_replays_each_metric_from_public_database_only(
    v3_reference_suite: tuple[Path, Path],
    tmp_path: Path,
    target: str,
) -> None:
    repository, suite_root = v3_reference_suite
    leaf = _leaf_by_target(suite_root, target)
    trusted_files = sorted(
        path.relative_to(leaf / "trusted_tools").as_posix()
        for path in (leaf / "trusted_tools").rglob("*")
        if path.is_file()
    )
    assert trusted_files == ["toolset.json"]
    assert not (leaf / "trusted_tools/payloads").exists()
    assert "payload_path" not in (
        leaf / "trusted_tools/toolset.json"
    ).read_text(encoding="utf-8")

    solver = (
        repository
        / "src/synthetic_derivatives"
        / "packaging_analytic_and_implied_greeks_iv/reference_solver_v3.py"
    )
    solver_source = solver.read_text(encoding="utf-8")
    assert "payloads/" not in solver_source
    assert "options.json" not in solver_source
    assert "underlyings.json" not in solver_source

    runtime_contract = load_json_object(
        leaf / "evaluation_view/public/runtime_contract.json"
    )
    result = replay_solver_source_with_tools(
        source_path=solver,
        tools=PortableMetricToolsV3(leaf),
        submission_directory=tmp_path / target,
        runtime_contract=runtime_contract,
    )
    expected = expected_metric_submission_v3(leaf, target)
    assert result.submission == expected
    assert result.tool_calls == {
        "query_public_duckdb_v3": 3,
        "submit_greeks_submission_v3": 1,
    }
    verify_market_metric_submission_v3(leaf, result.submission, target)


@pytest.mark.parametrize(
    "expression",
    (
        "tools._database",
        "tools._root",
        "tools._connection",
        "tools.query_public_duckdb_v3.__func__.__globals__",
    ),
)
def test_v3_solver_cannot_inspect_private_host_or_runtime_state(
    v3_reference_suite: tuple[Path, Path],
    tmp_path: Path,
    expression: str,
) -> None:
    _, suite_root = v3_reference_suite
    leaf = _leaf_by_target(suite_root, "delta")
    probe_solver = tmp_path / "reference_solver_v3_proxy_probe.py"
    probe_solver.write_text(
        f"def solve(tools):\n    return {expression}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match="private or dunder runtime attributes"
    ):
        replay_solver_source_with_tools(
            source_path=probe_solver,
            tools=PortableMetricToolsV3(leaf),
            submission_directory=tmp_path / "submission",
            runtime_contract=load_json_object(
                leaf / "evaluation_view/public/runtime_contract.json"
            ),
        )


@pytest.mark.parametrize("target", TARGET_ORDER)
def test_v3_reference_trajectory_round_trips_with_v3_tool_names(
    v3_reference_suite: tuple[Path, Path],
    tmp_path: Path,
    target: str,
) -> None:
    repository, suite_root = v3_reference_suite
    leaf = _leaf_by_target(suite_root, target)
    solver = (
        repository
        / "src/synthetic_derivatives"
        / "packaging_analytic_and_implied_greeks_iv/reference_solver_v3.py"
    )
    result = replay_solver_source_with_tools(
        source_path=solver,
        tools=PortableMetricToolsV3(leaf),
        submission_directory=tmp_path / target,
        runtime_contract=load_json_object(
            leaf / "evaluation_view/public/runtime_contract.json"
        ),
    )
    events = reference_trajectory_v3(result)
    assert [event.step_id for event in events] == list(range(1, 12))
    assert events[-1].event_type == "submission"
    assert events[-1].tool_name == "submit_greeks_submission_v3"

    query_events = [
        event for event in events if event.tool_name == "query_public_duckdb_v3"
    ]
    assert [event.event_type for event in query_events] == [
        "action",
        "tool_result",
        "action",
        "tool_result",
        "action",
        "tool_result",
    ]
    assert {
        event.tool_name for event in events if event.tool_name is not None
    } == {"query_public_duckdb_v3", "submit_greeks_submission_v3"}

    schema = load_json_object(
        repository / "schemas/agent-task-trajectory-v1.schema.json"
    )
    validator = Draft202012Validator(schema)
    encoded = b"".join(canonical_json_bytes(event.to_dict()) for event in events)
    decoded = [json.loads(line) for line in encoded.decode("utf-8").splitlines()]
    for item in decoded:
        validator.validate(item)
    assert tuple(TrajectoryEvent(**item) for item in decoded) == events
