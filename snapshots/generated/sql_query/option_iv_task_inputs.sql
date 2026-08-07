-- Solver-safe IV task inputs. The private IV audit table is intentionally excluded.
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2',
        DATE '2026-08-03',
        'SYNTH-METAL-GOLD'
    )
)
SELECT
    quote.snapshot_id,
    quote.date AS valuation_date,
    CAST(
        pricing.valuation_timestamp AT TIME ZONE 'UTC' AS VARCHAR
    ) AS valuation_timestamp_utc,
    quote.underlying_id,
    quote.option_id,
    underlying.spot_close,
    quote.call_put,
    quote.strike,
    quote.expiry,
    date_diff('day', quote.date, quote.expiry) AS days_to_expiry,
    CAST(date_diff('day', quote.date, quote.expiry) AS DOUBLE) / 365.0
        AS time_to_expiry_years_actual_365_fixed,
    quote.exercise_style,
    quote.settlement_type,
    quote.contract_multiplier,
    pricing.currency,
    pricing.risk_free_rate,
    pricing.dividend_yield,
    pricing.borrow_or_carry_rate,
    pricing.calendar,
    pricing.day_count,
    pricing.pricing_model,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.implied_volatility_solver.target_quote'
    ) AS target_quote,
    quote.mid AS target_option_price,
    quote.bid,
    quote.ask,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.risk_neutral_measure_id'
    ) AS risk_neutral_measure_id,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.numeraire_id'
    ) AS numeraire_id,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.rate_path_id'
    ) AS rate_path_id,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.measure_change'
    ) AS measure_change,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.volatility_mapping'
    ) AS volatility_mapping,
    json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.implied_volatility_solver.method'
    ) AS iv_method_contract,
    CAST(json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.implied_volatility_solver.accuracy'
    ) AS DOUBLE) AS iv_accuracy,
    CAST(json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.implied_volatility_solver.max_evaluations'
    ) AS INTEGER) AS iv_max_evaluations,
    CAST(json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.implied_volatility_solver.minimum_volatility'
    ) AS DOUBLE) AS iv_minimum_volatility,
    CAST(json_extract_string(
        pricing.pricing_dynamics,
        '$.q_pricing.implied_volatility_solver.maximum_volatility'
    ) AS DOUBLE) AS iv_maximum_volatility,
    pricing.input_precision,
    pricing.canonicalization
FROM solver_visible.option_daily AS quote
JOIN solver_visible.underlying_daily AS underlying
  ON underlying.snapshot_id = quote.snapshot_id
 AND underlying.date = quote.date
 AND underlying.underlying_id = quote.underlying_id
JOIN solver_visible.pricing_metadata AS pricing
  ON pricing.snapshot_id = quote.snapshot_id
 AND CAST(pricing.valuation_timestamp AT TIME ZONE 'UTC' AS DATE) = quote.date
 AND pricing.underlying_id = quote.underlying_id
JOIN parameters
  ON parameters.snapshot_id = quote.snapshot_id
 AND parameters.market_date = quote.date
 AND parameters.underlying_id = quote.underlying_id
ORDER BY quote.expiry, quote.call_put, quote.strike, quote.option_id;
