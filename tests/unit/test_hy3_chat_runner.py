from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from llm_solutions import run_bsm_market_implied_greeks as runner


def _checked_in_package() -> runner.TaskPackage:
    manifest_path = next(
        (
            REPOSITORY_ROOT / "task_packages/bsm_market_implied_greeks_v1"
        ).glob("*/manifest.json")
    )
    package = runner._package_from_manifest(manifest_path)
    assert package is not None
    return package


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


def test_generated_solver_runs_in_frozen_harness(tmp_path: Path) -> None:
    source = (
        REPOSITORY_ROOT
        / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/reference_solver.py"
    ).read_text(encoding="utf-8")

    result = runner._run_python_solver(
        package=_checked_in_package(),
        run_directory=tmp_path / "python_run",
        source=source,
        trusted_verification=True,
    )

    assert len(result.submission["rows"]) == 160
    assert result.tool_calls == {
        "query_greeks_task_contract_v1": 1,
        "query_greeks_task_inputs_v1": 1,
        "submit_greeks_submission_v1": 1,
    }


def test_generated_solver_cannot_expand_runtime_capabilities(tmp_path: Path) -> None:
    with pytest.raises(runner.AttemptError, match="import is not allowed"):
        runner._run_python_solver(
            package=_checked_in_package(),
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
        package=_checked_in_package(),
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
