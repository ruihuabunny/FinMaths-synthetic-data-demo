"""Compose and audit the effective allowlist for one solver task."""

from __future__ import annotations

import ast
import builtins
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import multiprocessing
import os
from pathlib import Path
import resource
from typing import Any

from synthetic_derivatives.packaging.contracts import (
    RUNTIME_CONTRACT_SCHEMA_VERSION,
    canonical_json_bytes,
    digest_json,
)
from synthetic_derivatives.tasks.bsm_market_greeks import MarketGreeksSubmission


class CapabilityViolation(ValueError):
    """A task overlay or solver source attempted an undeclared capability."""


_PROFILE_FIELDS = {
    "profile_kind",
    "profile_id",
    "environment_id",
    "python_version",
    "allowed_direct_imports",
    "trusted_tools",
    "filesystem",
    "network",
    "dynamic_installation",
    "process_spawning",
    "resource_budget",
    "denied_imports",
    "denied_operations",
}
_FORBIDDEN_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "input",
    "open",
}
_FORBIDDEN_ATTRIBUTE_NAMES = {
    "Popen",
    "attach",
    "connect",
    "copy",
    "install",
    "load",
    "popen",
    "run",
    "system",
    "urlopen",
}
_FORBIDDEN_SOURCE_TOKENS = (
    "authoring_private",
    "duckdb",
    "pip install",
    "private parent",
    "py_vollib",
    "quantlib",
    "reference/",
    "verifier/",
)
_FORBIDDEN_METHOD_IDENTIFIERS = {"allclose", "atol", "isclose", "rtol", "tol", "tolerance"}


_SAFE_BUILTINS = {
    "ArithmeticError",
    "AssertionError",
    "Exception",
    "KeyError",
    "RuntimeError",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
    "__build_class__",
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "callable",
    "dict",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hash",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "pow",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "zip",
}


def _require_profile_shape(raw: Mapping[str, Any], *, overlay: bool) -> None:
    expected = _PROFILE_FIELDS | ({"global_profile_id"} if overlay else set())
    if set(raw) != expected:
        raise CapabilityViolation("capability profile has missing or extra fields")
    kind = "task_overlay" if overlay else "global_allowlist"
    if raw["profile_kind"] != kind:
        raise CapabilityViolation(f"capability profile must be {kind}")


def _string_set(raw: Any, field: str) -> set[str]:
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item for item in raw
    ):
        raise CapabilityViolation(f"{field} must be a list of strings")
    if len(raw) != len(set(raw)):
        raise CapabilityViolation(f"{field} must be unique")
    return set(raw)


