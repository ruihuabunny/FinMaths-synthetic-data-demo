#!/usr/bin/env python3
"""Let an external LLM answer BSM market-implied Greeks tasks directly.

The runner exposes exactly the three trusted tools declared by each public task:
two queries and one submission.  The LLM's ``submit_greeks_submission_v1`` tool
arguments are the answer; no intermediate solver source is generated.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.packaging.database import (  # noqa: E402
    load_bsm_greeks_inputs,
)
from synthetic_derivatives.packaging.runtime import (  # noqa: E402
    RuntimeReplayResult,
    TrustedGreeksTools,
)
from synthetic_derivatives.verifier.bsm_market_greeks import (  # noqa: E402
    verify_market_greeks_submission,
)


VARIANT_ID = "bsm_market_implied_greeks_v1"
DEFAULT_RUN_ROOT = REPOSITORY_ROOT / "runs/bsm_market_implied_greeks"
DEFAULT_API_KEY_ENV = "LLM_API_KEY"
TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
QUERY_CONTRACT_TOOL = "query_greeks_task_contract_v1"
QUERY_INPUTS_TOOL = "query_greeks_task_inputs_v1"
SUBMIT_TOOL = "submit_greeks_submission_v1"

SYSTEM_PROMPT = """\
You are the solver for one public market-implied BSM Greeks task. Interact only
through the three supplied tools and follow the public prompt literally.

Do not return Python source, Markdown, prose, or a JSON answer in ordinary chat
content. First call each query tool exactly once. After receiving both public
tool results, compute every answer yourself and call
submit_greeks_submission_v1 exactly once. The arguments of that tool call must
be the complete final submission object matching the supplied public schema.
Never use precomputed answers, hidden resources, or task-specific constants.
"""


class RunnerError(RuntimeError):
    """A concise error suitable for the command-line caller."""


class AttemptError(RunnerError):
    """One independent LLM attempt failed before producing a valid answer."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class TaskPackage:
    task_id: str
    root: Path
    prompt_path: Path
    runtime_path: Path
    schema_path: Path
    database_path: Path


@dataclass(frozen=True)
class ApiConfig:
    url: str
    model: str
    api_key: str | None
    timeout_seconds: float
    retries: int
    max_tokens: int


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunnerError(f"cannot read valid JSON from {path}") from error


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _package_from_manifest(manifest_path: Path) -> TaskPackage | None:
    try:
        manifest = _load_json(manifest_path)
    except RunnerError:
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("variant_id") != VARIANT_ID
        or not isinstance(manifest.get("task_id"), str)
    ):
        return None

    root = manifest_path.parent
    public = root / "public"
    required = {
        "prompt_path": public / "prompt.md",
        "runtime_path": public / "runtime_contract.json",
        "schema_path": public / "submission.schema.json",
        "database_path": public / "task.duckdb",
    }
    if any(not path.is_file() for path in required.values()):
        return None
    return TaskPackage(task_id=manifest["task_id"], root=root, **required)


def discover_task_packages(run_root: Path) -> list[TaskPackage]:
    """Find source packages and deduplicate their copied release views."""

    root = run_root.resolve()
    if not root.exists():
        raise RunnerError(f"run root does not exist: {root}")
    manifests = [root] if root.is_file() else list(root.rglob("manifest.json"))

    by_task_id: dict[str, TaskPackage] = {}
    for manifest_path in manifests:
        package = _package_from_manifest(manifest_path)
        if package is None:
            continue
        current = by_task_id.get(package.task_id)
        if current is None:
            by_task_id[package.task_id] = package
            continue
        candidate_rank = (
            not (package.root / "verifier").is_dir(),
            len(package.root.parts),
        )
        current_rank = (
            not (current.root / "verifier").is_dir(),
            len(current.root.parts),
        )
        if candidate_rank < current_rank:
            by_task_id[package.task_id] = package

    packages = [by_task_id[task_id] for task_id in sorted(by_task_id)]
    if not packages:
        raise RunnerError(f"no {VARIANT_ID} task packages found under {root}")
    return packages


