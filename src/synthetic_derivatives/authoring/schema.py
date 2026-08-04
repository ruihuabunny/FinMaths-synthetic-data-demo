"""DuckDB schema and transactional incremental merge primitives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import uuid4

import duckdb


SCHEMA_VERSION = "1.0.0"

DDL = r"""
CREATE SCHEMA IF NOT EXISTS metadata;
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS solver_visible;

CREATE TABLE IF NOT EXISTS metadata.schema_migrations (
    schema_version VARCHAR PRIMARY KEY,
    ddl_sha256 VARCHAR NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS metadata.snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    schema_version VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('DRAFT', 'FROZEN')),
    generator_config_id VARCHAR NOT NULL,
    generator_version VARCHAR NOT NULL,
    current_config_sha256 VARCHAR NOT NULL,
    quantlib_version VARCHAR NOT NULL,
    duckdb_version VARCHAR NOT NULL,
    seed UBIGINT NOT NULL,
    rng VARCHAR NOT NULL,
    current_revision INTEGER NOT NULL DEFAULT 0,
    content_sha256 VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    frozen_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS metadata.generation_runs (
    run_id VARCHAR PRIMARY KEY,
    snapshot_id VARCHAR NOT NULL,
    operation VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'NOOP', 'FAILED')),
    config_sha256 VARCHAR NOT NULL,
    requested_start_date DATE,
    requested_end_date DATE,
    table_stats JSON,
    error_message VARCHAR,
    started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS metadata.snapshot_revisions (
    snapshot_id VARCHAR NOT NULL,
    revision INTEGER NOT NULL,
    run_id VARCHAR NOT NULL,
    config_sha256 VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    underlying_count BIGINT NOT NULL,
    option_contract_count BIGINT NOT NULL,
    underlying_daily_count BIGINT NOT NULL,
    option_daily_count BIGINT NOT NULL,
    pricing_metadata_count BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (snapshot_id, revision)
);

CREATE TABLE IF NOT EXISTS market.underlyings (
    snapshot_id VARCHAR NOT NULL,
    underlying_id VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    asset_class VARCHAR NOT NULL,
    initial_spot DECIMAL(24, 8) NOT NULL CHECK (initial_spot > 0),
    physical_drift DOUBLE NOT NULL,
    physical_volatility DOUBLE NOT NULL CHECK (physical_volatility > 0),
    risk_free_rate DOUBLE NOT NULL,
    dividend_yield DOUBLE NOT NULL,
    base_implied_volatility DOUBLE NOT NULL CHECK (base_implied_volatility > 0),
    generator_config_id VARCHAR NOT NULL,
    row_sha256 VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    last_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, underlying_id)
);

CREATE TABLE IF NOT EXISTS market.option_contracts (
    snapshot_id VARCHAR NOT NULL,
    option_id VARCHAR NOT NULL,
    underlying_id VARCHAR NOT NULL,
    template_id VARCHAR NOT NULL,
    call_put VARCHAR NOT NULL CHECK (call_put IN ('call', 'put')),
    strike DECIMAL(24, 8) NOT NULL CHECK (strike > 0),
    expiry DATE NOT NULL,
    exercise_style VARCHAR NOT NULL,
    settlement_type VARCHAR NOT NULL,
    contract_multiplier DECIMAL(24, 8) NOT NULL CHECK (contract_multiplier > 0),
    row_sha256 VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    last_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, option_id)
);

CREATE TABLE IF NOT EXISTS market.underlying_daily (
    snapshot_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    underlying_id VARCHAR NOT NULL,
    spot_open DECIMAL(24, 8) NOT NULL CHECK (spot_open > 0),
    spot_high DECIMAL(24, 8) NOT NULL CHECK (spot_high > 0),
    spot_low DECIMAL(24, 8) NOT NULL CHECK (spot_low > 0),
    spot_close DECIMAL(24, 8) NOT NULL CHECK (spot_close > 0),
    adjusted_close DECIMAL(24, 8) NOT NULL CHECK (adjusted_close > 0),
    volume BIGINT NOT NULL CHECK (volume >= 0),
    dividend DECIMAL(24, 8) NOT NULL,
    corporate_action VARCHAR NOT NULL,
    row_sha256 VARCHAR NOT NULL,
    generated_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, date, underlying_id),
    CHECK (spot_high >= spot_open AND spot_high >= spot_close),
    CHECK (spot_low <= spot_open AND spot_low <= spot_close),
    CHECK (spot_high >= spot_low)
);

