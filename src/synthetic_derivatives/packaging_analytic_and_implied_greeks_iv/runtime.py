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
from threading import Event, Thread
import types
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
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
    "builtins",
    "connect",
    "copy",
    "environ",
    "getenv",
    "glob",
    "inspect",
    "install",
    "iterdir",
    "listdir",
    "load",
    "modules",
    "open",
    "os",
    "popen",
    "read_bytes",
    "read_text",
    "rglob",
    "run",
    "scandir",
    "socket",
    "system",
    "sys",
    "urlopen",
    "walk",
    "write_bytes",
    "write_text",
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


class _TargetLoopBreakFinder(ast.NodeVisitor):
    """Find a break targeting one loop without descending into nested loops."""

    def __init__(self) -> None:
        self.found = False

    def visit_Break(self, node: ast.Break) -> None:  # noqa: N802
        self.found = True

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        return

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        return

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        return


def _fixed_schedule_loop_breaks(loop: ast.For) -> bool:
    iterator = loop.iter
    if not (
        isinstance(iterator, ast.Call)
        and isinstance(iterator.func, ast.Name)
        and iterator.func.id == "range"
        and len(iterator.args) == 1
        and isinstance(iterator.args[0], ast.Constant)
        and iterator.args[0].value == 80
    ):
        return False
    finder = _TargetLoopBreakFinder()
    for statement in loop.body:
        finder.visit(statement)
    return finder.found


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
        "profile_id": "bsm-greeks-effective-v2",
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


