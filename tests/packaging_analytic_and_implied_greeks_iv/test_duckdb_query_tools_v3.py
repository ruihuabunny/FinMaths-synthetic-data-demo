from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.duckdb_query_tools import (
    DuckDBQueryError,
    DuckDBQueryRejected,
    DuckDBQueryTimeout,
    DuckDBQueryToolsV3,
    MAX_RESULT_BYTES,
    query_response_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    _TrustedToolsProxy,
)


@pytest.fixture
def public_database(tmp_path: Path) -> Path:
    path = tmp_path / "task.duckdb"
    connection = duckdb.connect(str(path))
    try:
        connection.execute("CREATE SCHEMA metadata")
        connection.execute("CREATE SCHEMA solver_visible")
        connection.execute(
            """
            CREATE TABLE metadata.public_task (
                task_id VARCHAR,
                valuation_date DATE,
                exact_value DECIMAL(24,8),
                double_value DOUBLE,
                nullable_value VARCHAR
            )
            """
        )
        connection.execute(
            """
            INSERT INTO metadata.public_task
            VALUES ('task-v3', DATE '2026-08-14', 12.34000000, 0.125, NULL)
            """
        )
        connection.execute(
            """
            CREATE TABLE solver_visible.underlying_market_inputs (
                task_id VARCHAR,
                underlying_id VARCHAR,
                spot DECIMAL(24,8),
                risk_free_rate DOUBLE
            )
            """
        )
        connection.executemany(
            "INSERT INTO solver_visible.underlying_market_inputs VALUES (?, ?, ?, ?)",
            [
                ("task-v3", f"asset-{index:02d}", "100.00000000", 0.03)
                for index in range(8)
            ],
        )
        connection.execute(
            """
            CREATE TABLE solver_visible.option_quote_inputs (
                row_id VARCHAR,
                task_id VARCHAR,
                underlying_id VARCHAR,
                strike DECIMAL(24,8),
                bid DECIMAL(24,8),
                time_to_expiry_actual365 DOUBLE
            )
            """
        )
        connection.executemany(
            "INSERT INTO solver_visible.option_quote_inputs VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    f"row_{index + 1:06d}",
                    "task-v3",
                    f"asset-{index % 8:02d}",
                    "100.00000000",
                    f"{1 + index / 100:.8f}",
                    30.0 / 365.0,
                )
                for index in range(160)
            ],
        )
    finally:
        connection.close()
    return path


