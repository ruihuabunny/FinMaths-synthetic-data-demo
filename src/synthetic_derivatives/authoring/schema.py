"""DuckDB schema and transactional incremental insert primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import duckdb


SCHEMA_VERSION = "2.1.0"
MIGRATABLE_SCHEMA_VERSIONS = {"2.0.0"}

SCHEMA_BOOTSTRAP = r"""
CREATE SCHEMA IF NOT EXISTS metadata;
CREATE TABLE IF NOT EXISTS metadata.schema_versions (
    schema_version VARCHAR PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
"""

DDL = r"""
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS solver_visible;

CREATE TABLE IF NOT EXISTS metadata.snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    schema_version VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('DRAFT', 'FROZEN')),
    generator_config_id VARCHAR NOT NULL,
    generator_version VARCHAR NOT NULL,
    quantlib_version VARCHAR NOT NULL,
    duckdb_version VARCHAR NOT NULL,
    seed UBIGINT NOT NULL,
    rng VARCHAR NOT NULL,
    current_revision INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    frozen_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS metadata.generation_runs (
    run_id VARCHAR PRIMARY KEY,
    snapshot_id VARCHAR NOT NULL,
    operation VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'NOOP', 'FAILED')),
    generator_config_id VARCHAR NOT NULL,
    generator_version VARCHAR NOT NULL,
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
    underlying_count BIGINT NOT NULL,
    option_contract_count BIGINT NOT NULL,
    underlying_daily_count BIGINT NOT NULL,
    option_daily_count BIGINT NOT NULL,
    pricing_metadata_count BIGINT NOT NULL,
    underlying_dependence_count BIGINT NOT NULL DEFAULT 0,
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
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, underlying_id)
);

CREATE TABLE IF NOT EXISTS market.underlying_dependence (
    snapshot_id VARCHAR NOT NULL,
    dependence_spec_id VARCHAR NOT NULL,
    measure VARCHAR NOT NULL CHECK (measure = 'P'),
    driver_order JSON NOT NULL,
    formulation VARCHAR NOT NULL CHECK (formulation = 'factor_loading'),
    factor_loading_matrix JSON NOT NULL,
    idiosyncratic_diagonal JSON NOT NULL,
    correlation_matrix JSON NOT NULL,
    matrix_dtype VARCHAR NOT NULL CHECK (matrix_dtype = 'float64'),
    factorization_method VARCHAR NOT NULL
        CHECK (factorization_method = 'factor_loading_direct'),
    factorization_order VARCHAR NOT NULL
        CHECK (factorization_order = 'declared_driver_order'),
    time_grid VARCHAR NOT NULL CHECK (time_grid = 'business_daily'),
    regime_id VARCHAR NOT NULL,
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, dependence_spec_id)
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
    created_run_id VARCHAR NOT NULL,
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


TABLE_SPECS = {
    "underlying_dependence": TableSpec(
        "market.underlying_dependence",
        (
            "snapshot_id", "dependence_spec_id", "measure", "driver_order",
            "formulation", "factor_loading_matrix",
            "idiosyncratic_diagonal", "correlation_matrix", "matrix_dtype",
            "factorization_method", "factorization_order", "time_grid",
            "regime_id", "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "dependence_spec_id"),
    ),
    "underlyings": TableSpec(
        "market.underlyings",
        (
            "snapshot_id", "underlying_id", "currency", "asset_class",
            "initial_spot", "physical_drift", "physical_volatility",
            "risk_free_rate", "dividend_yield", "base_implied_volatility",
            "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "underlying_id"),
    ),
    "option_contracts": TableSpec(
        "market.option_contracts",
        (
            "snapshot_id", "option_id", "underlying_id", "template_id",
            "call_put", "strike", "expiry", "exercise_style",
            "settlement_type", "contract_multiplier",
            "created_run_id",
        ),
        ("snapshot_id", "option_id"),
    ),
    "underlying_daily": TableSpec(
        "market.underlying_daily",
        (
            "snapshot_id", "date", "underlying_id", "spot_open", "spot_high",
            "spot_low", "spot_close", "adjusted_close", "volume", "dividend",
            "corporate_action", "generated_run_id",
        ),
        ("snapshot_id", "date", "underlying_id"),
    ),
    "option_daily": TableSpec(
        "market.option_daily",
        (
            "snapshot_id", "date", "underlying_id", "option_id", "call_put",
            "strike", "expiry", "exercise_style", "settlement_type",
            "contract_multiplier", "bid", "ask", "mid", "settlement_price",
            "volume", "open_interest", "generated_run_id",
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
            "rng", "input_precision", "canonicalization", "generated_run_id",
        ),
        ("snapshot_id", "valuation_timestamp", "underlying_id"),
    ),
}


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(SCHEMA_BOOTSTRAP)
    current_version = connection.execute(
        """
        SELECT schema_version FROM metadata.schema_versions
        ORDER BY applied_at DESC, schema_version DESC LIMIT 1
        """
    ).fetchone()
    if (
        current_version
        and current_version[0] != SCHEMA_VERSION
        and current_version[0] not in MIGRATABLE_SCHEMA_VERSIONS
    ):
        raise RuntimeError(
            f"unsupported DuckDB schema version: {current_version[0]}"
        )
    connection.execute(DDL)
    connection.execute(
        """
        ALTER TABLE metadata.snapshot_revisions
        ADD COLUMN IF NOT EXISTS underlying_dependence_count BIGINT
        DEFAULT 0
        """
    )
    connection.execute(
        """
        INSERT INTO metadata.schema_versions (schema_version)
        VALUES (?) ON CONFLICT DO NOTHING
        """,
        [SCHEMA_VERSION],
    )
    if current_version and current_version[0] != SCHEMA_VERSION:
        connection.execute(
            """
            UPDATE metadata.snapshots
            SET schema_version = ?
            WHERE schema_version = ?
            """,
            [SCHEMA_VERSION, current_version[0]],
        )


def merge_rows(
    connection: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    rows: Sequence[Sequence[Any]],
) -> dict[str, int]:
    """Insert rows whose primary IDs do not already exist."""

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
    finally:
        connection.execute(f"DROP TABLE {stage_name}")

    inserted = sum(action[0] == "INSERT" for action in actions)
    return {
        "requested": len(rows),
        "inserted": inserted,
        "unchanged": len(rows) - inserted,
    }


def table_columns(connection: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()]
