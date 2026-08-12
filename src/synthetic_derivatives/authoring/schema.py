"""Define DuckDB storage, visibility views, and deterministic MERGE contracts.

Schema 2.5 still creates the legacy ``option_pricing_audit`` table so existing
databases remain readable.  Current authoring does not include that table in
``TABLE_SPECS`` and therefore cannot stage new IV-answer rows into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import duckdb


SCHEMA_VERSION = "2.5.0"
# Schema 2.5 replaces only the dependence contract table so it can store
# measure-qualified P/Q rows. Existing market observations are never rewritten.
MIGRATABLE_SCHEMA_VERSIONS = {
    "2.0.0",
    "2.1.0",
    "2.2.0",
    "2.3.0",
    "2.4.0",
}

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
    option_chain_spec_count BIGINT NOT NULL DEFAULT 0,
    option_pricing_audit_count BIGINT NOT NULL DEFAULT 0,
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
    -- Deprecated latent input for config <=1.4. Config 1.5+ derives Q pricing
    -- volatility from the declared diffusion and leaves this legacy column NULL.
    base_implied_volatility DOUBLE CHECK (
        base_implied_volatility IS NULL OR base_implied_volatility > 0
    ),
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, underlying_id)
);

-- Measure-qualified underlying/model-driver dependence. Config 1.7 writes a
-- P/Q pair; legacy configs write one P row. Q mapping columns are forbidden on
-- P and mandatory on Q.
CREATE TABLE IF NOT EXISTS market.underlying_dependence (
    snapshot_id VARCHAR NOT NULL,
    dependence_spec_id VARCHAR NOT NULL,
    measure VARCHAR NOT NULL CHECK (measure IN ('P', 'Q')),
    source_dependence_spec_id VARCHAR,
    mapping_id VARCHAR,
    mapping_type VARCHAR,
    risk_neutral_measure_id VARCHAR,
    numeraire_id VARCHAR,
    rate_path_id VARCHAR,
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
    CHECK (
        (measure = 'P'
            AND source_dependence_spec_id IS NULL
            AND mapping_id IS NULL
            AND mapping_type IS NULL
            AND risk_neutral_measure_id IS NULL
            AND numeraire_id IS NULL
            AND rate_path_id IS NULL)
        OR
        (measure = 'Q'
            AND source_dependence_spec_id IS NOT NULL
            AND mapping_id IS NOT NULL
            AND mapping_type = 'girsanov_drift_only_same_brownian_covariance'
            AND risk_neutral_measure_id IS NOT NULL
            AND numeraire_id IS NOT NULL
            AND rate_path_id IS NOT NULL)
    ),
    PRIMARY KEY (snapshot_id, dependence_spec_id)
);

-- Private option-chain authoring rules. Deliberately omitted from solver views.
-- Exactly one grid JSON is populated; moneyness is an authoring input rather
-- than a rule for recomputing strikes on each valuation date.
CREATE TABLE IF NOT EXISTS market.option_chain_specs (
    snapshot_id VARCHAR NOT NULL,
    chain_id VARCHAR NOT NULL,
    listing_rule VARCHAR NOT NULL CHECK (listing_rule = 'snapshot_start'),
    listing_date DATE NOT NULL,
    roll_rule VARCHAR NOT NULL CHECK (roll_rule = 'static'),
    grid_type VARCHAR NOT NULL CHECK (grid_type IN ('moneyness', 'strike')),
    expiry_days JSON NOT NULL,
    moneyness_grid JSON,
    strike_grid JSON,
    call_put JSON NOT NULL,
    strike_increment DECIMAL(24, 8) NOT NULL CHECK (strike_increment > 0),
    strike_rounding VARCHAR NOT NULL CHECK (strike_rounding = 'ROUND_HALF_EVEN'),
    exercise_style VARCHAR NOT NULL,
    settlement_type VARCHAR NOT NULL,
    contract_multiplier DECIMAL(24, 8) NOT NULL CHECK (contract_multiplier > 0),
    -- Nullable for migrated 2.2 rows. Config 1.4 stores the candidate-grid
    -- selection rule and complete BSM spread/noise contract here.
    liquidity_filter JSON,
    quote_model JSON,
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    CHECK (
        (grid_type = 'moneyness' AND moneyness_grid IS NOT NULL AND strike_grid IS NULL)
        OR (grid_type = 'strike' AND strike_grid IS NOT NULL AND moneyness_grid IS NULL)
    ),
    PRIMARY KEY (snapshot_id, chain_id)
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
    -- Nullable for migrated legacy rows. Chain rows persist the listing inputs
    -- for audit, while `strike` above remains the immutable economic term.
    chain_id VARCHAR,
    listing_date DATE,
    listing_spot DECIMAL(24, 8),
    strike_moneyness DECIMAL(24, 8),
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

-- Legacy schema-2.4 authoring table. Current pipelines do not solve IV or add
-- rows here; existing snapshots retain historical rows without exposing them
-- through solver_visible views.
CREATE TABLE IF NOT EXISTS market.option_pricing_audit (
    snapshot_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    option_id VARCHAR NOT NULL,
    risk_neutral_measure_id VARCHAR NOT NULL,
    numeraire_id VARCHAR NOT NULL,
    rate_path_id VARCHAR NOT NULL,
    measure_change VARCHAR NOT NULL CHECK (measure_change = 'girsanov_drift_only'),
    volatility_mapping VARCHAR NOT NULL CHECK (
        volatility_mapping = 'same_deterministic_diffusion'
    ),
    q_effective_volatility DOUBLE NOT NULL CHECK (q_effective_volatility > 0),
    -- Raw QuantLib NPV is retained for diagnostics and can contain a tiny
    -- negative floating-point artifact when the mathematical price is zero.
    theoretical_price DOUBLE NOT NULL,
    canonical_mid DECIMAL(24, 8) NOT NULL CHECK (canonical_mid >= 0),
    implied_volatility DOUBLE,
    iv_status VARCHAR NOT NULL CHECK (iv_status IN ('CONVERGED', 'NO_FINITE_IV')),
    iv_error VARCHAR,
    iv_solver JSON NOT NULL,
    generated_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, date, option_id),
    CHECK (
        (iv_status = 'CONVERGED' AND implied_volatility > 0 AND iv_error IS NULL)
        OR
        (iv_status = 'NO_FINITE_IV' AND implied_volatility IS NULL AND iv_error IS NOT NULL)
    )
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

CREATE OR REPLACE VIEW solver_visible.underlying_dependence AS
WITH measure_qualified_snapshots AS (
    SELECT snapshot_id
    FROM market.underlying_dependence
    GROUP BY snapshot_id
    HAVING sum(CASE WHEN measure = 'P' THEN 1 ELSE 0 END) = 1
       AND sum(CASE WHEN measure = 'Q' THEN 1 ELSE 0 END) = 1
)
SELECT
    dependence.snapshot_id,
    dependence.dependence_spec_id,
    dependence.measure,
    dependence.source_dependence_spec_id,
    dependence.mapping_id,
    dependence.mapping_type,
    dependence.risk_neutral_measure_id,
    dependence.numeraire_id,
    dependence.rate_path_id,
    dependence.driver_order,
    dependence.formulation,
    dependence.factor_loading_matrix,
    dependence.idiosyncratic_diagonal,
    dependence.correlation_matrix,
    dependence.matrix_dtype,
    dependence.factorization_method,
    dependence.factorization_order,
    dependence.time_grid,
    dependence.regime_id
FROM market.underlying_dependence AS dependence
JOIN measure_qualified_snapshots USING (snapshot_id);

CREATE OR REPLACE VIEW solver_visible.pricing_metadata AS
SELECT
    snapshot_id, valuation_timestamp, underlying_id, currency,
    discount_curve, risk_free_rate, dividend_curve, dividend_yield,
    borrow_or_carry_rate, calendar, day_count,
    json_object(
        'measure', json_extract_string(physical_dynamics, '$.measure'),
        'process', json_extract_string(physical_dynamics, '$.process'),
        'time_origin', json_extract_string(physical_dynamics, '$.time_origin'),
        'time_axis', json_extract_string(physical_dynamics, '$.time_axis'),
        'state_precision_contract',
            json_extract_string(physical_dynamics, '$.state_precision_contract'),
        'drift_function', json_object(
            'type', json_extract_string(physical_dynamics, '$.drift_function.type'),
            'node_offsets_calendar_days',
                json_extract(physical_dynamics, '$.drift_function.nodes[*].day_offset'),
            'extrapolation',
                json_extract_string(physical_dynamics, '$.drift_function.extrapolation')
        ),
        'volatility_function', json_object(
            'type', json_extract_string(physical_dynamics, '$.volatility_function.type'),
            'node_offsets_calendar_days',
                json_extract(physical_dynamics, '$.volatility_function.nodes[*].day_offset'),
            'extrapolation',
                json_extract_string(physical_dynamics, '$.volatility_function.extrapolation')
        )
    ) AS physical_dynamics,
    json_object(
        'measure', json_extract_string(pricing_dynamics, '$.measure'),
        'process', json_extract_string(pricing_dynamics, '$.process'),
        'risk_neutral_drift',
            json_extract_string(pricing_dynamics, '$.risk_neutral_drift'),
        'q_pricing', json_extract(pricing_dynamics, '$.q_pricing'),
        'underlying_dependence',
            json_extract(pricing_dynamics, '$.underlying_dependence'),
        'volatility_parameterization',
            'private_deterministic_diffusion',
        'task_input_reference', 'task-specific pricing context'
    ) AS pricing_dynamics,
    pricing_model, pricing_engine, generator_version,
    NULL::UBIGINT AS seed, rng, input_precision, canonicalization
FROM market.pricing_metadata;
"""