def compose_runtime_contract(
    global_profile: Mapping[str, Any], task_overlay: Mapping[str, Any]
) -> dict[str, Any]:
    """Intersect a global allowlist with a strictly restrictive task overlay."""

    _require_profile_shape(global_profile, overlay=False)
    _require_profile_shape(task_overlay, overlay=True)
    if task_overlay["global_profile_id"] != global_profile["profile_id"]:
        raise CapabilityViolation("task overlay references a different global profile")
    for field in ("environment_id", "python_version"):
        if task_overlay[field] != global_profile[field]:
            raise CapabilityViolation(f"task overlay changes {field}")

    global_imports = _string_set(
        global_profile["allowed_direct_imports"], "global allowed imports"
    )
    overlay_imports = _string_set(
        task_overlay["allowed_direct_imports"], "task allowed imports"
    )
    if not overlay_imports <= global_imports:
        raise CapabilityViolation("task overlay cannot expand allowed imports")

    global_tools = global_profile["trusted_tools"]
    overlay_tools = task_overlay["trusted_tools"]
    if not isinstance(global_tools, Mapping) or not isinstance(
        overlay_tools, Mapping
    ):
        raise CapabilityViolation("trusted_tools must be mappings")
    if not set(overlay_tools) <= set(global_tools):
        raise CapabilityViolation("task overlay cannot add trusted tools")
    tools = []
    for name in sorted(overlay_tools):
        task_limit = overlay_tools[name]
        global_limit = global_tools[name]
        if (
            type(task_limit) is not int
            or type(global_limit) is not int
            or task_limit < 1
            or task_limit > global_limit
        ):
            raise CapabilityViolation("task overlay expands a trusted-tool budget")
        tools.append({"name": name, "max_calls": task_limit})

    filesystem: dict[str, list[str]] = {}
    for access in ("read", "write"):
        global_paths = _string_set(
            global_profile["filesystem"][access], f"global filesystem {access}"
        )
        overlay_paths = _string_set(
            task_overlay["filesystem"][access], f"task filesystem {access}"
        )
        if not overlay_paths <= global_paths:
            raise CapabilityViolation("task overlay cannot expand filesystem access")
        filesystem[access] = sorted(overlay_paths)

    booleans: dict[str, bool] = {}
    for field in ("network", "dynamic_installation", "process_spawning"):
        global_value = global_profile[field]
        overlay_value = task_overlay[field]
        if type(global_value) is not bool or type(overlay_value) is not bool:
            raise CapabilityViolation(f"{field} must be boolean")
        if overlay_value and not global_value:
            raise CapabilityViolation(f"task overlay cannot enable {field}")
        booleans[field] = global_value and overlay_value

    global_budget = global_profile["resource_budget"]
    overlay_budget = task_overlay["resource_budget"]
    if set(global_budget) != set(overlay_budget):
        raise CapabilityViolation("resource budget fields differ between profiles")
    budget: dict[str, int] = {}
    for field in sorted(global_budget):
        global_limit = global_budget[field]
        task_limit = overlay_budget[field]
        if (
            type(global_limit) is not int
            or type(task_limit) is not int
            or task_limit < 1
            or task_limit > global_limit
        ):
            raise CapabilityViolation("task overlay expands a resource budget")
        budget[field] = task_limit

    denied_imports = _string_set(
        global_profile["denied_imports"], "global denied imports"
    ) | _string_set(task_overlay["denied_imports"], "task denied imports")
    denied_operations = _string_set(
        global_profile["denied_operations"], "global denied operations"
    ) | _string_set(
        task_overlay["denied_operations"], "task denied operations"
    )
    if overlay_imports & denied_imports:
        raise CapabilityViolation("an import cannot be both allowed and denied")

    return {
        "runtime_contract_schema_version": RUNTIME_CONTRACT_SCHEMA_VERSION,
        "environment_id": global_profile["environment_id"],
        "profile_id": "bsm-greeks-effective-v1",
        "global_profile_id": global_profile["profile_id"],
        "task_overlay_id": task_overlay["profile_id"],
        "python_version": global_profile["python_version"],
        "allowed_direct_imports": sorted(overlay_imports),
        "trusted_tools": tools,
        "filesystem": filesystem,
        **booleans,
        "resource_budget": budget,
        "denied_imports": sorted(denied_imports),
        "denied_operations": sorted(denied_operations),
    }


def audit_solver_source(source: str, runtime_contract: Mapping[str, Any]) -> None:
    """Reject direct imports and operations outside the effective contract."""

    if not isinstance(source, str) or not source.strip():
        raise CapabilityViolation("solver source must be non-empty text")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise CapabilityViolation("solver source is not valid Python") from error
    allowed = set(runtime_contract["allowed_direct_imports"])
    denied = {item.casefold() for item in runtime_contract["denied_imports"]}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise CapabilityViolation("relative imports are not allowed")
            modules = [(node.module or "").split(".", 1)[0]]
        else:
            modules = []
        for module in modules:
            if module == "__future__":
                continue
            if module.casefold() in denied or module not in allowed:
                raise CapabilityViolation(f"solver import is not allowed: {module}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALL_NAMES:
                raise CapabilityViolation(
                    f"solver call is not allowed: {node.func.id}"
                )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _FORBIDDEN_ATTRIBUTE_NAMES
            ):
                raise CapabilityViolation(
                    f"solver operation is not allowed: {node.func.attr}"
                )
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "range"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in {79, 81}
            ):
                raise CapabilityViolation("solver source changes the 80-step schedule")
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "float"
                and node.args
                and isinstance(node.args[0], ast.Subscript)
                and isinstance(node.args[0].slice, ast.Constant)
                and node.args[0].slice.value in {"bid", "ask"}
            ):
                raise CapabilityViolation(
                    "solver must construct the Decimal quote midpoint before float"
                )
        if isinstance(node, ast.Break):
            raise CapabilityViolation("solver source cannot early-stop an iteration")
        if isinstance(node, ast.Name) and node.id.casefold() in (
            _FORBIDDEN_METHOD_IDENTIFIERS
        ):
            raise CapabilityViolation(
                f"solver source uses a forbidden tolerance identifier: {node.id}"
            )
    source_casefold = source.casefold()
    for token in _FORBIDDEN_SOURCE_TOKENS:
        if token in source_casefold:
            raise CapabilityViolation(f"solver source references denied resource: {token}")


