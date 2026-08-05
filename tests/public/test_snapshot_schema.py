from __future__ import annotations

import json
from pathlib import Path

import duckdb

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


EXPECTED_UNDERLYING_FIELDS = {
    "date",
    "underlying_id",
    "spot_open",
    "spot_high",
    "spot_low",
    "spot_close",
    "adjusted_close",
    "volume",
    "dividend",
    "corporate_action",
}

EXPECTED_OPTION_FIELDS = {
    "date",
    "underlying_id",
    "option_id",
    "call_put",
    "strike",
    "expiry",
    "exercise_style",
    "settlement_type",
    "contract_multiplier",
    "bid",
    "ask",
    "mid",
    "settlement_price",
    "volume",
    "open_interest",
}

EXPECTED_METADATA_FIELDS = {
    "valuation_timestamp",
    "currency",
    "discount_curve",
    "risk_free_rate",
    "dividend_curve",
    "dividend_yield",
    "borrow_or_carry_rate",
    "calendar",
    "day_count",
    "physical_dynamics",
    "pricing_dynamics",
    "pricing_model",
    "pricing_engine",
    "generator_version",
    "seed",
    "rng",
    "input_precision",
    "canonicalization",
}


def _columns(connection: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()}


def test_solver_visible_views_cover_framework_fields(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "schema.duckdb"
    with AuthoringPipeline(database, load_generator_config(smoke_config_path)) as pipeline:
        pipeline.create_smoke_snapshot()

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert EXPECTED_UNDERLYING_FIELDS <= _columns(
            connection, "solver_visible.underlying_daily"
        )
        assert EXPECTED_OPTION_FIELDS <= _columns(
            connection, "solver_visible.option_daily"
        )
        assert EXPECTED_METADATA_FIELDS <= _columns(
            connection, "solver_visible.pricing_metadata"
        )
    finally:
        connection.close()


def test_checked_in_smoke_snapshot_matches_its_manifest(repository_root: Path) -> None:
    database = repository_root / "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
    manifest = json.loads(
        database.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    connection = duckdb.connect(str(database), read_only=True)
    try:
        catalog = connection.execute(
            """
            SELECT status, current_revision
            FROM metadata.snapshots WHERE snapshot_id = ?
            """,
            [manifest["snapshot_id"]],
        ).fetchone()
        assert catalog == (
            manifest["status"],
            manifest["revision"],
        )
        assert connection.execute(
            "SELECT count(*) FROM market.underlying_daily"
        ).fetchone()[0] == manifest["underlying_daily_count"]
        assert connection.execute(
            "SELECT count(*) FROM market.option_daily"
        ).fetchone()[0] == manifest["option_daily_count"]
    finally:
        connection.close()
