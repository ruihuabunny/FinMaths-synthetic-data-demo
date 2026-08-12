#!/usr/bin/env python3
"""Let Hy3 write and run a solver for BSM market-implied Greeks tasks.

Hy3 submits complete Python source to one agent-side execution tool.  The source
is audited and replayed in the repository's restricted solver harness, where it
must use exactly the three trusted tools declared by the public task.
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

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (  # noqa: E402
    load_bsm_greeks_inputs,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (  # noqa: E402
    CapabilityViolation,
    RuntimeReplayResult,
    replay_solver_source,
)
from synthetic_derivatives.verifier.bsm_market_greeks import (  # noqa: E402
    verify_market_greeks_submission,
)


VARIANT_ID = "bsm_market_implied_greeks_v1"
DEFAULT_RUN_ROOT = REPOSITORY_ROOT / "runs/bsm_market_implied_greeks"
DEFAULT_API_URL = "https://tokenhub.tencentmaas.com/v1/chat/completions"
DEFAULT_MODEL = "hy3"
DEFAULT_API_KEY_ENV = "TOKENHUB_API_KEY"
TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
RUN_PYTHON_TOOL = "run_python_solver_v1"
MAX_SOLVER_SOURCE_BYTES = 131_072

SYSTEM_PROMPT = """\
You are a coding agent for one public market-implied BSM Greeks task. Follow the
public prompt literally and call run_python_solver_v1 with a complete Python
3.12 solver. Do not return source, Markdown, prose, or JSON in ordinary response
content.

The source must define solve(tools). Inside solve, call each public query tool
exactly once, compute every row using the frozen numerical method, call
submit_greeks_submission_v1 exactly once, and return the identical submission
object. The restricted harness supplies only the imports and trusted tools in
the public runtime contract. It has no raw database, network, subprocess,
verifier, reference answer, or private resource access.

If the execution tool returns an error, correct the source and call it again.
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
    reasoning_effort: str


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
        "Write a complete solver for this task and execute it with "
        "run_python_solver_v1. The solver itself must use the public trusted "
        "tools exactly as declared.\n\n<public_prompt>\n"
        + prompt
        + "\n</public_prompt>\n\n<public_runtime_contract>\n"
        + runtime
        + "\n</public_runtime_contract>\n\n<public_submission_schema>\n"
        + schema
        + "\n</public_submission_schema>"
    )


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": RUN_PYTHON_TOOL,
                "description": (
                    "Audit and execute a complete Python 3.12 solver in the frozen "
                    "restricted task harness. The source must define solve(tools), "
                    "use the declared trusted tools, submit the result, and return "
                    "that same result. Fix and call again if execution reports an error."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source"],
                    "properties": {
                        "source": {
                            "type": "string",
                            "maxLength": MAX_SOLVER_SOURCE_BYTES,
                            "description": (
                                "Complete solver.py source defining solve(tools)."
                            ),
                        }
                    },
                },
            },
        },
    ]


def _decode_http_error(error: HTTPError) -> str:
    try:
        body = error.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    return f"HTTP {error.code}" + (f": {body}" if body else "")


def _chat_request_body(
    config: ApiConfig,
    messages: Sequence[Mapping[str, Any]],
    tool_definitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": list(messages),
        "tools": list(tool_definitions),
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning_effort": config.reasoning_effort,
        "max_tokens": config.max_tokens,
        "stream": False,
    }


