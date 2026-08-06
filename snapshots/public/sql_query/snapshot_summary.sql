-- Edit the snapshot_id here when querying another public snapshot.
WITH parameters(snapshot_id) AS (
    VALUES ('DERIVATIVES-METALS-LIQUID-BSM-v1')
),
underlying_stats AS (
    SELECT
        snapshot_id,
        min(date) AS date_min,
        max(date) AS date_max,
        count(DISTINCT date) AS business_date_count,
        count(*) AS underlying_daily_count
    FROM market.underlying_daily
    GROUP BY snapshot_id
),
option_stats AS (
    SELECT
        snapshot_id,
        count(DISTINCT date) AS option_business_date_count,
        count(DISTINCT option_id) AS quoted_option_count,
        count(*) AS option_daily_count
    FROM market.option_daily
    GROUP BY snapshot_id
),
metadata_stats AS (
    SELECT snapshot_id, count(*) AS pricing_metadata_count
    FROM market.pricing_metadata
    GROUP BY snapshot_id
)
SELECT
    snapshot.snapshot_id,
    snapshot.schema_version,
    snapshot.status,
    snapshot.current_revision,
    snapshot.generator_config_id,
    snapshot.generator_version,
    underlying_stats.date_min,
    underlying_stats.date_max,
    underlying_stats.business_date_count,
    (
        SELECT count(*)
        FROM market.underlyings
        WHERE snapshot_id = snapshot.snapshot_id
    ) AS underlying_count,
    (
        SELECT count(*)
        FROM market.option_contracts
        WHERE snapshot_id = snapshot.snapshot_id
    ) AS option_contract_count,
    (
        SELECT count(*)
        FROM market.underlying_dependence
        WHERE snapshot_id = snapshot.snapshot_id
    ) AS underlying_dependence_count,
    (
        SELECT count(*)
        FROM market.option_chain_specs
        WHERE snapshot_id = snapshot.snapshot_id
    ) AS option_chain_spec_count,
    underlying_stats.underlying_daily_count,
    option_stats.option_business_date_count,
    option_stats.quoted_option_count,
    option_stats.option_daily_count,
    metadata_stats.pricing_metadata_count
FROM metadata.snapshots AS snapshot
JOIN parameters
  ON parameters.snapshot_id = snapshot.snapshot_id
JOIN underlying_stats
  ON underlying_stats.snapshot_id = snapshot.snapshot_id
JOIN option_stats
  ON option_stats.snapshot_id = snapshot.snapshot_id
JOIN metadata_stats
  ON metadata_stats.snapshot_id = snapshot.snapshot_id
ORDER BY snapshot.snapshot_id;