def select_packages(
    packages: Sequence[TaskPackage],
    requested_task_ids: Sequence[str],
    limit: int | None,
) -> list[TaskPackage]:
    by_id = {package.task_id: package for package in packages}
    if requested_task_ids:
        if len(requested_task_ids) != len(set(requested_task_ids)):
            raise RunnerError("--task-id values must be unique")
        missing = sorted(set(requested_task_ids) - set(by_id))
        if missing:
            raise RunnerError(
                "requested task IDs were not found: " + ", ".join(missing)
            )
        selected = [by_id[task_id] for task_id in requested_task_ids]
    else:
        selected = list(packages)
    if limit is not None:
        if limit < 1:
            raise RunnerError("--limit must be positive")
        selected = selected[:limit]
    if not selected:
        raise RunnerError("task selection is empty")
    return selected


def _public_task_message(package: TaskPackage) -> str:
    prompt = package.prompt_path.read_text(encoding="utf-8")
    runtime = package.runtime_path.read_text(encoding="utf-8")
    schema = package.schema_path.read_text(encoding="utf-8")
    return (
        "Solve this task by using the supplied tools. Your final answer must be "
        "the arguments of submit_greeks_submission_v1, not ordinary response "
        "content.\n\n<public_prompt>\n"
        + prompt
        + "\n</public_prompt>\n\n<public_runtime_contract>\n"
        + runtime
        + "\n</public_runtime_contract>\n\n<public_submission_schema>\n"
        + schema
        + "\n</public_submission_schema>"
    )


def _submission_parameters(package: TaskPackage) -> dict[str, Any]:
    schema = _load_json(package.schema_path)
    if not isinstance(schema, dict):
        raise RunnerError("public submission schema must be a JSON object")
    supported = {"type", "additionalProperties", "required", "properties", "$defs"}
    parameters = {key: value for key, value in schema.items() if key in supported}
    if not {"type", "required", "properties"} <= set(parameters):
        raise RunnerError("public submission schema lacks required object fields")
    return parameters


def _tool_definitions(package: TaskPackage) -> list[dict[str, Any]]:
    no_arguments = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    return [
        {
            "type": "function",
            "function": {
                "name": QUERY_CONTRACT_TOOL,
                "description": (
                    "Return the one public canonical method contract. "
                    "Call exactly once."
                ),
                "parameters": no_arguments,
            },
        },
        {
            "type": "function",
            "function": {
                "name": QUERY_INPUTS_TOOL,
                "description": (
                    "Return all 160 public task rows in canonical order. "
                    "Call exactly once."
                ),
                "parameters": no_arguments,
            },
        },
        {
            "type": "function",
            "function": {
                "name": SUBMIT_TOOL,
                "description": (
                    "Submit the complete final answer after both query results. "
                    "The arguments are the submission object itself. Call exactly once."
                ),
                "parameters": _submission_parameters(package),
            },
        },
    ]


def _decode_http_error(error: HTTPError) -> str:
    try:
        body = error.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    return f"HTTP {error.code}" + (f": {body}" if body else "")


