"""Restricted read-only DuckDB query host for the v3 portable task protocol.

The solver receives this adapter, never a DuckDB connection or database path.  SQL
is parsed by DuckDB, checked against the public relation boundary, bound by DuckDB,
and only then executed on a fresh read-only connection with external access disabled.
``SHOW TABLES`` keeps DuckDB's current-schema semantics; cross-schema discovery uses
the allowlisted ``information_schema`` relations.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from threading import Event, Lock, Timer
from time import monotonic
from typing import Any

import duckdb


QUERY_TOOL_NAME = "query_public_duckdb_v3"
MAX_QUERY_CALLS = 10
MAX_SQL_CHARACTERS = 20_000
MAX_RESULT_ROWS = 1_000
MAX_RESULT_BYTES = 1_048_576
QUERY_TIMEOUT_SECONDS = 5.0
DUCKDB_MEMORY_LIMIT_MIB = 256

ALLOWED_SCHEMAS = frozenset({"metadata", "solver_visible", "information_schema"})
ALLOWED_PUBLIC_RELATIONS = frozenset(
    {
        "metadata.public_task",
        "solver_visible.underlying_market_inputs",
        "solver_visible.option_quote_inputs",
    }
)
SAFE_INFORMATION_SCHEMA_RELATIONS = frozenset(
    {"columns", "schemata", "tables", "views"}
)

_FORBIDDEN_SCALAR_FUNCTIONS = frozenset(
    {
        "current_connection_id",
        "current_query",
        "current_query_id",
        "current_setting",
        "current_transaction_id",
        "currval",
        "error",
        "gen_random_uuid",
        "getvariable",
        "nextval",
        "random",
        "setseed",
        "sleep_ms",
        "uuid",
        "uuidv4",
        "uuidv7",
        "write_log",
    }
)
_INVALID_QUERY_LOCATION = (1 << 64) - 1


class DuckDBQueryError(ValueError):
    """Base error exposed by the restricted query host."""


class DuckDBQueryRejected(DuckDBQueryError):
    """The requested SQL is outside the frozen read-only capability."""


class DuckDBQueryTimeout(DuckDBQueryError):
    """DuckDB did not finish within the per-query time budget."""


class DuckDBQuerySerializationError(DuckDBQueryError):
    """A result cannot be represented by the frozen JSON value contract."""


def _require_bounded_int(name: str, value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 through {maximum}")


def _database_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _connection_config(memory_limit_mib: int) -> dict[str, str]:
    return {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "enable_external_access": "false",
        "lock_configuration": "true",
        "max_temp_directory_size": "0B",
        "memory_limit": f"{memory_limit_mib}MiB",
    }


def _walk_json(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _parse_select_ast(
    connection: duckdb.DuckDBPyConnection, sql: str
) -> Mapping[str, Any]:
    try:
        statements = connection.extract_statements(sql)
    except duckdb.Error as error:
        raise DuckDBQueryRejected("SQL does not parse as one DuckDB statement") from error
    if len(statements) != 1:
        raise DuckDBQueryRejected("exactly one SQL statement is required")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise DuckDBQueryRejected(
            "only SELECT, WITH SELECT, SHOW TABLES, or DESCRIBE is allowed"
        )

    try:
        serialized = connection.execute(
            "SELECT json_serialize_sql(?)", [sql]
        ).fetchone()[0]
        parsed = json.loads(serialized)
    except (duckdb.Error, json.JSONDecodeError, TypeError) as error:
        raise DuckDBQueryRejected("SQL cannot be validated as a read-only SELECT") from error
    if parsed.get("error") is not False:
        raise DuckDBQueryRejected("SQL is not an allowed SELECT form")
    serialized_statements = parsed.get("statements")
    if not isinstance(serialized_statements, list) or len(serialized_statements) != 1:
        raise DuckDBQueryRejected("exactly one SQL statement is required")
    statement = serialized_statements[0]
    if not isinstance(statement, Mapping):
        raise DuckDBQueryRejected("DuckDB returned an invalid parsed statement")
    return statement


def _validate_show_form(statement: Mapping[str, Any]) -> None:
    node = statement.get("node")
    if not isinstance(node, Mapping):
        raise DuckDBQueryRejected("DuckDB returned an invalid SELECT syntax tree")
    show_refs = [item for item in _walk_json(node) if item.get("type") == "SHOW_REF"]
    if not show_refs:
        return
    if len(show_refs) != 1 or node.get("type") != "SELECT_NODE":
        raise DuckDBQueryRejected("SHOW may only be used as SHOW TABLES or DESCRIBE")
    show = show_refs[0]
    if node.get("from_table") is not show:
        raise DuckDBQueryRejected("nested SHOW operations are not allowed")
    if (
        show.get("show_type") == "SHOW_UNQUALIFIED"
        and show.get("table_name") == '"tables"'
        and show.get("query") is None
    ):
        return
    query = show.get("query")
    if show.get("show_type") == "DESCRIBE" and isinstance(query, Mapping):
        select_list = query.get("select_list")
        cte_map = query.get("cte_map")
        from_table = query.get("from_table")
        if (
            query.get("type") == "SELECT_NODE"
            and isinstance(cte_map, Mapping)
            and cte_map.get("map") == []
            and isinstance(select_list, list)
            and len(select_list) == 1
            and isinstance(select_list[0], Mapping)
            and select_list[0].get("type") == "STAR"
            and select_list[0].get("query_location") == _INVALID_QUERY_LOCATION
            and isinstance(from_table, Mapping)
            and from_table.get("type") == "BASE_TABLE"
        ):
            return
    raise DuckDBQueryRejected("only SHOW TABLES and DESCRIBE are allowed")


def _validate_ast_node(
    node: Mapping[str, Any], cte_scope: frozenset[str]
) -> int:
    node_type = node.get("type")
    if node_type == "TABLE_FUNCTION":
        raise DuckDBQueryRejected("table functions are not allowed")
    if node_type == "BASE_TABLE":
        table = node.get("table_name")
        schema = node.get("schema_name")
        catalog = node.get("catalog_name")
        if not all(isinstance(item, str) for item in (table, schema, catalog)):
            raise DuckDBQueryRejected("DuckDB returned an invalid relation reference")
        if catalog or node.get("at_clause") is not None:
            raise DuckDBQueryRejected(
                "catalog-qualified or versioned relations are not allowed"
            )
        normalized_table = table.casefold()
        normalized_schema = schema.casefold()
        if not normalized_schema and normalized_table in cte_scope:
            return 0
        relation = f"{normalized_schema}.{normalized_table}"
        if normalized_schema == "information_schema":
            if normalized_table not in SAFE_INFORMATION_SCHEMA_RELATIONS:
                raise DuckDBQueryRejected(
                    "that information_schema relation is not allowed"
                )
        elif relation not in ALLOWED_PUBLIC_RELATIONS:
            raise DuckDBQueryRejected("query references a non-public relation")
        return 1
    if node.get("class") == "FUNCTION":
        name = node.get("function_name")
        schema = node.get("schema")
        catalog = node.get("catalog")
        if not all(isinstance(item, str) for item in (name, schema, catalog)):
            raise DuckDBQueryRejected("DuckDB returned an invalid function reference")
        if schema or catalog:
            raise DuckDBQueryRejected("qualified function calls are not allowed")
        if name.casefold() in _FORBIDDEN_SCALAR_FUNCTIONS:
            raise DuckDBQueryRejected("that scalar function is not allowed")
    return 0


def _validate_scoped_ast(value: Any, cte_scope: frozenset[str]) -> int:
    if isinstance(value, list):
        return sum(_validate_scoped_ast(child, cte_scope) for child in value)
    if not isinstance(value, Mapping):
        return 0

    base_relation_count = _validate_ast_node(value, cte_scope)
    cte_map = value.get("cte_map")
    entries = cte_map.get("map") if isinstance(cte_map, Mapping) else None
    if not isinstance(entries, list) or not entries:
        return base_relation_count + sum(
            _validate_scoped_ast(child, cte_scope) for child in value.values()
        )

    visible = set(cte_scope)
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("key"), str):
            raise DuckDBQueryRejected("DuckDB returned an invalid CTE definition")
        cte_name = entry["key"].casefold()
        cte_value = entry.get("value")
        if not isinstance(cte_value, Mapping):
            raise DuckDBQueryRejected("DuckDB returned an invalid CTE definition")
        cte_query = cte_value.get("query")
        if not isinstance(cte_query, Mapping):
            raise DuckDBQueryRejected("DuckDB returned an invalid CTE query")
        cte_node = cte_query.get("node")
        recursive = isinstance(cte_node, Mapping) and (
            cte_node.get("type") == "RECURSIVE_CTE_NODE"
        )
        definition_scope = frozenset(visible | ({cte_name} if recursive else set()))
        base_relation_count += _validate_scoped_ast(cte_query, definition_scope)
        visible.add(cte_name)

    for key, child in value.items():
        if key != "cte_map":
            base_relation_count += _validate_scoped_ast(child, frozenset(visible))
    return base_relation_count


def _validate_ast_capabilities(statement: Mapping[str, Any]) -> None:
    _validate_show_form(statement)
    described = any(
        node.get("type") == "SHOW_REF" and node.get("show_type") == "DESCRIBE"
        for node in _walk_json(statement)
    )
    base_relation_count = _validate_scoped_ast(statement, frozenset())
    if described and base_relation_count != 1:
        raise DuckDBQueryRejected("DESCRIBE requires one allowed relation")


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        raise DuckDBQuerySerializationError(
            "TIMESTAMP results are outside the v3 serialization contract"
        )
    if isinstance(value, date):
        return value.isoformat()
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DuckDBQuerySerializationError(
                "non-finite DOUBLE results are not valid JSON numbers"
            )
        return value
    raise DuckDBQuerySerializationError(
        f"result type {type(value).__name__} is outside the v3 serialization contract"
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def query_response_json_bytes(response: Mapping[str, Any]) -> bytes:
    """Serialize a response exactly as used for the result-byte budget."""

    return _json_bytes(response)


def _response_size(
    columns_json: bytes,
    row_json_lengths: list[int],
    *,
    row_count: int,
    truncated: bool,
) -> int:
    rows_size = sum(row_json_lengths) + max(0, len(row_json_lengths) - 1)
    return (
        len(b'{"columns":')
        + len(columns_json)
        + len(b',"rows":[')
        + rows_size
        + len(b'],"row_count":')
        + len(str(row_count).encode("ascii"))
        + len(b',"truncated":')
        + len(b"true" if truncated else b"false")
        + len(b"}")
    )


def _fetch_response(
    cursor: duckdb.DuckDBPyConnection,
    *,
    max_result_rows: int,
    max_result_bytes: int,
) -> dict[str, Any]:
    description = cursor.description
    if description is None:
        raise DuckDBQueryRejected("query did not produce a result set")
    columns = [str(item[0]) for item in description]
    columns_json = _json_bytes(columns)
    if _response_size(columns_json, [], row_count=0, truncated=True) > max_result_bytes:
        raise DuckDBQuerySerializationError("query response metadata exceeds the byte limit")

    rows: list[list[Any]] = []
    row_json_lengths: list[int] = []
    truncated = False
    while True:
        raw = cursor.fetchone()
        if raw is None:
            break
        if len(rows) == max_result_rows:
            truncated = True
            break
        row = [_serialize_value(value) for value in raw]
        encoded = _json_bytes(row)
        candidate_lengths = [*row_json_lengths, len(encoded)]
        if (
            _response_size(
                columns_json,
                candidate_lengths,
                row_count=len(rows) + 1,
                truncated=False,
            )
            > max_result_bytes
        ):
            truncated = True
            break
        rows.append(row)
        row_json_lengths.append(len(encoded))

    response = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
    if len(query_response_json_bytes(response)) > max_result_bytes:
        raise DuckDBQuerySerializationError("query response exceeds the byte limit")
    return response


class DuckDBQueryToolsV3:
    """Counted host for ``query_public_duckdb_v3``."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_query_calls: int = MAX_QUERY_CALLS,
        max_sql_characters: int = MAX_SQL_CHARACTERS,
        max_result_rows: int = MAX_RESULT_ROWS,
        max_result_bytes: int = MAX_RESULT_BYTES,
        timeout_seconds: float = QUERY_TIMEOUT_SECONDS,
        memory_limit_mib: int = DUCKDB_MEMORY_LIMIT_MIB,
        expected_database_sha256: str | None = None,
    ) -> None:
        _require_bounded_int("max_query_calls", max_query_calls, MAX_QUERY_CALLS)
        _require_bounded_int(
            "max_sql_characters", max_sql_characters, MAX_SQL_CHARACTERS
        )
        _require_bounded_int("max_result_rows", max_result_rows, MAX_RESULT_ROWS)
        _require_bounded_int("max_result_bytes", max_result_bytes, MAX_RESULT_BYTES)
        _require_bounded_int(
            "memory_limit_mib", memory_limit_mib, DUCKDB_MEMORY_LIMIT_MIB
        )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= QUERY_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout_seconds must be positive and at most {QUERY_TIMEOUT_SECONDS}"
            )

        self._database = Path(database)
        if not self._database.is_file():
            raise ValueError("public DuckDB file does not exist")
        if expected_database_sha256 is not None:
            if (
                not isinstance(expected_database_sha256, str)
                or len(expected_database_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in expected_database_sha256
                )
            ):
                raise ValueError("expected database SHA-256 is invalid")
            if _database_sha256(self._database) != expected_database_sha256:
                raise ValueError("public DuckDB digest differs from its binding")

        self._max_query_calls = max_query_calls
        self._max_sql_characters = max_sql_characters
        self._max_result_rows = max_result_rows
        self._max_result_bytes = max_result_bytes
        self._timeout_seconds = float(timeout_seconds)
        self._connection_options = _connection_config(memory_limit_mib)
        self._query_call_count = 0
        self._count_lock = Lock()
        self._validate_database_relations()

    @property
    def query_call_count(self) -> int:
        """Return the number of attempted trusted query calls."""

        with self._count_lock:
            return self._query_call_count

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(
            str(self._database),
            read_only=True,
            config=self._connection_options,
        )

    def _validate_database_relations(self) -> None:
        connection = self._connect()
        try:
            relations = {
                f"{str(schema).casefold()}.{str(table).casefold()}"
                for schema, table in connection.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                    """
                ).fetchall()
            }
            user_defined_functions = connection.execute(
                """
                SELECT schema_name, function_name, function_type
                FROM duckdb_functions()
                WHERE NOT internal
                """
            ).fetchall()
        finally:
            connection.close()
        if relations != ALLOWED_PUBLIC_RELATIONS:
            raise ValueError("DuckDB relation inventory differs from the public allowlist")
        if user_defined_functions:
            raise ValueError("DuckDB contains non-public functions or macros")

    def _count_query_call(self) -> None:
        with self._count_lock:
            if self._query_call_count >= self._max_query_calls:
                raise DuckDBQueryRejected("trusted query call budget exceeded")
            self._query_call_count += 1

    def require_minimum_query_calls(self) -> None:
        """Reject completion when the solver never queried the public database."""

        if self.query_call_count < 1:
            raise DuckDBQueryRejected("at least one trusted query call is required")

    def query_public_duckdb_v3(self, sql: str) -> dict[str, Any]:
        """Validate, execute, limit, and JSON-normalize one public SQL query."""

        self._count_query_call()
        if not isinstance(sql, str):
            raise DuckDBQueryRejected("sql must be a string")
        if not sql.strip():
            raise DuckDBQueryRejected("sql must not be empty")
        if len(sql) > self._max_sql_characters:
            raise DuckDBQueryRejected("sql exceeds the character limit")
        if "\x00" in sql:
            raise DuckDBQueryRejected("sql contains a NUL character")

        try:
            connection = self._connect()
        except duckdb.Error as error:
            raise DuckDBQueryError("DuckDB could not open the public database") from error
        timed_out = Event()
        started_at = monotonic()

        def interrupt() -> None:
            timed_out.set()
            connection.interrupt()

        timer = Timer(self._timeout_seconds, interrupt)
        timer.daemon = True
        try:
            timer.start()
        except RuntimeError as error:
            connection.close()
            raise DuckDBQueryError("query timeout watchdog could not start") from error
        try:
            statement = _parse_select_ast(connection, sql)
            _validate_ast_capabilities(statement)
            # EXPLAIN invokes DuckDB's binder and metadata resolver without running
            # the query.  The subsequent execution uses the same locked connection.
            connection.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchone()
            cursor = connection.execute(sql)
            response = _fetch_response(
                cursor,
                max_result_rows=self._max_result_rows,
                max_result_bytes=self._max_result_bytes,
            )
            timer.cancel()
            timer.join()
            if timed_out.is_set() or monotonic() - started_at > self._timeout_seconds:
                raise DuckDBQueryTimeout("query exceeded the time limit")
            return response
        except duckdb.InterruptException as error:
            if timed_out.is_set():
                raise DuckDBQueryTimeout("query exceeded the time limit") from error
            raise DuckDBQueryError("DuckDB interrupted the query") from error
        except DuckDBQueryError:
            raise
        except duckdb.Error as error:
            if timed_out.is_set():
                raise DuckDBQueryTimeout("query exceeded the time limit") from error
            raise DuckDBQueryError(f"DuckDB rejected the query: {error}") from error
        finally:
            timer.cancel()
            timer.join()
            connection.close()


def execute_public_duckdb_query_v3(
    database: str | Path, sql: str, **limits: Any
) -> dict[str, Any]:
    """Execute one query through a fresh v3 host (convenient for integration tests)."""

    host = DuckDBQueryToolsV3(database, max_query_calls=1, **limits)
    return host.query_public_duckdb_v3(sql)