def test_public_queries_cover_discovery_join_and_aggregation(
    public_database: Path,
) -> None:
    tools = DuckDBQueryToolsV3(public_database)

    # DuckDB SHOW TABLES is scoped to the empty default ``main`` schema for this
    # layout.  Cross-schema discovery intentionally uses information_schema below.
    shown = tools.query_public_duckdb_v3("SHOW TABLES")
    assert shown == {
        "columns": ["name"],
        "rows": [],
        "row_count": 0,
        "truncated": False,
    }

    described = tools.query_public_duckdb_v3(
        "DESCRIBE solver_visible.option_quote_inputs"
    )
    assert described["columns"] == [
        "column_name",
        "column_type",
        "null",
        "key",
        "default",
        "extra",
    ]
    assert [row[0] for row in described["rows"]] == [
        "row_id",
        "task_id",
        "underlying_id",
        "strike",
        "bid",
        "time_to_expiry_actual365",
    ]

    discovered = tools.query_public_duckdb_v3(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('metadata', 'solver_visible')
        ORDER BY table_schema, table_name
        """
    )
    assert discovered["rows"] == [
        ["metadata", "public_task"],
        ["solver_visible", "option_quote_inputs"],
        ["solver_visible", "underlying_market_inputs"],
    ]

    joined = tools.query_public_duckdb_v3(
        """
        WITH underlyings AS (
            SELECT task_id, underlying_id, spot
            FROM solver_visible.underlying_market_inputs
        )
        SELECT o.row_id, u.spot, o.bid
        FROM solver_visible.option_quote_inputs AS o
        JOIN underlyings AS u USING (task_id, underlying_id)
        WHERE o.underlying_id = 'asset-00'
        ORDER BY o.row_id
        LIMIT 2
        """
    )
    assert joined == {
        "columns": ["row_id", "spot", "bid"],
        "rows": [
            ["row_000001", "100.00000000", "1.00000000"],
            ["row_000009", "100.00000000", "1.08000000"],
        ],
        "row_count": 2,
        "truncated": False,
    }

    counts = tools.query_public_duckdb_v3(
        """
        SELECT
            (SELECT count(*) FROM solver_visible.underlying_market_inputs) AS underlyings,
            (SELECT count(*) FROM solver_visible.option_quote_inputs) AS options
        """
    )
    assert counts["rows"] == [[8, 160]]
    assert tools.query_call_count == 5


def test_date_decimal_double_varchar_and_null_serialization(
    public_database: Path,
) -> None:
    result = DuckDBQueryToolsV3(public_database).query_public_duckdb_v3(
        """
        SELECT valuation_date, exact_value, double_value, task_id, nullable_value,
               exact_value IS NOT NULL AS boolean_value
        FROM metadata.public_task
        """
    )

    assert result == {
        "columns": [
            "valuation_date",
            "exact_value",
            "double_value",
            "task_id",
            "nullable_value",
            "boolean_value",
        ],
        "rows": [["2026-08-14", "12.34000000", 0.125, "task-v3", None, True]],
        "row_count": 1,
        "truncated": False,
    }


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "SELECT 1; SELECT 2",
        "INSERT INTO metadata.public_task VALUES ('x', DATE '2026-01-01', 1, 1, NULL)",
        "UPDATE metadata.public_task SET task_id = 'x'",
        "DELETE FROM metadata.public_task",
        "CREATE TABLE metadata.extra (value INTEGER)",
        "DROP TABLE metadata.public_task",
        "ALTER TABLE metadata.public_task ADD COLUMN hidden INTEGER",
        "ATTACH ':memory:' AS hidden",
        "DETACH task",
        "COPY metadata.public_task TO '/tmp/leak.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET memory_limit = '1GB'",
        "RESET memory_limit",
        "PRAGMA database_list",
        "SHOW ALL TABLES",
        "SHOW DATABASES",
        "SUMMARIZE metadata.public_task",
        "DESCRIBE SELECT * FROM metadata.public_task",
    ],
)
def test_non_select_and_non_contract_statements_are_rejected(
    public_database: Path, sql: str
) -> None:
    with pytest.raises(DuckDBQueryRejected):
        DuckDBQueryToolsV3(public_database).query_public_duckdb_v3(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_json('https://example.invalid/private.json')",
        "SELECT * FROM read_parquet('/tmp/private.parquet')",
        "SELECT * FROM glob('/tmp/*')",
        "SELECT * FROM query_table('metadata.public_task')",
        "SELECT * FROM duckdb_settings()",
        "SELECT current_setting('secret_directory')",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM main.private_table",
        "SELECT * FROM task.metadata.public_task",
        "SELECT * FROM information_schema.character_sets",
        """
        SELECT * FROM sqlite_master
        WHERE EXISTS (
            WITH sqlite_master AS (SELECT 1)
            SELECT * FROM sqlite_master
        )
        """,
    ],
)
def test_external_dynamic_and_non_allowlist_access_is_rejected(
    public_database: Path, sql: str
) -> None:
    with pytest.raises(DuckDBQueryRejected):
        DuckDBQueryToolsV3(public_database).query_public_duckdb_v3(sql)


def test_duckdb_binder_rejects_unknown_columns(public_database: Path) -> None:
    with pytest.raises(DuckDBQueryError, match="DuckDB rejected the query"):
        DuckDBQueryToolsV3(public_database).query_public_duckdb_v3(
            "SELECT hidden_answer FROM metadata.public_task"
        )


def test_attempted_calls_are_counted_and_budget_is_enforced(
    public_database: Path,
) -> None:
    assert issubclass(DuckDBQueryError, ValueError)
    assert issubclass(DuckDBQueryRejected, DuckDBQueryError)
    assert issubclass(DuckDBQueryTimeout, DuckDBQueryError)
    tools = DuckDBQueryToolsV3(public_database, max_query_calls=2)

    assert tools.query_public_duckdb_v3("SELECT 1")["rows"] == [[1]]
    with pytest.raises(DuckDBQueryRejected, match="exactly one SQL statement"):
        tools.query_public_duckdb_v3("SELECT 1; SELECT 2")
    assert tools.query_call_count == 2
    with pytest.raises(DuckDBQueryRejected, match="call budget exceeded"):
        tools.query_public_duckdb_v3("SELECT 1")
    assert tools.query_call_count == 2


def test_minimum_query_call_is_required(public_database: Path) -> None:
    tools = DuckDBQueryToolsV3(public_database)
    with pytest.raises(DuckDBQueryRejected, match="at least one"):
        tools.require_minimum_query_calls()


def test_row_limit_returns_explicit_truncation(public_database: Path) -> None:
    tools = DuckDBQueryToolsV3(public_database, max_result_rows=2)
    result = tools.query_public_duckdb_v3(
        "SELECT row_id FROM solver_visible.option_quote_inputs ORDER BY row_id"
    )

    assert result == {
        "columns": ["row_id"],
        "rows": [["row_000001"], ["row_000002"]],
        "row_count": 2,
        "truncated": True,
    }
    assert result["row_count"] == len(result["rows"])
    assert tools.query_call_count == 1


def test_byte_limit_returns_explicit_truncation(public_database: Path) -> None:
    tools = DuckDBQueryToolsV3(public_database, max_result_bytes=100)
    result = tools.query_public_duckdb_v3("SELECT repeat('x', 1000) AS value")

    assert result == {
        "columns": ["value"],
        "rows": [],
        "row_count": 0,
        "truncated": True,
    }
    assert len(query_response_json_bytes(result)) <= 100
    assert result["row_count"] == len(result["rows"])
    assert tools.query_call_count == 1


def test_default_result_byte_limit_is_enforced(public_database: Path) -> None:
    result = DuckDBQueryToolsV3(public_database).query_public_duckdb_v3(
        "SELECT repeat('x', 1100000) AS value"
    )
    assert result["truncated"] is True
    assert result["rows"] == []
    assert len(query_response_json_bytes(result)) <= MAX_RESULT_BYTES


def test_query_timeout_interrupts_duckdb(public_database: Path) -> None:
    tools = DuckDBQueryToolsV3(public_database, timeout_seconds=0.01)
    with pytest.raises(DuckDBQueryTimeout, match="time limit"):
        tools.query_public_duckdb_v3(
            """
            WITH RECURSIVE counter(value) AS (
                SELECT 1
                UNION ALL
                SELECT value + 1 FROM counter WHERE value < 100000000
            )
            SELECT max(value) FROM counter
            """
        )
    assert tools.query_call_count == 1


def test_database_digest_binding(public_database: Path) -> None:
    digest = sha256(public_database.read_bytes()).hexdigest()
    DuckDBQueryToolsV3(public_database, expected_database_sha256=digest)
    with pytest.raises(ValueError, match="digest differs"):
        DuckDBQueryToolsV3(public_database, expected_database_sha256="0" * 64)


def test_solver_tool_proxy_does_not_expose_host_paths() -> None:
    proxy = _TrustedToolsProxy(object())

    assert not hasattr(proxy, "_database")
    assert not hasattr(proxy, "_root")
    with pytest.raises(AttributeError):
        proxy._database
    with pytest.raises(AttributeError):
        proxy._root


def test_database_relation_inventory_must_be_exact(public_database: Path) -> None:
    connection = duckdb.connect(str(public_database))
    try:
        connection.execute("CREATE TABLE metadata.private_answer(answer DOUBLE)")
    finally:
        connection.close()

    with pytest.raises(ValueError, match="relation inventory"):
        DuckDBQueryToolsV3(public_database)


def test_database_must_not_define_functions_or_macros(public_database: Path) -> None:
    connection = duckdb.connect(str(public_database))
    try:
        connection.execute("CREATE MACRO hidden_setting() AS current_setting('home_directory')")
    finally:
        connection.close()

    with pytest.raises(ValueError, match="functions or macros"):
        DuckDBQueryToolsV3(public_database)
