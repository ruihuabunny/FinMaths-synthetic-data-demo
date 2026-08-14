from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from llm_solutions import run_bsm_market_implied_greeks as runner
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    TARGET_ORDER,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (
    build_portable_bsm_metric_suite_v3,
)


PORTABLE_DELIVERY_ROOT = (
    REPOSITORY_ROOT
    / "task_packages/deliveries/bsm_market_implied_greeks_v1"
    / "20260813_prompt_v2_100"
)
SOURCE_RUN_ROOT = (
    REPOSITORY_ROOT
    / "runs/bsm_market_implied_greeks"
    / "20260814_24_tasks_prompt_v2_parent_seed_20260806_selector_seed_0"
)


def _portable_package() -> runner.TaskPackage:
    manifest_path = sorted(
        (PORTABLE_DELIVERY_ROOT / "tasks").glob("*/delivery_manifest.json")
    )[0]
    package = runner._package_from_manifest(manifest_path)
    assert package is not None
    assert package.is_portable
    assert package.database_path is None
    assert package.prompt_path.parent == package.root / "evaluation_view/public"
    return package


def _source_package(tmp_path: Path) -> runner.TaskPackage:
    root = tmp_path / "source_package"
    public = root / "public"
    public.mkdir(parents=True)
    for name in (
        "prompt.md",
        "runtime_contract.json",
        "submission.schema.json",
    ):
        (public / name).write_text("{}", encoding="utf-8")
    (public / "task.duckdb").write_bytes(b"source database")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "task_id": "bsm-mig-v2-000000000000000000000000",
                "variant_id": runner.VARIANT_ID,
            }
        ),
        encoding="utf-8",
    )
    package = runner._package_from_manifest(manifest)
    assert package is not None
    assert not package.is_portable
    assert package.database_path == public / "task.duckdb"
    return package


