-- Edit snapshot_id, market_date and underlying_id for the desired slice.
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
    quote.strike - underlying.spot_close AS strike_minus_spot,
    quote.strike / underlying.spot_close AS spot_moneyness,
    ln(CAST(quote.strike / underlying.spot_close AS DOUBLE))
        AS log_spot_moneyness
FROM solver_visible.option_daily AS quote
JOIN solver_visible.underlying_daily AS underlying
  ON underlying.snapshot_id = quote.snapshot_id
 AND underlying.date = quote.date
 AND underlying.underlying_id = quote.underlying_id
JOIN parameters
  ON parameters.snapshot_id = quote.snapshot_id
 AND parameters.market_date = quote.date
 AND parameters.underlying_id = quote.underlying_id
ORDER BY quote.expiry, quote.call_put, quote.strike, quote.option_id;
