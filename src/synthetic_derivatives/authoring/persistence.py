"""Deterministic, insert-only DuckDB staging and MERGE primitive."""

from __future__ import annotations

from typing import Any, Sequence

import duckdb

from synthetic_derivatives.authoring.table_specs import TableSpec

def merge_rows(
    connection: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    rows: Sequence[Sequence[Any]],
) -> dict[str, int]:
    """Insert rows whose business keys do not already exist.

    A temporary table inherits the destination column types, then DuckDB MERGE
    inserts only unmatched keys.  Existing rows are deliberately never updated:
    economic compatibility is checked by the pipeline before this primitive is
    called.  The returned counts drive NOOP detection and revision creation.
    """

    if not rows:
        return {"requested": 0, "inserted": 0, "unchanged": 0}
    stage_name = "incremental_rows"
    column_csv = ", ".join(spec.columns)
    placeholders = ", ".join("?" for _ in spec.columns)
    connection.execute(
        f"CREATE TEMP TABLE {stage_name} AS SELECT {column_csv} FROM {spec.name} WHERE false"
    )
    try:
        connection.executemany(
            f"INSERT INTO {stage_name} ({column_csv}) VALUES ({placeholders})", rows
        )
        key_join = " AND ".join(f"target.{key} = source.{key}" for key in spec.keys)
        insert_values = ", ".join(f"source.{column}" for column in spec.columns)
        actions = connection.execute(
            f"""
            MERGE INTO {spec.name} AS target
            USING {stage_name} AS source
            ON {key_join}
            WHEN NOT MATCHED THEN
                INSERT ({column_csv}) VALUES ({insert_values})
            RETURNING merge_action
            """
        ).fetchall()
    except Exception:
        # A failed DuckDB statement aborts the surrounding transaction. The
        # caller rolls it back, which also removes the temporary stage table.
        raise
    else:
        connection.execute(f"DROP TABLE {stage_name}")

    inserted = sum(action[0] == "INSERT" for action in actions)
    return {
        "requested": len(rows),
        "inserted": inserted,
        "unchanged": len(rows) - inserted,
    }


def table_columns(connection: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """Return DuckDB column names in physical table order for schema tests."""

    return [row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()]
