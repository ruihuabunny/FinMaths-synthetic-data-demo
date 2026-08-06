-- Edit snapshot_id, market_date and underlying_id for the desired pricing slice.
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-LIQUID-BSM-v1',
        DATE '2026-08-03',
        'SYNTH-METAL-GOLD'
    )
)
SELECT
    quote.date,
    quote.underlying_id,
    quote.option_id,
    underlying.spot_close,
    quote.call_put,
    quote.strike,
    quote.expiry,
    quote.mid,
    CAST(
        pricing.valuation_timestamp AT TIME ZONE 'UTC' AS VARCHAR
    ) AS valuation_timestamp_utc,
    pricing.risk_free_rate,
    pricing.dividend_yield,
    pricing.borrow_or_carry_rate,
    pricing.day_count,
    pricing.pricing_model,
    pricing.pricing_engine
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
