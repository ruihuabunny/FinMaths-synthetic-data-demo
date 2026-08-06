-- Edit snapshot_id, market_date and underlying_id for the desired chain.
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
    quote.call_put,
    quote.strike,
    quote.expiry,
    quote.exercise_style,
    quote.settlement_type,
    quote.contract_multiplier,
    quote.bid,
    quote.mid,
    quote.ask,
    quote.settlement_price,
    quote.volume,
    quote.open_interest
FROM solver_visible.option_daily AS quote
JOIN parameters
  ON parameters.snapshot_id = quote.snapshot_id
 AND parameters.market_date = quote.date
 AND parameters.underlying_id = quote.underlying_id
ORDER BY quote.expiry, quote.call_put, quote.strike, quote.option_id;