CREATE TABLE IF NOT EXISTS market.option_daily (
    snapshot_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    underlying_id VARCHAR NOT NULL,
    option_id VARCHAR NOT NULL,
    call_put VARCHAR NOT NULL CHECK (call_put IN ('call', 'put')),
    strike DECIMAL(24, 8) NOT NULL CHECK (strike > 0),
    expiry DATE NOT NULL,
    exercise_style VARCHAR NOT NULL,
    settlement_type VARCHAR NOT NULL,
    contract_multiplier DECIMAL(24, 8) NOT NULL CHECK (contract_multiplier > 0),
    bid DECIMAL(24, 8) NOT NULL CHECK (bid >= 0),
    ask DECIMAL(24, 8) NOT NULL CHECK (ask >= bid),
    mid DECIMAL(24, 8) NOT NULL CHECK (mid >= bid AND mid <= ask),
    settlement_price DECIMAL(24, 8) NOT NULL CHECK (settlement_price >= 0),
    volume BIGINT NOT NULL CHECK (volume >= 0),
    open_interest BIGINT NOT NULL CHECK (open_interest >= 0),
    row_sha256 VARCHAR NOT NULL,
    generated_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, date, option_id)
);

CREATE TABLE IF NOT EXISTS market.pricing_metadata (
    snapshot_id VARCHAR NOT NULL,
    valuation_timestamp TIMESTAMPTZ NOT NULL,
    valuation_date DATE NOT NULL,
    underlying_id VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    discount_curve JSON NOT NULL,
    risk_free_rate DOUBLE NOT NULL,
    dividend_curve JSON NOT NULL,
    dividend_yield DOUBLE NOT NULL,
    borrow_or_carry_rate DOUBLE NOT NULL,
    calendar VARCHAR NOT NULL,
    day_count VARCHAR NOT NULL,
    physical_dynamics JSON NOT NULL,
    pricing_dynamics JSON NOT NULL,
    pricing_model VARCHAR NOT NULL,
    pricing_engine VARCHAR NOT NULL,
    generator_version VARCHAR NOT NULL,
    seed UBIGINT NOT NULL,
    rng VARCHAR NOT NULL,
    input_precision JSON NOT NULL,
    canonicalization JSON NOT NULL,
    row_sha256 VARCHAR NOT NULL,
    generated_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, valuation_timestamp, underlying_id)
);

CREATE OR REPLACE VIEW solver_visible.underlying_daily AS
SELECT
    snapshot_id, date, underlying_id, spot_open, spot_high, spot_low,
    spot_close, adjusted_close, volume, dividend, corporate_action
FROM market.underlying_daily;

CREATE OR REPLACE VIEW solver_visible.option_daily AS
SELECT
    snapshot_id, date, underlying_id, option_id, call_put, strike, expiry,
    exercise_style, settlement_type, contract_multiplier, bid, ask, mid,
    settlement_price, volume, open_interest
FROM market.option_daily;

CREATE OR REPLACE VIEW solver_visible.pricing_metadata AS
SELECT
    snapshot_id, valuation_timestamp, underlying_id, currency,
    discount_curve, risk_free_rate, dividend_curve, dividend_yield,
    borrow_or_carry_rate, calendar, day_count, physical_dynamics,
    pricing_dynamics, pricing_model, pricing_engine, generator_version,
    seed, rng, input_precision, canonicalization