@dataclass(frozen=True)
class RuntimeReplayResult:
    """Observable result and audit from one tool-budgeted solver replay."""

    submission: dict[str, Any]
    submission_bytes: bytes
    tool_calls: dict[str, int]
    contract_digest: str
    input_digest: str
    submission_digest: str


class TrustedGreeksTools:
    """Counted query/submit adapter; the solver never receives a DB handle."""

    def __init__(self, database: str | Path, runtime_contract: Mapping[str, Any]):
        from synthetic_derivatives.packaging.database import (
            load_bsm_greeks_contract,
            load_bsm_greeks_inputs,
        )

        self._contract = load_bsm_greeks_contract(database)
        self._inputs = tuple(load_bsm_greeks_inputs(database))
        self._runtime = dict(runtime_contract)
        self._limits = {
            str(item["name"]): int(item["max_calls"])
            for item in runtime_contract["trusted_tools"]
        }
        self._calls = {name: 0 for name in self._limits}
        self._submission: dict[str, Any] | None = None
        self._submission_bytes: bytes | None = None
        self._query_call_count = 0

    def _count(self, name: str, *, query: bool = False) -> None:
        if name not in self._limits:
            raise CapabilityViolation(f"trusted tool is not available: {name}")
        if self._calls[name] >= self._limits[name]:
            raise CapabilityViolation(f"trusted tool call budget exceeded: {name}")
        if query:
            self._query_call_count += 1
            if self._query_call_count > self._runtime["resource_budget"][
                "trusted_query_calls"
            ]:
                raise CapabilityViolation("total trusted query budget exceeded")
        self._calls[name] += 1

    def query_greeks_task_contract_v1(self) -> dict[str, Any]:
        self._count("query_greeks_task_contract_v1", query=True)
        return dict(self._contract)

    def query_greeks_task_inputs_v1(self) -> list[dict[str, Any]]:
        self._count("query_greeks_task_inputs_v1", query=True)
        return [row.to_tool_mapping() for row in self._inputs]

    def submit_greeks_submission_v1(self, payload: Mapping[str, Any]) -> None:
        self._count("submit_greeks_submission_v1")
        if self._submission is not None:
            raise CapabilityViolation("a submission was already recorded")
        try:
            submission = MarketGreeksSubmission.from_mapping(payload)
        except (TypeError, ValueError) as error:
            raise CapabilityViolation("submission violates its public schema") from error
        if submission.task_id != self._inputs[0].task_id:
            raise CapabilityViolation("submission task ID differs from the queried task")
        encoded = canonical_json_bytes(submission.to_dict())
        if len(encoded) > self._runtime["resource_budget"]["submission_bytes"]:
            raise CapabilityViolation("submission exceeds the byte budget")
        self._submission = submission.to_dict()
        self._submission_bytes = encoded

    def result(self) -> RuntimeReplayResult:
        if self._submission is None or self._submission_bytes is None:
            raise CapabilityViolation("solver did not submit a result")
        required = {
            "query_greeks_task_contract_v1": 1,
            "query_greeks_task_inputs_v1": 1,
            "submit_greeks_submission_v1": 1,
        }
        if self._calls != required:
            raise CapabilityViolation("solver did not use the frozen tool schedule")
        input_payload = [row.to_tool_mapping() for row in self._inputs]
        return RuntimeReplayResult(
            submission=self._submission,
            submission_bytes=self._submission_bytes,
            tool_calls=dict(self._calls),
            contract_digest=digest_json(self._contract),
            input_digest=digest_json(input_payload),
            submission_digest=sha256(self._submission_bytes).hexdigest(),
        )


