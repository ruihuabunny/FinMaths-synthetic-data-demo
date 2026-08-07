-- Edit snapshot_id, market_date and underlying_id for the desired pricing slice.
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2',
        DATE '2026-08-03',
        'SYNTH-METAL-GOLD'
    )
)
SELECT
    quote.snapshot_id,
    quote.date,
    quote.underlying_id,
    quote.option_id,
    underlying.spot_close,
    quote.call_put,
    quote.strike,
    quote.expiry,
    date_diff('day', quote.date, quote.expiry) AS days_to_expiry,
    CAST(date_diff('day', quote.date, quote.expiry) AS DOUBLE) / 365.0
        AS time_to_expiry_years_actual_365_fixed,
    quote.bid,
    quote.mid,
    quote.ask,
    quote.settlement_price,
    CAST(
        pricing.valuation_timestamp AT TIME ZONE 'UTC' AS VARCHAR
    ) AS valuation_timestamp_utc,
    pricing.currency,
    pricing.risk_free_rate,
    pricing.discount_curve,
    pricing.dividend_yield,
    pricing.dividend_curve,
    pricing.borrow_or_carry_rate,
    pricing.calendar,
    pricing.day_count,
    pricing.pricing_model,
    pricing.pricing_engine,
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
        '$.risk_neutral_drift'
    ) AS risk_neutral_drift,
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
        '$.quote_iv_source'
    ) AS quote_iv_source,
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
