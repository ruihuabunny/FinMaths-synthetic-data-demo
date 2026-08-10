#!/usr/bin/env python3
"""Ask an external LLM for one solver and replay it across a BSM task run.

The LLM receives only the package's public prompt, runtime contract, and output
schema.  It generates a task-independent ``solve(tools)`` Python artifact.  The
repository's restricted runtime then supplies each task's public rows and writes
the canonical submission.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
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
    audit_solver_source,
    replay_solver_source,
)


VARIANT_ID = "bsm_market_implied_greeks_v1"
DEFAULT_RUN_ROOT = REPOSITORY_ROOT / "runs/bsm_market_implied_greeks"
DEFAULT_API_KEY_ENV = "LLM_API_KEY"
TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

SYSTEM_PROMPT = """\
You are producing one complete Python source file for a restricted financial
solver runtime. Return Python source only: no Markdown fences and no prose.

The source must expose solve(tools), must use the trusted tools exactly as the
public task prompt requires, and must return the same object that it submits.
Implement the declared numerical method literally with only allowed imports.
Do not use precomputed answers, task-specific constants, hidden resources,
filesystem access, networking, subprocesses, or third-party pricing libraries.
The same source will be replayed against many tasks of this variant, so derive
all identities and numerical answers from the trusted public tool results.
"""


class RunnerError(RuntimeError):
    """A concise error suitable for the command-line caller."""


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
    return TaskPackage(
        task_id=manifest["task_id"],
        root=root,
        **required,
    )


def discover_task_packages(run_root: Path) -> list[TaskPackage]:
    """Find canonical source packages and deduplicate their release views."""

    root = run_root.resolve()
    if not root.exists():
        raise RunnerError(f"run root does not exist: {root}")

    if root.is_file():
        manifests = [root]
    else:
        manifests = []
        manifests.extend(root.rglob("manifest.json"))

    by_task_id: dict[str, TaskPackage] = {}
    for manifest_path in manifests:
        if not manifest_path.is_file():
            continue
        package = _package_from_manifest(manifest_path)
        if package is None:
            continue
        current = by_task_id.get(package.task_id)
        if current is None:
            by_task_id[package.task_id] = package
            continue
        # Prefer the source package over copied release views.  The source has
        # a verifier directory, while an evaluation/train view may not.
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

    packages = [by_task_id[key] for key in sorted(by_task_id)]
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


def public_task_message(package: TaskPackage) -> str:
    """Build the LLM message exclusively from declared public artifacts."""

    prompt = package.prompt_path.read_text(encoding="utf-8")
    runtime = package.runtime_path.read_text(encoding="utf-8")
    schema = package.schema_path.read_text(encoding="utf-8")
    return (
        "Create the reusable solver requested by these public task artifacts.\n\n"
        "<public_prompt>\n"
        + prompt
        + "\n</public_prompt>\n\n<public_runtime_contract>\n"
        + runtime
        + "\n</public_runtime_contract>\n\n<public_submission_schema>\n"
        + schema
        + "\n</public_submission_schema>"
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        if pieces:
            return "".join(pieces)
    raise RunnerError("LLM response message has no text content")


def response_text(payload: Any) -> str:
    """Read the assistant text from an OpenAI-compatible chat response."""

    if not isinstance(payload, Mapping):
        raise RunnerError("LLM API response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RunnerError("LLM API response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise RunnerError("LLM API first choice is invalid")
    message = first.get("message")
    if isinstance(message, Mapping):
        return _content_text(message.get("content"))
    if isinstance(first.get("text"), str):
        return first["text"]
    raise RunnerError("LLM API first choice has no assistant message")


def extract_solver_source(text: str) -> str:
    """Accept plain source or extract the most likely fenced Python block."""

    stripped = text.strip()
    blocks = re.findall(
        r"```(?:python|py)?\s*\n?(.*?)```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if blocks:
        containing_solve = [block for block in blocks if "def solve" in block]
        stripped = (containing_solve or blocks)[0].strip()
    if not stripped:
        raise RunnerError("LLM returned an empty solver source")
    return stripped + "\n"


def _decode_http_error(error: HTTPError) -> str:
    try:
        body = error.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    suffix = f": {body}" if body else ""
    return f"HTTP {error.code}{suffix}"


def call_chat_completions(
    config: ApiConfig,
    messages: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """POST one OpenAI-compatible non-streaming chat completion request."""

    parsed = urlparse(config.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RunnerError("LLM API URL must be an absolute http(s) URL")
    body = json.dumps(
        {
            "model": config.model,
            "messages": list(messages),
            "max_tokens": config.max_tokens,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "synthetic-data-demo-llm-solver/1.0",
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


def _runtime_contract(package: TaskPackage) -> dict[str, Any]:
    runtime = _load_json(package.runtime_path)
    if not isinstance(runtime, dict):
        raise RunnerError(f"runtime contract is not an object: {package.runtime_path}")
    return runtime


def _trusted_verify(package: TaskPackage, submission: Mapping[str, Any]) -> None:
    from synthetic_derivatives.verifier.bsm_market_greeks import (
        verify_market_greeks_submission,
    )

    inputs = load_bsm_greeks_inputs(package.database_path)
    verify_market_greeks_submission(inputs, submission)


def _candidate_feedback(error: Exception, *, failure_kind: str) -> str:
    if failure_kind == "trusted_semantic_mismatch":
        detail = (
            "The source passed the public runtime and schema checks, but its "
            "canonical numeric submission failed trusted exact verification. "
            "No oracle values are provided. Recheck every stated formula, unit, "
            "binary64 operation, iteration, and Decimal rounding checkpoint."
        )
    elif failure_kind == "llm_response":
        detail = (
            "The previous API response could not be used as a complete Python "
            f"source file: {type(error).__name__}: {error}"
        )
    else:
        detail = (
            "The public runtime rejected the source: "
            f"{type(error).__name__}: {error}"
        )
    return (
        detail
        + "\nReturn the full corrected Python source only. It must remain reusable "
        "for every task ID."
    )


def generate_and_validate_solver(
    *,
    package: TaskPackage,
    output_dir: Path,
    api_config: ApiConfig,
    generation_attempts: int,
    trusted_verification: bool,
) -> Path:
    """Generate candidates until one passes a complete pilot replay."""

    if generation_attempts < 1:
        raise RunnerError("--generation-attempts must be positive")
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": public_task_message(package)},
    ]
    _write_json(output_dir / "public_llm_request.json", {"messages": messages})
    runtime = _runtime_contract(package)
    last_error: Exception | None = None

    for attempt in range(1, generation_attempts + 1):
        attempt_dir = output_dir / "generation_attempts" / f"attempt_{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        assistant_text = ""
        failure_kind = "llm_response"
        try:
            payload = call_chat_completions(api_config, messages)
            _write_json(attempt_dir / "api_response.json", payload)
            assistant_text = response_text(payload)
            source = extract_solver_source(assistant_text)
            source_path = attempt_dir / "solver.py"
            source_path.write_text(source, encoding="utf-8")

            failure_kind = "public_runtime"
            audit_solver_source(source, runtime)
            replay = replay_solver_source(
                source_path=source_path,
                database=package.database_path,
                submission_directory=attempt_dir / "pilot_submission",
                runtime_contract=runtime,
            )
            if trusted_verification:
                try:
                    _trusted_verify(package, replay.submission)
                except ValueError as error:
                    failure_kind = "trusted_semantic_mismatch"
                    raise error
        except Exception as error:
            last_error = error
            _write_json(
                attempt_dir / "result.json",
                {
                    "status": "rejected",
                    "failure_kind": failure_kind,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
            if attempt == generation_attempts:
                break
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})
            messages.append(
                {
                    "role": "user",
                    "content": _candidate_feedback(
                        error,
                        failure_kind=failure_kind,
                    ),
                }
            )
            continue

        _write_json(
            attempt_dir / "result.json",
            {
                "status": "accepted",
                "pilot_task_id": package.task_id,
                "trusted_verification": trusted_verification,
                "tool_calls": replay.tool_calls,
            },
        )
        chosen = output_dir / "solver.py"
        shutil.copyfile(source_path, chosen)
        return chosen

    assert last_error is not None
    raise RunnerError(
        f"no LLM solver passed after {generation_attempts} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def validate_existing_solver(
    *,
    source_path: Path,
    package: TaskPackage,
    output_dir: Path,
    trusted_verification: bool,
) -> Path:
    """Validate and copy an existing solver before a batch replay."""

    source = source_path.resolve()
    if not source.is_file():
        raise RunnerError(f"solver source does not exist: {source}")
    runtime = _runtime_contract(package)
    audit_solver_source(source.read_text(encoding="utf-8"), runtime)
    pilot_dir = output_dir / "existing_solver_pilot"
    replay = replay_solver_source(
        source_path=source,
        database=package.database_path,
        submission_directory=pilot_dir,
        runtime_contract=runtime,
    )
    if trusted_verification:
        _trusted_verify(package, replay.submission)
    chosen = output_dir / "solver.py"
    shutil.copyfile(source, chosen)
    _write_json(
        output_dir / "existing_solver_result.json",
        {
            "status": "accepted",
            "pilot_task_id": package.task_id,
            "trusted_verification": trusted_verification,
            "tool_calls": replay.tool_calls,
        },
    )
    return chosen


def replay_batch(
    *,
    solver_path: Path,
    packages: Sequence[TaskPackage],
    output_dir: Path,
    trusted_verification: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, package in enumerate(packages, start=1):
        submission_dir = output_dir / "submissions" / package.task_id
        replay = replay_solver_source(
            source_path=solver_path,
            database=package.database_path,
            submission_directory=submission_dir,
            runtime_contract=_runtime_contract(package),
        )
        if trusted_verification:
            _trusted_verify(package, replay.submission)
        item = {
            "task_id": package.task_id,
            "status": "verified" if trusted_verification else "runtime_passed",
            "row_count": len(replay.submission["rows"]),
            "submission": str(
                (submission_dir / "submission.json").relative_to(output_dir)
            ),
            "tool_calls": replay.tool_calls,
        }
        results.append(item)
        print(
            f"[{index:03d}/{len(packages):03d}] {package.task_id} {item['status']}",
            flush=True,
        )
    return results


def _default_output_dir(run_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parent / "results" / f"{run_root.name}_{timestamp}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one restricted BSM market-implied Greeks solver through an "
            "external OpenAI-compatible LLM API and replay it across task packages."
        )
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--solver-source",
        type=Path,
        help="reuse an existing solver.py and skip the external API call",
    )
    parser.add_argument("--api-url", default=os.environ.get("LLM_API_URL"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"))
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--api-timeout", type=float, default=180.0)
    parser.add_argument("--api-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--generation-attempts", type=int, default=3)
    parser.add_argument(
        "--skip-trusted-verification",
        action="store_true",
        help="run only the public restricted-runtime/schema checks",
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
    api_config = None if arguments.solver_source else _api_config(arguments)
    output_dir = (
        arguments.output_dir or _default_output_dir(arguments.run_root)
    ).resolve()
    if output_dir.exists():
        raise RunnerError(f"refusing to overwrite output directory: {output_dir}")
    output_dir.mkdir(parents=True)
    trusted_verification = not arguments.skip_trusted_verification

    _write_json(
        output_dir / "selection.json",
        {
            "variant_id": VARIANT_ID,
            "run_root": str(arguments.run_root.resolve()),
            "task_count": len(packages),
            "task_ids": [package.task_id for package in packages],
            "trusted_verification": trusted_verification,
            "llm_receives_public_artifacts_only": True,
        },
    )

    if arguments.solver_source:
        solver_path = validate_existing_solver(
            source_path=arguments.solver_source,
            package=packages[0],
            output_dir=output_dir,
            trusted_verification=trusted_verification,
        )
        api_summary: dict[str, Any] = {"used": False}
    else:
        assert api_config is not None
        solver_path = generate_and_validate_solver(
            package=packages[0],
            output_dir=output_dir,
            api_config=api_config,
            generation_attempts=arguments.generation_attempts,
            trusted_verification=trusted_verification,
        )
        api_summary = {
            "used": True,
            "url": api_config.url,
            "model": api_config.model,
            "api_key_environment_variable": arguments.api_key_env,
        }

    results = replay_batch(
        solver_path=solver_path,
        packages=packages,
        output_dir=output_dir,
        trusted_verification=trusted_verification,
    )
    _write_json(
        output_dir / "run_summary.json",
        {
            "status": "completed",
            "variant_id": VARIANT_ID,
            "solver": "solver.py",
            "api": api_summary,
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