def compose_runtime_contract_v3(
    global_profile: Mapping[str, Any], task_overlay: Mapping[str, Any]
) -> dict[str, Any]:
    """Compose the frozen trusted-DuckDB v3 solver capability contract."""

    result = compose_runtime_contract(global_profile, task_overlay)
    expected_tools = {
        "query_public_duckdb_v3": 10,
        "submit_greeks_submission_v3": 1,
    }
    actual_tools = {
        str(item["name"]): int(item["max_calls"])
        for item in result["trusted_tools"]
    }
    expected_budget = {
        "duckdb_memory_mib": 256,
        "memory_mib": 1024,
        "query_result_bytes": 1_048_576,
        "query_result_rows": 1_000,
        "query_timeout_seconds": 5,
        "submission_bytes": 5_242_880,
        "submission_calls": 1,
        "trusted_query_calls": 10,
        "vcpu": 1,
        "wall_clock_seconds": 600,
    }
    if (
        global_profile.get("profile_id") != "solver-global-v3"
        or task_overlay.get("profile_id") != "bsm-greeks-overlay-v3"
        or actual_tools != expected_tools
        or result["resource_budget"] != expected_budget
    ):
        raise CapabilityViolation("v3 DuckDB-query capability profile changed")
    result.update(
        {
            "runtime_contract_schema_version": (
                "agent-task-runtime-contract-v3.0.0"
            ),
            "host_protocol": "read-only-duckdb-query-schema-submit-v3",
            "profile_id": "bsm-greeks-effective-v3",
        }
    )
    return result


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
            modules = [
                (alias.name, alias.name.split(".", 1)[0])
                for alias in node.names
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise CapabilityViolation("relative imports are not allowed")
            if any(alias.name == "*" for alias in node.names):
                raise CapabilityViolation("wildcard imports are not allowed")
            full_module = node.module or ""
            modules = [(full_module, full_module.split(".", 1)[0])]
        else:
            modules = []
        for full_module, module in modules:
            if module == "__future__":
                continue
            if full_module != module:
                raise CapabilityViolation(
                    f"solver submodule import is not allowed: {full_module}"
                )
            if module.casefold() in denied or module not in allowed:
                raise CapabilityViolation(f"solver import is not allowed: {module}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALL_NAMES:
                raise CapabilityViolation(
                    f"solver call is not allowed: {node.func.id}"
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
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise CapabilityViolation(
                    "solver cannot inspect private or dunder runtime attributes"
                )
            if node.attr in _FORBIDDEN_ATTRIBUTE_NAMES:
                raise CapabilityViolation(
                    f"solver operation is not allowed: {node.attr}"
                )
        if isinstance(node, ast.For) and _fixed_schedule_loop_breaks(node):
            raise CapabilityViolation("solver source cannot early-stop an iteration")
        if isinstance(node, ast.Name) and node.id.casefold() in (
            _FORBIDDEN_METHOD_IDENTIFIERS
        ):
            raise CapabilityViolation(
                f"solver source uses a forbidden tolerance identifier: {node.id}"
            )
    source_casefold = source.casefold().replace(
        "query_public_duckdb_v3", "query_public_database_v3"
    )
    for token in _FORBIDDEN_SOURCE_TOKENS:
        if token in source_casefold:
            raise CapabilityViolation(f"solver source references denied resource: {token}")


@dataclass(frozen=True)
class RuntimeReplayResult:
    """Observable result and audit from one tool-budgeted solver replay."""

    submission: dict[str, Any]
    submission_bytes: bytes
    tool_calls: dict[str, int]
    underlying_market_digest: str
    option_quotes_digest: str
    submission_digest: str


class TrustedGreeksTools:
    """Counted query/submit adapter; the solver never receives a DB handle."""

    def __init__(self, database: str | Path, runtime_contract: Mapping[str, Any]):
        from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
            join_bsm_greeks_query_rows,
            load_bsm_greeks_option_quotes,
            load_bsm_greeks_underlying_market,
        )

        self._underlyings = tuple(load_bsm_greeks_underlying_market(database))
        self._options = tuple(load_bsm_greeks_option_quotes(database))
        self._inputs = join_bsm_greeks_query_rows(
            self._underlyings,
            self._options,
        )
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

    def query_greeks_underlying_market_v2(self) -> list[dict[str, Any]]:
        self._count("query_greeks_underlying_market_v2", query=True)
        return [dict(row) for row in self._underlyings]

    def query_greeks_option_quotes_v2(self) -> list[dict[str, Any]]:
        self._count("query_greeks_option_quotes_v2", query=True)
        return [dict(row) for row in self._options]

    def submit_greeks_submission_v2(self, payload: Mapping[str, Any]) -> None:
        self._count("submit_greeks_submission_v2")
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
            "query_greeks_underlying_market_v2": 1,
            "query_greeks_option_quotes_v2": 1,
            "submit_greeks_submission_v2": 1,
        }
        if self._calls != required:
            raise CapabilityViolation("solver did not use the frozen tool schedule")
        return RuntimeReplayResult(
            submission=self._submission,
            submission_bytes=self._submission_bytes,
            tool_calls=dict(self._calls),
            underlying_market_digest=digest_json(self._underlyings),
            option_quotes_digest=digest_json(self._options),
            submission_digest=sha256(self._submission_bytes).hexdigest(),
        )


_TRUSTED_TOOL_PROXY_METHODS = frozenset(
    {
        "query_greeks_underlying_market_v2",
        "query_greeks_option_quotes_v2",
        "query_public_duckdb_v3",
        "submit_greeks_submission_v2",
        "submit_greeks_submission_v3",
        "result",
    }
)


class _TrustedToolsProxy:
    """Expose only trusted calls while the host retains private task paths."""

    __slots__ = ("_connection",)

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def _call(self, method: str, *arguments: Any) -> Any:
        try:
            self._connection.send((method, arguments))
            status, payload = self._connection.recv()
        except (EOFError, OSError) as error:
            raise CapabilityViolation("trusted tool host became unavailable") from error
        if status == "ok":
            return payload
        error_type, message = payload
        raise CapabilityViolation(f"trusted tool failed with {error_type}: {message}")

    def query_greeks_underlying_market_v2(self) -> list[dict[str, Any]]:
        return self._call("query_greeks_underlying_market_v2")

    def query_greeks_option_quotes_v2(self) -> list[dict[str, Any]]:
        return self._call("query_greeks_option_quotes_v2")

    def query_public_duckdb_v3(self, sql: str) -> dict[str, Any]:
        return self._call("query_public_duckdb_v3", sql)

    def submit_greeks_submission_v2(self, payload: Mapping[str, Any]) -> None:
        self._call("submit_greeks_submission_v2", payload)

    def submit_greeks_submission_v3(self, payload: Mapping[str, Any]) -> None:
        self._call("submit_greeks_submission_v3", payload)

    def result(self) -> RuntimeReplayResult:
        result = self._call("result")
        if not isinstance(result, RuntimeReplayResult):
            raise CapabilityViolation("trusted tool host returned an invalid result")
        return result


def _serve_trusted_tools(
    connection: Any,
    tools: Any,
    stop: Event,
) -> None:
    """Serve a narrow RPC surface without giving solver code the host object."""

    try:
        while not stop.is_set():
            if not connection.poll(0.05):
                continue
            try:
                request = connection.recv()
            except EOFError:
                return
            if (
                not isinstance(request, tuple)
                or len(request) != 2
                or not isinstance(request[0], str)
                or not isinstance(request[1], tuple)
                or request[0] not in _TRUSTED_TOOL_PROXY_METHODS
            ):
                connection.send(
                    (
                        "error",
                        ("CapabilityViolation", "trusted tool request is invalid"),
                    )
                )
                continue
            method_name, arguments = request
            try:
                method = getattr(tools, method_name)
                result = method(*arguments)
                connection.send(("ok", result))
            except BaseException as error:
                connection.send(("error", (type(error).__name__, str(error))))
    except (EOFError, OSError):
        return
    finally:
        connection.close()


def _restricted_builtins(runtime_contract: Mapping[str, Any]) -> dict[str, Any]:
    allowed = set(runtime_contract["allowed_direct_imports"])
    denied = {item.casefold() for item in runtime_contract["denied_imports"]}
    module_facades: dict[str, types.ModuleType] = {}

    def safe_module(name: str) -> types.ModuleType:
        facade = module_facades.get(name)
        if facade is not None:
            return facade
        imported = builtins.__import__(name)
        facade = types.ModuleType(name)
        for attribute in dir(imported):
            if attribute.startswith("_"):
                continue
            value = getattr(imported, attribute)
            if not isinstance(value, types.ModuleType):
                setattr(facade, attribute, value)
        module_facades[name] = facade
        return facade

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
        if (
            level
            or name != top_level
            or top_level not in allowed
            or top_level.casefold() in denied
        ):
            raise CapabilityViolation(f"runtime import is not allowed: {name}")
        return safe_module(name)

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
    tools: Any,
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


def _replay_audited_solver_source(
    *,
    source: str,
    source_path: Path,
    tools: Any,
    submission_directory: str | Path,
    runtime_contract: Mapping[str, Any],
) -> RuntimeReplayResult:
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    host_connection, solver_tool_connection = context.Pipe(duplex=True)
    tool_proxy = _TrustedToolsProxy(solver_tool_connection)
    host_stop = Event()
    host_thread = Thread(
        target=_serve_trusted_tools,
        args=(host_connection, tools, host_stop),
        name="trusted-tool-host",
        daemon=True,
    )
    process = context.Process(
        target=_solver_process,
        kwargs={
            "connection": child_connection,
            "source": source,
            "source_name": str(source_path),
            "tools": tool_proxy,
            "runtime_contract": dict(runtime_contract),
        },
    )
    host_thread.start()
    try:
        process.start()
        child_connection.close()
        solver_tool_connection.close()
        process.join(runtime_contract["resource_budget"]["wall_clock_seconds"])
        if process.is_alive():
            process.terminate()
            process.join()
            raise CapabilityViolation("solver exceeded the wall-clock budget")
        if not parent_connection.poll():
            raise CapabilityViolation(
                "solver process exited without a result "
                f"(exit code {process.exitcode})"
            )
        status, payload = parent_connection.recv()
    finally:
        parent_connection.close()
        host_stop.set()
        host_thread.join(timeout=10)
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


def replay_solver_source_with_tools(
    *,
    source_path: str | Path,
    tools: Any,
    submission_directory: str | Path,
    runtime_contract: Mapping[str, Any],
) -> RuntimeReplayResult:
    """Audit and execute a solver with a caller-supplied trusted tool adapter."""

    resolved_source_path = Path(source_path)
    source = resolved_source_path.read_text(encoding="utf-8")
    audit_solver_source(source, runtime_contract)
    return _replay_audited_solver_source(
        source=source,
        source_path=resolved_source_path,
        tools=tools,
        submission_directory=submission_directory,
        runtime_contract=runtime_contract,
    )


def replay_solver_source(
    *,
    source_path: str | Path,
    database: str | Path,
    submission_directory: str | Path,
    runtime_contract: Mapping[str, Any],
) -> RuntimeReplayResult:
    """Audit and execute the solver with the source-package database adapter."""

    resolved_source_path = Path(source_path)
    source = resolved_source_path.read_text(encoding="utf-8")
    audit_solver_source(source, runtime_contract)
    tools = TrustedGreeksTools(database, runtime_contract)
    return _replay_audited_solver_source(
        source=source,
        source_path=resolved_source_path,
        tools=tools,
        submission_directory=submission_directory,
        runtime_contract=runtime_contract,
    )


__all__ = [
    "CapabilityViolation",
    "RuntimeReplayResult",
    "TrustedGreeksTools",
    "audit_solver_source",
    "compose_runtime_contract",
    "compose_runtime_contract_v3",
    "replay_solver_source",
    "replay_solver_source_with_tools",
]