PRIVATE_METADATA_KEYS = frozenset(
    {
        "seed",
        "sampling_seed",
        "mutation_seed",
        "parameter_generator_seed",
        "value",
        "drift",
        "volatility",
        "clean_quotes",
        "quote_noise_realization",
        "requested_signature",
        "private_truth_signature",
        "mutation_lineage",
        "canonical_answer",
    }
)


def public_dynamics_projection(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose node locations and model semantics without private node heights."""

    def function_projection(function: Any) -> dict[str, Any]:
        if not isinstance(function, dict):
            raise ValueError("node function metadata must be an object")
        nodes = function.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("node function metadata requires public locations")
        return {
            "type": function.get("type"),
            "node_offsets_calendar_days": [int(node["day_offset"]) for node in nodes],
            "extrapolation": function.get("extrapolation"),
        }

    return {
        key: raw[key]
        for key in (
            "measure",
            "process",
            "time_origin",
            "time_axis",
            "state_precision_contract",
        )
        if key in raw
    } | {
        "drift_function": function_projection(raw["drift_function"]),
        "volatility_function": function_projection(raw["volatility_function"]),
    }


def assert_no_private_metadata_leakage(value: Any, path: str = "public") -> None:
    """Recursively reject private values even when nested or renamed by a wrapper."""

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in PRIVATE_METADATA_KEYS:
                raise ValueError(f"private metadata field leaked at {path}.{key}")
            assert_no_private_metadata_leakage(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_private_metadata_leakage(item, f"{path}[{index}]")


@dataclass(frozen=True)
class TableSpec:
    """Ordered table contract used by deterministic staging and MERGE calls."""

    name: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]


# Only tables produced by the current authoring contract belong here.  The
# schema-retained option_pricing_audit table is intentionally absent: replay
# compares current materialized rows, while manifest counts can still report
# historical audit rows in a legacy database.
TABLE_SPECS = {
    "underlying_dependence": TableSpec(
        "market.underlying_dependence",
        (
            "snapshot_id", "dependence_spec_id", "measure",
            "source_dependence_spec_id", "mapping_id", "mapping_type",
            "risk_neutral_measure_id", "numeraire_id", "rate_path_id",
            "driver_order", "formulation", "factor_loading_matrix",
            "idiosyncratic_diagonal", "correlation_matrix", "matrix_dtype",
            "factorization_method", "factorization_order", "time_grid",
            "regime_id", "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "dependence_spec_id"),
    ),
    "option_chain_specs": TableSpec(
        "market.option_chain_specs",
        (
            "snapshot_id", "chain_id", "listing_rule", "listing_date",
            "roll_rule", "grid_type", "expiry_days", "moneyness_grid",
            "strike_grid", "call_put", "strike_increment", "strike_rounding",
            "exercise_style", "settlement_type", "contract_multiplier",
            "liquidity_filter", "quote_model",
            "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "chain_id"),
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
            "chain_id", "listing_date", "listing_spot", "strike_moneyness",
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
    """Create the current schema or apply the supported logical migration.

    Existing mutable 2.0--2.4 snapshots keep every market observation unchanged.
    The dependence table is rebuilt only to replace its P-only CHECK constraint
    with the P/Q contract and nullable mapping columns. A frozen database is
    never migrated in place; it remains readable through a read-only connection.
    """

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
    legacy_dependence_table = False
    if current_version and current_version[0] != SCHEMA_VERSION:
        snapshots_table_exists = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'metadata' AND table_name = 'snapshots'
            """
        ).fetchone()[0]
        if snapshots_table_exists:
            frozen_count = connection.execute(
                "SELECT count(*) FROM metadata.snapshots WHERE status = 'FROZEN'"
            ).fetchone()[0]
            if frozen_count:
                raise RuntimeError(
                    "frozen snapshot schema is immutable; open it read-only or "
                    "clone it before migration"
                )
        legacy_dependence_table = bool(
            connection.execute(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'market'
                  AND table_name = 'underlying_dependence'
                """
            ).fetchone()[0]
        )
        if legacy_dependence_table:
            connection.execute(
                """
                ALTER TABLE market.underlying_dependence
                RENAME TO underlying_dependence_schema_2_4
                """
            )
    connection.execute(DDL)
    if legacy_dependence_table:
        connection.execute(
            """
            INSERT INTO market.underlying_dependence (
                snapshot_id, dependence_spec_id, measure,
                source_dependence_spec_id, mapping_id, mapping_type,
                risk_neutral_measure_id, numeraire_id, rate_path_id,
                driver_order, formulation, factor_loading_matrix,
                idiosyncratic_diagonal, correlation_matrix, matrix_dtype,
                factorization_method, factorization_order, time_grid,
                regime_id, generator_config_id, created_run_id
            )
            SELECT
                snapshot_id, dependence_spec_id, measure,
                NULL, NULL, NULL, NULL, NULL, NULL,
                driver_order, formulation, factor_loading_matrix,
                idiosyncratic_diagonal, correlation_matrix, matrix_dtype,
                factorization_method, factorization_order, time_grid,
                regime_id, generator_config_id, created_run_id
            FROM market.underlying_dependence_schema_2_4
            """
        )
        connection.execute(
            "DROP TABLE market.underlying_dependence_schema_2_4"
        )
    connection.execute(
        """
        ALTER TABLE metadata.snapshot_revisions
        ADD COLUMN IF NOT EXISTS underlying_dependence_count BIGINT
        DEFAULT 0
        """
    )
    connection.execute(
        """
        ALTER TABLE metadata.snapshot_revisions
        ADD COLUMN IF NOT EXISTS option_chain_spec_count BIGINT
        DEFAULT 0
        """
    )
    connection.execute(
        """
        ALTER TABLE metadata.snapshot_revisions
        ADD COLUMN IF NOT EXISTS option_pricing_audit_count BIGINT
        DEFAULT 0
        """
    )
    connection.execute(
        """
        ALTER TABLE market.underlyings
        ALTER COLUMN base_implied_volatility DROP NOT NULL
        """
    )
    for column_definition in (
        "chain_id VARCHAR",
        "listing_date DATE",
        "listing_spot DECIMAL(24, 8)",
        "strike_moneyness DECIMAL(24, 8)",
    ):
        connection.execute(
            f"""
            ALTER TABLE market.option_contracts
            ADD COLUMN IF NOT EXISTS {column_definition}
            """
        )
    for column_definition in (
        "liquidity_filter JSON",
        "quote_model JSON",
    ):
        connection.execute(
            f"""
            ALTER TABLE market.option_chain_specs
            ADD COLUMN IF NOT EXISTS {column_definition}
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
