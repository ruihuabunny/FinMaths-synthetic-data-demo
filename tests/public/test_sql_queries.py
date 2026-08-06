from __future__ import annotations

import re
from pathlib import Path

import duckdb


EXPECTED_ROW_COUNTS = {
    "generation_audit.sql": 1,
    "option_chain.sql": 56,
    "option_chain_authoring_spec.sql": 1,
    "option_iv_authoring_answers.sql": 56,
    "option_iv_task_inputs.sql": 56,
    "option_pricing_context.sql": 56,
    "option_spot_moneyness.sql": 56,
    "snapshot_summary.sql": 1,
    "underlying_time_series.sql": 65,
    "underlying_dependence.sql": 1,
    "underlying_dynamics_authoring_audit.sql": 22,
}

SOLVER_SAFE_QUERIES = {
    "option_chain.sql",
    "option_iv_task_inputs.sql",
    "option_pricing_context.sql",
    "option_spot_moneyness.sql",
    "underlying_time_series.sql",
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
            if path.name in SOLVER_SAFE_QUERIES:
                assert re.search(
                    r"\b(?:FROM|JOIN)\s+(?:market|metadata)\.",
                    query,
                    flags=re.IGNORECASE,
                ) is None
            rows = connection.execute(query).fetchall()
            assert len(rows) == EXPECTED_ROW_COUNTS[path.name]
            columns = [item[0] for item in connection.description]
            if path.name == "snapshot_summary.sql":
                summary = dict(zip(columns, rows[0]))
                assert summary["quantlib_version"] == "1.39"
                assert summary["duckdb_version"] == "1.5.5"
                assert summary["seed"] == 20260806
                assert summary["converged_iv_count"] == 59_836
                assert summary["no_finite_iv_count"] == 532
            elif path.name == "underlying_dynamics_authoring_audit.sql":
                drift_match_index = columns.index("drift_day0_matches_master")
                volatility_match_index = columns.index(
                    "volatility_day0_matches_master"
                )
                assert all(row[drift_match_index] for row in rows)
                assert all(row[volatility_match_index] for row in rows)
    finally:
        connection.close()