def _restricted_builtins(runtime_contract: Mapping[str, Any]) -> dict[str, Any]:
    allowed = set(runtime_contract["allowed_direct_imports"])
    denied = {item.casefold() for item in runtime_contract["denied_imports"]}

    def restricted_import(
        name: str,
        globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        top_level = name.split(".", 1)[0]
        if top_level == "__future__" and not level:
            return builtins.__import__(name, globals, locals, fromlist, level)
        if level or top_level not in allowed or top_level.casefold() in denied:
            raise CapabilityViolation(f"runtime import is not allowed: {name}")
        return builtins.__import__(name, globals, locals, fromlist, level)

    result = {name: getattr(builtins, name) for name in _SAFE_BUILTINS}
    result["__import__"] = restricted_import
    return result


def _apply_process_limits(runtime_contract: Mapping[str, Any]) -> None:
    budget = runtime_contract["resource_budget"]
    memory_bytes = int(budget["memory_mib"]) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    cpu_seconds = int(budget["wall_clock_seconds"]) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        available = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, set(available[: int(budget["vcpu"])]))


def _solver_process(
    connection: Any,
    *,
    source: str,
    source_name: str,
    tools: TrustedGreeksTools,
    runtime_contract: Mapping[str, Any],
) -> None:
    try:
        _apply_process_limits(runtime_contract)
        namespace = {
            "__builtins__": _restricted_builtins(runtime_contract),
            "__file__": source_name,
            "__name__": "__solver__",
            "__package__": None,
        }
        code = compile(source, source_name, "exec")
        exec(code, namespace)
        solve = namespace.get("solve")
        if not callable(solve):
            raise CapabilityViolation("solver artifact does not expose solve(tools)")
        returned = solve(tools)
        result = tools.result()
        if returned != result.submission:
            raise CapabilityViolation("solver return value differs from its submission")
        connection.send(("ok", result))
    except BaseException as error:
        connection.send(("error", (type(error).__name__, str(error))))
    finally:
        connection.close()


def replay_solver_source(
    *,
    source_path: str | Path,
    database: str | Path,
    submission_directory: str | Path,
    runtime_contract: Mapping[str, Any],
) -> RuntimeReplayResult:
    """Audit and execute the solver in a resource-limited trusted harness."""

    source = Path(source_path).read_text(encoding="utf-8")
    audit_solver_source(source, runtime_contract)
    tools = TrustedGreeksTools(database, runtime_contract)
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_solver_process,
        kwargs={
            "connection": child_connection,
            "source": source,
            "source_name": str(source_path),
            "tools": tools,
            "runtime_contract": dict(runtime_contract),
        },
    )
    process.start()
    child_connection.close()
    process.join(runtime_contract["resource_budget"]["wall_clock_seconds"])
    if process.is_alive():
        process.terminate()
        process.join()
        parent_connection.close()
        raise CapabilityViolation("solver exceeded the wall-clock budget")
    if not parent_connection.poll():
        parent_connection.close()
        raise CapabilityViolation(
            f"solver process exited without a result (exit code {process.exitcode})"
        )
    status, payload = parent_connection.recv()
    parent_connection.close()
    if status != "ok":
        error_type, message = payload
        raise CapabilityViolation(f"solver failed with {error_type}: {message}")
    result = payload
    if not isinstance(result, RuntimeReplayResult):
        raise CapabilityViolation("solver process returned an invalid audit result")
    output = Path(submission_directory)
    output.mkdir(parents=True, exist_ok=True)
    submission_path = output / "submission.json"
    if submission_path.exists():
        raise FileExistsError(f"refusing to overwrite submission: {submission_path}")
    submission_path.write_bytes(result.submission_bytes)
    return result


__all__ = [
    "CapabilityViolation",
    "RuntimeReplayResult",
    "TrustedGreeksTools",
    "audit_solver_source",
    "compose_runtime_contract",
    "replay_solver_source",
]
