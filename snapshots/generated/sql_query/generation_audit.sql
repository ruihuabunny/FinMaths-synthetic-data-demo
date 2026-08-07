-- Authoring/audit query: edit snapshot_id for another generated snapshot.
WITH parameters(snapshot_id) AS (
    VALUES ('DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v1')
)
SELECT
    snapshot.snapshot_id,
    snapshot.schema_version,
    snapshot.status AS snapshot_status,
    snapshot.current_revision,
    snapshot.quantlib_version,
    snapshot.duckdb_version,
    snapshot.seed,
    snapshot.rng,
    run.run_id,
    run.operation,
    run.status,
    run.requested_start_date,
    run.requested_end_date,
    run.generator_config_id,
    run.generator_version,
    run.table_stats,
    run.error_message,
    revision.revision,
    revision.underlying_count,
    revision.option_contract_count,
    revision.underlying_daily_count,
    revision.option_daily_count,
    revision.pricing_metadata_count,
    revision.underlying_dependence_count,
    revision.option_chain_spec_count,
    revision.option_pricing_audit_count,
    CAST(run.started_at AS VARCHAR) AS started_at,
    CAST(run.completed_at AS VARCHAR) AS completed_at
FROM metadata.generation_runs AS run
JOIN metadata.snapshots AS snapshot
  ON snapshot.snapshot_id = run.snapshot_id
LEFT JOIN metadata.snapshot_revisions AS revision
  ON revision.snapshot_id = run.snapshot_id
 AND revision.run_id = run.run_id
JOIN parameters
  ON parameters.snapshot_id = run.snapshot_id
ORDER BY run.started_at, run.run_id;
