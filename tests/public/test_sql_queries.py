from __future__ import annotations

from pathlib import Path

import duckdb


EXPECTED_ROW_COUNTS = {
    "generation_audit.sql": 2,
    "option_chain.sql": 5,
    "option_pricing_context.sql": 5,
    "option_spot_moneyness.sql": 5,
    "snapshot_summary.sql": 1,
    "underlying_time_series.sql": 5,
}


def test_public_sql_queries_are_read_only_and_runnable(repository_root: Path) -> None:
    query_directory = repository_root / "snapshots/public/sql_query"
    query_paths = sorted(query_directory.glob("*.sql"))

    assert {path.name for path in query_paths} == set(EXPECTED_ROW_COUNTS)
    connection = duckdb.connect(
        str(repository_root / "snapshots/public/quantlib_bsm_smoke_v1.duckdb"),
        read_only=True,
    )
    try:
        for path in query_paths:
            query = path.read_text(encoding="utf-8")
            normalized = query.upper()
            for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "CREATE ", "DROP "):
                assert forbidden not in normalized
            rows = connection.execute(query).fetchall()
            assert len(rows) == EXPECTED_ROW_COUNTS[path.name]
    finally:
        connection.close()
