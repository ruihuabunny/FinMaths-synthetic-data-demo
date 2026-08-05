-- Authoring/audit query: edit snapshot_id for another public snapshot.
WITH parameters(snapshot_id) AS (
    VALUES ('DERIVATIVES-QUANTLIB-SMOKE-v1')
)
SELECT
    run.run_id,
    run.operation,
    run.status,
    run.requested_start_date,
    run.requested_end_date,
    run.config_sha256,
    run.table_stats,
    run.error_message,
    revision.revision,
    revision.content_sha256,
    CAST(run.started_at AS VARCHAR) AS started_at,
    CAST(run.completed_at AS VARCHAR) AS completed_at
FROM metadata.generation_runs AS run
LEFT JOIN metadata.snapshot_revisions AS revision
  ON revision.snapshot_id = run.snapshot_id
 AND revision.run_id = run.run_id
JOIN parameters
  ON parameters.snapshot_id = run.snapshot_id
ORDER BY run.started_at, run.run_id;
