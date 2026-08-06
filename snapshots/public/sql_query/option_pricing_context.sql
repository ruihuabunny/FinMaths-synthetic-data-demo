-- Edit snapshot_id, market_date and underlying_id for the desired pricing slice.
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3',
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
    quote.bid,
    quote.mid,
    quote.ask,
    quote.settlement_price,
    CAST(
        pricing.valuation_timestamp AT TIME ZONE 'UTC' AS VARCHAR
    ) AS valuation_timestamp_utc,
    pricing.risk_free_rate,
    pricing.discount_curve,
    pricing.dividend_yield,
    pricing.dividend_curve,
    pricing.borrow_or_carry_rate,
    pricing.calendar,
    pricing.day_count,
    pricing.pricing_model,
    pricing.pricing_engine,
    pricing.pricing_dynamics,
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