@pytest.fixture(scope="module")
def portable_v3_delivery(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    root = tmp_path_factory.mktemp("hy3-runner-v3")
    profile = root / "profile.json"
    profile.write_text(
        json.dumps(
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
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    suite = build_portable_bsm_metric_suite_v3(
        source_run=SOURCE_RUN_ROOT,
        output_root=root / "deliveries",
        delivery_id="hy3_runner_metric_v3",
        profile=profile,
        allocation_id="hy3_runner_metric_v3_allocation",
        expected_source_task_count=24,
    )
    return suite.delivery_root


def _v3_package(delivery: Path, target: str = "delta") -> runner.TaskPackage:
    manifest_path = next(
        (delivery / f"targets/{target}/tasks").glob("*/delivery_manifest.json")
    )
    package = runner._package_from_manifest(manifest_path)
    assert package is not None
    assert package.is_portable
    assert package.host_protocol == runner.PORTABLE_TOOL_HOST_PROTOCOL_V3
    assert package.target_metric == target
    assert package.database_path == package.root / "task.duckdb"
    return package


def _v3_delta_solver_source(*, query_after_submission: bool = False) -> str:
    trailing_query = (
        '\n    tools.query_public_duckdb_v3("SELECT 1")'
        if query_after_submission
        else ""
    )
    return '''def solve(tools):
    result = tools.query_public_duckdb_v3(
        "SELECT task_id, row_id "
        "FROM solver_visible.option_quote_inputs ORDER BY row_id"
    )
    if result["truncated"]:
        raise ValueError("required input was truncated")
    submission = {
        "task_id": result["rows"][0][0],
        "submission_schema_version": "bsm-market-implied-delta-submission-v2.0.0",
        "method_id": "bsm-mid-iv-bisection80-analytic-delta-v1",
        "status": "completed",
        "rows": [
            {"row_id": row[1], "unit_delta": "0.00000000"}
            for row in result["rows"]
        ],
    }
    tools.submit_greeks_submission_v3(submission)''' + trailing_query + '''
    return submission
'''


def test_discovers_portable_delivery_tasks() -> None:
    packages = runner.discover_task_packages(PORTABLE_DELIVERY_ROOT)

    assert len(packages) == 100
    assert all(package.is_portable for package in packages)
    assert all(package.database_path is None for package in packages)


def test_discovers_only_frozen_v3_metric_leaves(
    portable_v3_delivery: Path,
) -> None:
    packages = runner.discover_task_packages(portable_v3_delivery)

    assert len(packages) == len(TARGET_ORDER)
    assert {package.target_metric for package in packages} == set(TARGET_ORDER)
    assert {package.variant_id for package in packages} == set(
        runner.METRIC_VARIANT_IDS
    )
    assert all(package.is_portable for package in packages)
    assert all(
        package.host_protocol == runner.PORTABLE_TOOL_HOST_PROTOCOL_V3
        for package in packages
    )
    assert all(
        package.database_path == package.root / "task.duckdb"
        for package in packages
    )


def test_legacy_static_metric_leaves_are_not_misrouted_to_v3() -> None:
    legacy_metric_root = (
        REPOSITORY_ROOT
        / "task_packages/deliveries/bsm_market_implied_metric_suite_v1"
        / "20260814_metric_6x4_unique_db"
    )

    with pytest.raises(runner.RunnerError, match="no supported"):
        runner.discover_task_packages(legacy_metric_root)


def test_v3_discovery_rejects_protocol_identity_drift(
    portable_v3_delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _v3_package(portable_v3_delivery)
    manifest_path = package.root / "delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["task_interface_version"] = "wrong"
    original_load_json = runner._load_json

    def changed_manifest(path: Path):
        return manifest if path == manifest_path else original_load_json(path)

    monkeypatch.setattr(runner, "_load_json", changed_manifest)

    assert runner._package_from_manifest(manifest_path) is None


def test_source_package_format_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _source_package(tmp_path)
    expected = runner.RuntimeReplayResult(
        submission={"rows": []},
        submission_bytes=b'{"rows":[]}\n',
        tool_calls={},
        underlying_market_digest="underlyings",
        option_quotes_digest="options",
        submission_digest="submission",
    )

    def fake_replay_solver_source(**kwargs):
        assert kwargs["database"] == package.database_path
        return expected

    monkeypatch.setattr(runner, "replay_solver_source", fake_replay_solver_source)
    monkeypatch.setattr(runner, "_runtime_contract", lambda _: {})

    result = runner._run_python_solver(
        package=package,
        run_directory=tmp_path / "source_run",
        source="def solve(tools):\n    return {}\n",
        trusted_verification=False,
    )

    assert result is expected


def test_hy3_chat_request_uses_nested_function_tools() -> None:
    assert runner.DEFAULT_API_URL == (
        "https://tokenhub.tencentmaas.com/v1/chat/completions"
    )
    tools = runner._tool_definitions()
    config = runner.ApiConfig(
        url=runner.DEFAULT_API_URL,
        model="hy3",
        api_key=None,
        timeout_seconds=300.0,
        retries=3,
        max_tokens=32768,
        reasoning_effort="high",
    )
    messages = [
        {"role": "system", "content": runner.SYSTEM_PROMPT},
        {"role": "user", "content": "solve"},
    ]

    body = runner._chat_request_body(config, messages, tools)

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == runner.RUN_PYTHON_TOOL
    assert "name" not in tools[0]
    assert tools[0]["function"]["parameters"]["required"] == ["source"]
    assert body == {
        "model": "hy3",
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning_effort": "high",
        "max_tokens": 32768,
        "stream": False,
    }


def test_v3_solver_definition_and_system_prompt_freeze_inner_tool_lifecycle(
    portable_v3_delivery: Path,
) -> None:
    package = _v3_package(portable_v3_delivery)
    definition = runner._tool_definitions(package)[0]["function"]
    description = definition["description"]
    system_prompt = runner._system_prompt(package)

    for required in (
        "tools.query_public_duckdb_v3(sql_string)",
        "tools.submit_greeks_submission_v3(submission_object)",
        "between one and ten times",
        "no query may follow submission",
    ):
        assert required.casefold() in description.casefold()
    assert "columns" in system_prompt
    assert "row_count" in system_prompt
    assert "truncated" in system_prompt
    assert runner._system_prompt(_portable_package()) == runner.SYSTEM_PROMPT


def test_hy3_function_call_and_output_follow_chat_shape() -> None:
    message = runner._assistant_tool_message(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "I should write the solver.",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": runner.RUN_PYTHON_TOOL,
                                    "arguments": (
                                        '{"source":"def solve(tools): pass"}'
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )

    assert message["reasoning_content"] == "I should write the solver."
    assert runner._tool_call(message["tool_calls"][0]) == (
        "call_1",
        runner.RUN_PYTHON_TOOL,
        {"source": "def solve(tools): pass"},
    )
    assert runner._tool_result_message("call_1", {"rows": [1]}) == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"rows":[1]}',
    }


def test_hy3_ordinary_output_is_not_accepted_as_a_tool_call() -> None:
    with pytest.raises(runner.AttemptError, match="required tool"):
        runner._assistant_tool_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "def solve(tools): pass",
                        }
                    }
                ]
            }
        )