def call_chat_completions(
    config: ApiConfig,
    messages: Sequence[Mapping[str, Any]],
    tool_definitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """POST one non-streaming OpenAI-compatible tool-calling request."""

    parsed = urlparse(config.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RunnerError("LLM API URL must be an absolute http(s) URL")
    body = json.dumps(
        {
            "model": config.model,
            "messages": list(messages),
            "tools": list(tool_definitions),
            "tool_choice": "auto",
            "max_tokens": config.max_tokens,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "synthetic-data-demo-direct-llm-solver/1.0",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    last_error = "unknown API error"
    for attempt in range(config.retries + 1):
        request = Request(config.url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RunnerError("LLM API response must be a JSON object")
            return payload
        except HTTPError as error:
            last_error = _decode_http_error(error)
            retryable = error.code in TRANSIENT_HTTP_STATUSES
        except (URLError, TimeoutError, OSError) as error:
            last_error = str(error)
            retryable = True
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RunnerError("LLM API returned invalid JSON") from error
        if not retryable or attempt == config.retries:
            break
        time.sleep(min(2**attempt, 8))
    raise RunnerError(f"LLM API request failed after retries: {last_error}")


def _assistant_tool_message(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AttemptError("api_response", "LLM API response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AttemptError("api_response", "LLM API response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping) or not isinstance(first.get("message"), Mapping):
        raise AttemptError("api_response", "LLM API response has no assistant message")
    raw_message = first["message"]
    tool_calls = raw_message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        raise AttemptError(
            "tool_protocol",
            "LLM returned ordinary content instead of calling a required tool",
        )
    return {
        "role": "assistant",
        "content": raw_message.get("content"),
        "tool_calls": tool_calls,
    }


def _tool_call(call: Any) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(call, Mapping):
        raise AttemptError("tool_protocol", "LLM tool call is not an object")
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not call_id:
        raise AttemptError("tool_protocol", "LLM tool call has no ID")
    if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
        raise AttemptError("tool_protocol", "LLM tool call has no function name")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise AttemptError(
                "tool_protocol", "LLM tool arguments are not valid JSON"
            ) from error
    else:
        parsed = arguments
    if not isinstance(parsed, dict):
        raise AttemptError("tool_protocol", "LLM tool arguments must be an object")
    return call_id, function["name"], parsed


def _tool_result_message(call_id: str, name: str, result: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
    }


def _runtime_contract(package: TaskPackage) -> dict[str, Any]:
    runtime = _load_json(package.runtime_path)
    if not isinstance(runtime, dict):
        raise RunnerError("public runtime contract must be a JSON object")
    return runtime


def _verify_submission(
    package: TaskPackage,
    result: RuntimeReplayResult,
    *,
    trusted_verification: bool,
) -> None:
    if trusted_verification:
        inputs = load_bsm_greeks_inputs(package.database_path)
        try:
            verify_market_greeks_submission(inputs, result.submission)
        except ValueError as error:
            raise AttemptError(
                "trusted_semantic_mismatch",
                "LLM submission failed exact trusted verification",
            ) from error


def direct_submission_attempt(
    *,
    package: TaskPackage,
    attempt_dir: Path,
    api_config: ApiConfig,
    max_agent_rounds: int,
    trusted_verification: bool,
    retry_feedback: str | None,
) -> RuntimeReplayResult:
    """Run one fresh tool-budgeted LLM conversation for a single task."""

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _public_task_message(package)},
    ]
    if retry_feedback:
        messages.append({"role": "user", "content": retry_feedback})
    definitions = _tool_definitions(package)
    _write_json(
        attempt_dir / "public_agent_request.json",
        {
            "model": api_config.model,
            "messages": messages,
            "tools": definitions,
        },
    )

    trusted_tools = TrustedGreeksTools(
        package.database_path,
        _runtime_contract(package),
    )
    query_counts = {QUERY_CONTRACT_TOOL: 0, QUERY_INPUTS_TOOL: 0}
    try:
        for round_number in range(1, max_agent_rounds + 1):
            payload = call_chat_completions(api_config, messages, definitions)
            _write_json(
                attempt_dir / f"api_response_round_{round_number:02d}.json",
                payload,
            )
            assistant = _assistant_tool_message(payload)
            messages.append(assistant)
            calls = assistant["tool_calls"]
            queries_complete_before_round = all(
                count == 1 for count in query_counts.values()
            )

            for call_index, raw_call in enumerate(calls):
                call_id, name, arguments = _tool_call(raw_call)
                if name == QUERY_CONTRACT_TOOL:
                    if arguments:
                        raise AttemptError(
                            "tool_protocol", f"{QUERY_CONTRACT_TOOL} takes no arguments"
                        )
                    if query_counts[name]:
                        raise AttemptError(
                            "tool_protocol", f"{QUERY_CONTRACT_TOOL} was called twice"
                        )
                    result = trusted_tools.query_greeks_task_contract_v1()
                    query_counts[name] += 1
                    messages.append(_tool_result_message(call_id, name, result))
                elif name == QUERY_INPUTS_TOOL:
                    if arguments:
                        raise AttemptError(
                            "tool_protocol", f"{QUERY_INPUTS_TOOL} takes no arguments"
                        )
                    if query_counts[name]:
                        raise AttemptError(
                            "tool_protocol", f"{QUERY_INPUTS_TOOL} was called twice"
                        )
                    result = trusted_tools.query_greeks_task_inputs_v1()
                    query_counts[name] += 1
                    messages.append(_tool_result_message(call_id, name, result))
                elif name == SUBMIT_TOOL:
                    if not queries_complete_before_round:
                        raise AttemptError(
                            "tool_protocol",
                            "LLM submitted before receiving both public query results",
                        )
                    if len(calls) != 1 or call_index != 0:
                        raise AttemptError(
                            "tool_protocol",
                            "the submission must be the only tool call in its turn",
                        )
                    try:
                        trusted_tools.submit_greeks_submission_v1(arguments)
                        replay = trusted_tools.result()
                    except ValueError as error:
                        raise AttemptError(
                            "submission_schema",
                            "LLM submission violates the public schema or tool schedule",
                        ) from error
                    (attempt_dir / "candidate_submission.json").write_bytes(
                        replay.submission_bytes
                    )
                    _verify_submission(
                        package,
                        replay,
                        trusted_verification=trusted_verification,
                    )
                    _write_json(attempt_dir / "transcript.json", messages)
                    return replay
                else:
                    raise AttemptError(
                        "tool_protocol", f"LLM called unavailable tool: {name}"
                    )
        raise AttemptError(
            "tool_protocol",
            f"LLM did not submit within {max_agent_rounds} agent rounds",
        )
    except Exception:
        _write_json(attempt_dir / "transcript.json", messages)
        raise


def _retry_feedback(error: AttemptError) -> str:
    if error.kind == "trusted_semantic_mismatch":
        detail = (
            "A previous independent attempt had the correct tool/schema shape but "
            "failed exact numeric verification. No oracle values are available. "
            "Recheck the declared BSM formulas, units, 80 binary64 bisection updates, "
            "row order, and Decimal ROUND_HALF_EVEN serialization."
        )
    else:
        detail = f"A previous independent attempt failed: {error}"
    return detail + " Start again and obey the three-tool schedule exactly."


def solve_task(
    *,
    package: TaskPackage,
    task_output: Path,
    api_config: ApiConfig,
    task_attempts: int,
    max_agent_rounds: int,
    trusted_verification: bool,
) -> RuntimeReplayResult:
    feedback: str | None = None
    last_error: AttemptError | None = None
    for attempt in range(1, task_attempts + 1):
        attempt_dir = task_output / "attempts" / f"attempt_{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        try:
            result = direct_submission_attempt(
                package=package,
                attempt_dir=attempt_dir,
                api_config=api_config,
                max_agent_rounds=max_agent_rounds,
                trusted_verification=trusted_verification,
                retry_feedback=feedback,
            )
        except AttemptError as error:
            last_error = error
            _write_json(
                attempt_dir / "result.json",
                {
                    "status": "rejected",
                    "failure_kind": error.kind,
                    "error": str(error),
                },
            )
            feedback = _retry_feedback(error)
            continue
        _write_json(
            attempt_dir / "result.json",
            {
                "status": "accepted",
                "task_id": package.task_id,
                "tool_calls": result.tool_calls,
                "trusted_verification": trusted_verification,
            },
        )
        return result
    assert last_error is not None
    raise RunnerError(
        f"task {package.task_id} failed after {task_attempts} attempts: {last_error}"
    ) from last_error


def _prepare_output_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise RunnerError(f"output path is not a directory: {path}")
        if any(path.iterdir()):
            raise RunnerError(f"refusing to overwrite non-empty output directory: {path}")
        return
    path.mkdir(parents=True)


def _default_output_dir(run_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parent / "results" / f"{run_root.name}_{timestamp}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Let an external OpenAI-compatible tool-calling LLM directly submit "
            "answers for BSM market-implied Greeks task packages."
        )
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--api-url", default=os.environ.get("LLM_API_URL"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"))
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--api-timeout", type=float, default=300.0)
    parser.add_argument("--api-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--task-attempts", type=int, default=2)
    parser.add_argument("--max-agent-rounds", type=int, default=4)
    parser.add_argument(
        "--skip-trusted-verification",
        action="store_true",
        help="enforce public tool/schema rules without exact QuantLib verification",
    )
    return parser


def _api_config(arguments: argparse.Namespace) -> ApiConfig:
    if not arguments.api_url:
        raise RunnerError("set --api-url or LLM_API_URL")
    if not arguments.model:
        raise RunnerError("set --model or LLM_MODEL")
    if arguments.api_timeout <= 0:
        raise RunnerError("--api-timeout must be positive")
    if arguments.api_retries < 0:
        raise RunnerError("--api-retries must be nonnegative")
    if arguments.max_tokens < 1:
        raise RunnerError("--max-tokens must be positive")
    if arguments.task_attempts < 1:
        raise RunnerError("--task-attempts must be positive")
    if arguments.max_agent_rounds < 2:
        raise RunnerError("--max-agent-rounds must be at least 2")
    return ApiConfig(
        url=arguments.api_url,
        model=arguments.model,
        api_key=os.environ.get(arguments.api_key_env),
        timeout_seconds=arguments.api_timeout,
        retries=arguments.api_retries,
        max_tokens=arguments.max_tokens,
    )


def run(arguments: argparse.Namespace) -> Path:
    packages = select_packages(
        discover_task_packages(arguments.run_root),
        arguments.task_id,
        arguments.limit,
    )
    api_config = _api_config(arguments)
    output_dir = (
        arguments.output_dir or _default_output_dir(arguments.run_root)
    ).resolve()
    _prepare_output_directory(output_dir)
    trusted_verification = not arguments.skip_trusted_verification
    _write_json(
        output_dir / "selection.json",
        {
            "variant_id": VARIANT_ID,
            "run_root": str(arguments.run_root.resolve()),
            "task_count": len(packages),
            "task_ids": [package.task_id for package in packages],
            "llm_submission_mode": "direct_trusted_tool_call",
            "trusted_verification": trusted_verification,
            "api": {"url": api_config.url, "model": api_config.model},
        },
    )

    results: list[dict[str, Any]] = []
    for index, package in enumerate(packages, start=1):
        task_output = output_dir / "tasks" / package.task_id
        result = solve_task(
            package=package,
            task_output=task_output,
            api_config=api_config,
            task_attempts=arguments.task_attempts,
            max_agent_rounds=arguments.max_agent_rounds,
            trusted_verification=trusted_verification,
        )
        submission_path = output_dir / "submissions" / package.task_id / "submission.json"
        submission_path.parent.mkdir(parents=True, exist_ok=False)
        submission_path.write_bytes(result.submission_bytes)
        item = {
            "task_id": package.task_id,
            "status": "verified" if trusted_verification else "schema_passed",
            "row_count": len(result.submission["rows"]),
            "submission": str(submission_path.relative_to(output_dir)),
            "tool_calls": result.tool_calls,
        }
        results.append(item)
        print(
            f"[{index:03d}/{len(packages):03d}] {package.task_id} {item['status']}",
            flush=True,
        )

    _write_json(
        output_dir / "run_summary.json",
        {
            "status": "completed",
            "variant_id": VARIANT_ID,
            "submission_mode": "direct_trusted_tool_call",
            "task_count": len(results),
            "trusted_verification": trusted_verification,
            "results": results,
        },
    )
    return output_dir


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        output_dir = run(arguments)
    except (RunnerError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"completed: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