def call_chat_completions(
    config: ApiConfig,
    messages: Sequence[Mapping[str, Any]],
    tool_definitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """POST one non-streaming Hy3 Chat Completions tool-calling request."""

    parsed = urlparse(config.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RunnerError("TokenHub API URL must be an absolute http(s) URL")
    body = json.dumps(
        _chat_request_body(config, messages, tool_definitions),
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "synthetic-data-demo-hy3-coding-runner/1.0",
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
    raise RunnerError(f"TokenHub API request failed after retries: {last_error}")


def _assistant_tool_message(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AttemptError("api_response", "Hy3 API response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AttemptError("api_response", "Hy3 API response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping) or not isinstance(first.get("message"), Mapping):
        raise AttemptError("api_response", "Hy3 API response has no assistant message")
    message = dict(first["message"])
    if message.get("role") != "assistant":
        raise AttemptError("api_response", "Hy3 response message is not assistant")
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        raise AttemptError(
            "tool_protocol",
            "Hy3 returned ordinary output instead of calling a required tool",
        )
    return message


def _tool_call(call: Any) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(call, Mapping):
        raise AttemptError("tool_protocol", "Hy3 tool call is not an object")
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not call_id:
        raise AttemptError("tool_protocol", "Hy3 tool call has no ID")
    if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
        raise AttemptError("tool_protocol", "Hy3 tool call has no function name")
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
        raise AttemptError("tool_protocol", "Hy3 function arguments must be an object")
    return call_id, function["name"], parsed


def _tool_result_message(call_id: str, result: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
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
                "generated solver submission failed exact trusted verification",
            ) from error


def _run_python_solver(
    *,
    package: TaskPackage,
    run_directory: Path,
    source: str,
    trusted_verification: bool,
) -> RuntimeReplayResult:
    run_directory.mkdir(parents=True, exist_ok=False)
    if not source.strip():
        raise AttemptError("solver_source", "solver source must not be empty")
    if len(source.encode("utf-8")) > MAX_SOLVER_SOURCE_BYTES:
        raise AttemptError(
            "solver_source",
            f"solver source exceeds {MAX_SOLVER_SOURCE_BYTES} UTF-8 bytes",
        )

    source_path = run_directory / "solver.py"
    source_path.write_text(source, encoding="utf-8")
    try:
        result = replay_solver_source(
            source_path=source_path,
            database=package.database_path,
            submission_directory=run_directory / "submission",
            runtime_contract=_runtime_contract(package),
        )
    except (CapabilityViolation, OSError, ValueError) as error:
        raise AttemptError("solver_execution", str(error)) from error
    _verify_submission(
        package,
        result,
        trusted_verification=trusted_verification,
    )
    return result


def _solver_error_output(error: AttemptError) -> dict[str, str]:
    message = str(error)
    if len(message) > 2000:
        message = message[:2000] + "..."
    return {
        "status": "error",
        "failure_kind": error.kind,
        "error": message,
        "instruction": "Correct the complete solver source and call the tool again.",
    }


def direct_submission_attempt(
    *,
    package: TaskPackage,
    attempt_dir: Path,
    api_config: ApiConfig,
    max_agent_rounds: int,
    trusted_verification: bool,
    retry_feedback: str | None,
) -> RuntimeReplayResult:
    """Run one fresh Hy3 coding conversation for a single task."""

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _public_task_message(package)},
    ]
    if retry_feedback:
        messages.append({"role": "user", "content": retry_feedback})
    definitions = _tool_definitions()
    _write_json(
        attempt_dir / "public_agent_request.json",
        _chat_request_body(api_config, messages, definitions),
    )

    last_solver_error: AttemptError | None = None
    try:
        for round_number in range(1, max_agent_rounds + 1):
            payload = call_chat_completions(api_config, messages, definitions)
            _write_json(
                attempt_dir / f"api_response_round_{round_number:02d}.json",
                payload,
            )
            assistant_message = _assistant_tool_message(payload)
            messages.append(assistant_message)
            calls = assistant_message["tool_calls"]
            if len(calls) != 1:
                raise AttemptError(
                    "tool_protocol",
                    f"Hy3 must make exactly one {RUN_PYTHON_TOOL} call per turn",
                )
            call_id, name, arguments = _tool_call(calls[0])
            if name != RUN_PYTHON_TOOL:
                raise AttemptError(
                    "tool_protocol", f"Hy3 called unavailable tool: {name}"
                )
            if set(arguments) != {"source"} or not isinstance(
                arguments["source"], str
            ):
                raise AttemptError(
                    "tool_protocol",
                    f"{RUN_PYTHON_TOOL} requires one string field named source",
                )

            run_directory = (
                attempt_dir / "python_runs" / f"run_{round_number:02d}"
            )
            try:
                result = _run_python_solver(
                    package=package,
                    run_directory=run_directory,
                    source=arguments["source"],
                    trusted_verification=trusted_verification,
                )
            except AttemptError as error:
                last_solver_error = error
                error_output = _solver_error_output(error)
                _write_json(run_directory / "result.json", error_output)
                messages.append(_tool_result_message(call_id, error_output))
                continue

            accepted_output = {
                "status": "accepted",
                "task_id": package.task_id,
                "row_count": len(result.submission["rows"]),
                "trusted_verification": trusted_verification,
            }
            _write_json(run_directory / "result.json", accepted_output)
            messages.append(_tool_result_message(call_id, accepted_output))
            (attempt_dir / "candidate_submission.json").write_bytes(
                result.submission_bytes
            )
            _write_json(
                attempt_dir / "transcript.json",
                {"messages": messages},
            )
            return result
        if last_solver_error is not None:
            raise AttemptError(
                last_solver_error.kind,
                f"Hy3 failed all {max_agent_rounds} solver runs: "
                f"{last_solver_error}",
            ) from last_solver_error
        raise AttemptError(
            "tool_protocol",
            f"Hy3 did not call the solver tool in {max_agent_rounds} rounds",
        )
    except Exception:
        _write_json(
            attempt_dir / "transcript.json",
            {"messages": messages},
        )
        raise


def _retry_feedback(error: AttemptError) -> str:
    if error.kind == "trusted_semantic_mismatch":
        detail = (
            "A previous generated solver passed the tool/schema checks but failed "
            "exact numeric verification. No oracle values are available. "
            "Recheck the declared BSM formulas, units, 80 binary64 bisection updates, "
            "row order, and Decimal ROUND_HALF_EVEN serialization."
        )
    else:
        detail = f"A previous independent attempt failed: {error}"
    return (
        detail
        + " Start again, call the Python solver tool, and make the generated "
        "solve(tools) obey the three trusted-tool calls exactly."
    )


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
            "Let Hy3 use TokenHub's Chat Completions API to write and execute "
            "restricted solvers for BSM market-implied Greeks task packages."
        )
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("TOKENHUB_API_URL", DEFAULT_API_URL),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("TOKENHUB_MODEL", DEFAULT_MODEL),
    )
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--api-timeout", type=float, default=300.0)
    parser.add_argument("--api-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high"),
        default="high",
    )
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
        raise RunnerError("set --api-url or TOKENHUB_API_URL")
    if not arguments.model:
        raise RunnerError("set --model or TOKENHUB_MODEL")
    if arguments.api_timeout <= 0:
        raise RunnerError("--api-timeout must be positive")
    if arguments.api_retries < 0:
        raise RunnerError("--api-retries must be nonnegative")
    if arguments.max_tokens < 1:
        raise RunnerError("--max-tokens must be positive")
    if arguments.task_attempts < 1:
        raise RunnerError("--task-attempts must be positive")
    if arguments.max_agent_rounds < 1:
        raise RunnerError("--max-agent-rounds must be positive")
    return ApiConfig(
        url=arguments.api_url,
        model=arguments.model,
        api_key=os.environ.get(arguments.api_key_env),
        timeout_seconds=arguments.api_timeout,
        retries=arguments.api_retries,
        max_tokens=arguments.max_tokens,
        reasoning_effort=arguments.reasoning_effort,
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
            "llm_submission_mode": "generated_solver_restricted_replay",
            "trusted_verification": trusted_verification,
            "api": {
                "protocol": "openai_chat_completions",
                "url": api_config.url,
                "model": api_config.model,
                "reasoning_effort": api_config.reasoning_effort,
            },
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
            "submission_mode": "generated_solver_restricted_replay",
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