def test_portable_solver_runs_static_tools_without_database_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        REPOSITORY_ROOT
        / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/reference_solver.py"
    ).read_text(encoding="utf-8")

    def reject_database_fallback(*args, **kwargs):
        raise AssertionError("portable replay must not use the database adapter")

    monkeypatch.setattr(runner, "replay_solver_source", reject_database_fallback)
    monkeypatch.setattr(runner, "load_bsm_greeks_inputs", reject_database_fallback)

    result = runner._run_python_solver(
        package=_portable_package(),
        run_directory=tmp_path / "python_run",
        source=source,
        trusted_verification=True,
    )

    assert len(result.submission["rows"]) == 160
    assert result.tool_calls == {
        "query_greeks_underlying_market_v2": 1,
        "query_greeks_option_quotes_v2": 1,
        "submit_greeks_submission_v2": 1,
    }


def test_v3_leaf_runs_query_and_submit_without_static_payload_fallback(
    portable_v3_delivery: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _v3_package(portable_v3_delivery)

    def reject_static_fallback(*args, **kwargs):
        raise AssertionError("v3 replay must not use a static JSON adapter")

    monkeypatch.setattr(runner, "PortableGreeksTools", reject_static_fallback)
    monkeypatch.setattr(runner, "replay_solver_source", reject_static_fallback)
    result = runner._run_python_solver(
        package=package,
        run_directory=tmp_path / "v3_python_run",
        source=_v3_delta_solver_source(),
        trusted_verification=False,
    )

    assert len(result.submission["rows"]) == 160
    assert result.tool_calls == {
        "query_public_duckdb_v3": 1,
        "submit_greeks_submission_v3": 1,
    }
    with pytest.raises(
        runner.AttemptError, match="failed exact trusted verification"
    ) as mismatch:
        runner._run_python_solver(
            package=package,
            run_directory=tmp_path / "v3_trusted_run",
            source=_v3_delta_solver_source(),
            trusted_verification=True,
        )
    assert mismatch.value.kind == "trusted_semantic_mismatch"


def test_v3_runner_enforces_no_query_after_submission(
    portable_v3_delivery: Path, tmp_path: Path
) -> None:
    with pytest.raises(runner.AttemptError, match="closed after submission") as error:
        runner._run_python_solver(
            package=_v3_package(portable_v3_delivery),
            run_directory=tmp_path / "v3_lifecycle_run",
            source=_v3_delta_solver_source(query_after_submission=True),
            trusted_verification=False,
        )
    assert error.value.kind == "solver_execution"


def test_retry_feedback_is_protocol_specific(
    portable_v3_delivery: Path,
) -> None:
    v3_package = _v3_package(portable_v3_delivery)
    error = runner.AttemptError("solver_execution", "query failed")
    v3_output = runner._solver_error_output(error, v3_package)
    v3_feedback = runner._retry_feedback(error, v3_package)
    v2_feedback = runner._retry_feedback(error, _portable_package())

    assert "tools.query_public_duckdb_v3(sql_string)" in v3_output["instruction"]
    assert "do not query after submission" in v3_output["instruction"]
    assert "between one and ten" in v3_feedback
    assert "three trusted-tool calls exactly" in v2_feedback
    assert "query_public_duckdb_v3" not in v2_feedback


def test_generated_solver_cannot_expand_runtime_capabilities(tmp_path: Path) -> None:
    with pytest.raises(runner.AttemptError, match="import is not allowed"):
        runner._run_python_solver(
            package=_portable_package(),
            run_directory=tmp_path / "denied_run",
            source="import numpy\n\ndef solve(tools):\n    return {}\n",
            trusted_verification=True,
        )


def test_hy3_solver_tool_call_produces_verified_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        REPOSITORY_ROOT
        / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/reference_solver.py"
    ).read_text(encoding="utf-8")

    call_count = 0

    def fake_call_chat_completions(config, messages, tool_definitions):
        nonlocal call_count
        call_count += 1
        assert tool_definitions[0]["function"]["name"] == runner.RUN_PYTHON_TOOL
        if call_count == 1:
            emitted_source = "import numpy\n\ndef solve(tools):\n    return {}\n"
        else:
            assert messages[-2]["reasoning_content"] == "reasoning 1"
            error_output = json.loads(messages[-1]["content"])
            assert messages[-1]["role"] == "tool"
            assert error_output["status"] == "error"
            assert error_output["failure_kind"] == "solver_execution"
            emitted_source = source
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": f"reasoning {call_count}",
                        "tool_calls": [
                            {
                                "id": f"call_solver_{call_count}",
                                "type": "function",
                                "function": {
                                    "name": runner.RUN_PYTHON_TOOL,
                                    "arguments": json.dumps(
                                        {"source": emitted_source}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    monkeypatch.setattr(
        runner, "call_chat_completions", fake_call_chat_completions
    )
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    result = runner.direct_submission_attempt(
        package=_portable_package(),
        attempt_dir=attempt_dir,
        api_config=runner.ApiConfig(
            url=runner.DEFAULT_API_URL,
            model="hy3",
            api_key=None,
            timeout_seconds=300.0,
            retries=0,
            max_tokens=32768,
            reasoning_effort="high",
        ),
        max_agent_rounds=2,
        trusted_verification=True,
        retry_feedback=None,
    )

    assert len(result.submission["rows"]) == 160
    assert call_count == 2
    assert json.loads(
        (attempt_dir / "python_runs/run_01/result.json").read_text(
            encoding="utf-8"
        )
    )["status"] == "error"
    assert (attempt_dir / "candidate_submission.json").read_bytes() == (
        result.submission_bytes
    )
    transcript = json.loads(
        (attempt_dir / "transcript.json").read_text(encoding="utf-8")
    )
    assert transcript["messages"][-1]["role"] == "tool"
    assert '"status":"accepted"' in transcript["messages"][-1]["content"]
    assistant_messages = [
        message
        for message in transcript["messages"]
        if message["role"] == "assistant"
    ]
    assert [message["reasoning_content"] for message in assistant_messages] == [
        "reasoning 1",
        "reasoning 2",
    ]


def test_v3_run_summary_reports_actual_metric_variants(
    portable_v3_delivery: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_solve_task(**kwargs):
        package = kwargs["package"]
        submission = {"task_id": package.task_id, "rows": []}
        encoded = (json.dumps(submission, separators=(",", ":")) + "\n").encode()
        return runner.RuntimeReplayResult(
            submission=submission,
            submission_bytes=encoded,
            tool_calls={
                "query_public_duckdb_v3": 1,
                "submit_greeks_submission_v3": 1,
            },
            underlying_market_digest="underlyings",
            option_quotes_digest="options",
            submission_digest="submission",
        )

    monkeypatch.setattr(runner, "solve_task", fake_solve_task)
    output = tmp_path / "runner-output"
    arguments = runner._parser().parse_args(
        [
            "--run-root",
            str(portable_v3_delivery),
            "--output-dir",
            str(output),
            "--skip-trusted-verification",
        ]
    )
    assert runner.run(arguments) == output.resolve()
    selection = json.loads((output / "selection.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "run_summary.json").read_text(encoding="utf-8"))

    for payload in (selection, summary):
        assert "variant_id" not in payload
        assert set(payload["variant_ids"]) == set(runner.METRIC_VARIANT_IDS)
        assert set(payload["target_metrics"]) == set(TARGET_ORDER)
        assert payload["host_protocols"] == [
            runner.PORTABLE_TOOL_HOST_PROTOCOL_V3
        ]
    assert {
        (item["variant_id"], item["target_metric"], item["host_protocol"])
        for item in summary["results"]
    } == {
        (
            runner.METRIC_SPECS_DB_QUERY_V3_BY_TARGET[target].variant_id,
            target,
            runner.PORTABLE_TOOL_HOST_PROTOCOL_V3,
        )
        for target in TARGET_ORDER
    }

    v2_identity = runner._run_package_identity([_portable_package()])
    assert v2_identity["variant_id"] == runner.VARIANT_ID
    assert v2_identity["variant_ids"] == [runner.VARIANT_ID]
    assert v2_identity["target_metrics"] == []
    assert v2_identity["host_protocols"] == [runner.PORTABLE_TOOL_HOST_PROTOCOL]