FROM market.pricing_metadata;
"""


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]
    immutable_on_update: tuple[str, ...] = ()


TABLE_SPECS = {
    "underlyings": TableSpec(
        "market.underlyings",
        (
            "snapshot_id", "underlying_id", "currency", "asset_class",
            "initial_spot", "physical_drift", "physical_volatility",
            "risk_free_rate", "dividend_yield", "base_implied_volatility",
            "generator_config_id", "row_sha256", "created_run_id", "last_run_id",
        ),
        ("snapshot_id", "underlying_id"),
        ("created_run_id",),
    ),
    "option_contracts": TableSpec(
        "market.option_contracts",
        (
            "snapshot_id", "option_id", "underlying_id", "template_id",
            "call_put", "strike", "expiry", "exercise_style",
            "settlement_type", "contract_multiplier", "row_sha256",
            "created_run_id", "last_run_id",
        ),
        ("snapshot_id", "option_id"),
        ("created_run_id",),
    ),
    "underlying_daily": TableSpec(
        "market.underlying_daily",
        (
            "snapshot_id", "date", "underlying_id", "spot_open", "spot_high",
            "spot_low", "spot_close", "adjusted_close", "volume", "dividend",
            "corporate_action", "row_sha256", "generated_run_id",
        ),
        ("snapshot_id", "date", "underlying_id"),
    ),
    "option_daily": TableSpec(
        "market.option_daily",
        (
            "snapshot_id", "date", "underlying_id", "option_id", "call_put",
            "strike", "expiry", "exercise_style", "settlement_type",
            "contract_multiplier", "bid", "ask", "mid", "settlement_price",
            "volume", "open_interest", "row_sha256", "generated_run_id",
        ),
        ("snapshot_id", "date", "option_id"),
    ),
    "pricing_metadata": TableSpec(
        "market.pricing_metadata",
        (
            "snapshot_id", "valuation_timestamp", "valuation_date",
            "underlying_id", "currency", "discount_curve", "risk_free_rate",
            "dividend_curve", "dividend_yield", "borrow_or_carry_rate",
            "calendar", "day_count", "physical_dynamics", "pricing_dynamics",
            "pricing_model", "pricing_engine", "generator_version", "seed",
            "rng", "input_precision", "canonicalization", "row_sha256",
            "generated_run_id",
        ),
        ("snapshot_id", "valuation_timestamp", "underlying_id"),
    ),
}


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    ddl_hash = hashlib.sha256(DDL.encode("utf-8")).hexdigest()
    schema_exists = connection.execute(
        """
        SELECT count(*)
        FROM information_schema.tables
        WHERE table_schema = 'metadata' AND table_name = 'schema_migrations'
        """
    ).fetchone()[0]
    if schema_exists:
        migration = connection.execute(
            """
            SELECT ddl_sha256 FROM metadata.schema_migrations
            WHERE schema_version = ?
            """,
            [SCHEMA_VERSION],
        ).fetchone()
        if migration is None or migration[0] != ddl_hash:
            raise RuntimeError(
                "DuckDB schema checksum mismatch; add an explicit schema migration"
            )
        return

    connection.execute(DDL)
    connection.execute(
        """
        INSERT INTO metadata.schema_migrations (schema_version, ddl_sha256)
        VALUES (?, ?)
        """,
        [SCHEMA_VERSION, ddl_hash],
    )


def merge_rows(
    connection: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    rows: Sequence[Sequence[Any]],
) -> dict[str, int]:
    """Merge one incremental batch and leave byte-identical logical rows untouched."""

    if not rows:
        return {"requested": 0, "inserted": 0, "updated": 0, "unchanged": 0}
    if any(len(row) != len(spec.columns) for row in rows):
        raise ValueError(f"row width does not match {spec.name}")

    stage_name = f"stage_{uuid4().hex}"
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
        update_columns = tuple(
            column
            for column in spec.columns
            if column not in spec.keys and column not in spec.immutable_on_update
        )
        update_set = ", ".join(
            f"{column} = source.{column}" for column in update_columns
        )
        insert_values = ", ".join(f"source.{column}" for column in spec.columns)
        actions = connection.execute(
            f"""
            MERGE INTO {spec.name} AS target
            USING {stage_name} AS source
            ON {key_join}
            WHEN MATCHED AND target.row_sha256 <> source.row_sha256 THEN
                UPDATE SET {update_set}
            WHEN NOT MATCHED THEN
                INSERT ({column_csv}) VALUES ({insert_values})
            RETURNING merge_action
            """
        ).fetchall()
    finally:
        connection.execute(f"DROP TABLE IF EXISTS {stage_name}")

    inserted = sum(action[0] == "INSERT" for action in actions)
    updated = sum(action[0] == "UPDATE" for action in actions)
    return {
        "requested": len(rows),
        "inserted": inserted,
        "updated": updated,
        "unchanged": len(rows) - inserted - updated,
    }


def table_columns(connection: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()]
