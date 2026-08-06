-- Edit snapshot_id and underlying_id for the desired time series.
WITH parameters(snapshot_id, underlying_id) AS (
    VALUES ('DERIVATIVES-METALS-LIQUID-BSM-v1', 'SYNTH-METAL-GOLD')
)
SELECT
    daily.snapshot_id,
    daily.date,
    daily.underlying_id,
    daily.spot_open,
    daily.spot_high,
    daily.spot_low,
    daily.spot_close,
    daily.adjusted_close,
    daily.volume,
    daily.dividend,
    daily.corporate_action
FROM solver_visible.underlying_daily AS daily
JOIN parameters
  ON parameters.snapshot_id = daily.snapshot_id
 AND parameters.underlying_id = daily.underlying_id
ORDER BY daily.date, daily.underlying_id;
