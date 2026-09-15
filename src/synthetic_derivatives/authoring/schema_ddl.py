"""Current DuckDB schema and solver-visible view DDL.

Schema 2.6 adds private bridge and volume observation-law tables while still
creating the legacy ``option_pricing_audit`` table so existing databases remain
readable. Current authoring cannot stage new IV-answer rows into that table.
"""

from __future__ import annotations

SCHEMA_VERSION = "2.6.0"

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
    intraday_bridge_spec_count BIGINT NOT NULL DEFAULT 0,
    underlying_volume_model_count BIGINT NOT NULL DEFAULT 0,
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

-- Private config-1.8 observation law. Deliberately omitted from solver views.
CREATE TABLE IF NOT EXISTS market.intraday_bridge_specs (
    snapshot_id VARCHAR NOT NULL,
    bridge_spec_id VARCHAR NOT NULL CHECK (length(bridge_spec_id) > 0),
    method VARCHAR NOT NULL CHECK (method = 'log_price_brownian_bridge'),
    steps INTEGER NOT NULL CHECK (steps BETWEEN 2 AND 4096),
    grid VARCHAR NOT NULL CHECK (grid = 'uniform_calendar_fraction'),
    variance_clock VARCHAR NOT NULL CHECK (variance_clock = 'integrated_variance'),
    endpoint_policy VARCHAR NOT NULL CHECK (
        endpoint_policy = 'published_quantized_open_close'
    ),
    extrema_policy VARCHAR NOT NULL CHECK (
        extrema_policy = 'quantized_discrete_grid_only'
    ),
    cross_asset_policy VARCHAR NOT NULL CHECK (
        cross_asset_policy = 'marginal_independent_given_close_endpoints'
    ),
    stream_namespace VARCHAR NOT NULL CHECK (length(stream_namespace) > 0),
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, bridge_spec_id)
);

-- One private per-underlying volume parameter row under a shared model ID.
CREATE TABLE IF NOT EXISTS market.underlying_volume_models (
    snapshot_id VARCHAR NOT NULL,
    volume_spec_id VARCHAR NOT NULL CHECK (length(volume_spec_id) > 0),
    underlying_id VARCHAR NOT NULL,
    measure VARCHAR NOT NULL CHECK (measure = 'P'),
    method VARCHAR NOT NULL CHECK (method = 'keyed_mean_preserving_lognormal'),
    base_volume BIGINT NOT NULL CHECK (base_volume > 0),
    volume_log_stddev DOUBLE NOT NULL CHECK (
        isfinite(volume_log_stddev)
        AND volume_log_stddev BETWEEN 0 AND 2
    ),
    rounding VARCHAR NOT NULL CHECK (rounding = 'ROUND_HALF_EVEN_INTEGER'),
    overflow_policy VARCHAR NOT NULL CHECK (overflow_policy = 'clip_signed_int64'),
    dependence_policy VARCHAR NOT NULL CHECK (
        dependence_policy = 'independent_by_underlying_date_and_from_price_streams'
    ),
    stream_namespace VARCHAR NOT NULL CHECK (length(stream_namespace) > 0),
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, volume_spec_id, underlying_id)
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
    NULL::UBIGINT AS seed,
    CASE
        WHEN rng = 'QuantLib.BoxMullerMersenneTwisterGaussianRng/semantic-keyed-authoring-streams-sha256-v2'
        THEN NULL::VARCHAR
        ELSE rng
    END AS rng,
    input_precision, canonicalization
FROM market.pricing_metadata;
"""
