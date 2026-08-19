from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def _small_config(smoke_config_path: Path):
    config = load_generator_config(smoke_config_path)
    return replace(
        config,
        business_days=1,
        underlyings=config.underlyings[:1],
        option_templates=config.option_templates[:1],
    )


def test_started_run_failure_rolls_back_market_rows_and_records_failed(
    tmp_path: Path, smoke_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "started-run-failure.duckdb"
    with AuthoringPipeline(database, _small_config(smoke_config_path)) as pipeline:
        def fail_after_materialization() -> None:
            raise RuntimeError("injected post-write gate failure")

        monkeypatch.setattr(
            pipeline.quality_gates,
            "validate_materialized_snapshot",
            fail_after_materialization,
        )
        with pytest.raises(RuntimeError, match="injected post-write"):
            pipeline.create_smoke_snapshot()

        assert pipeline.connection.execute(
            "SELECT count(*) FROM market.underlying_daily"
        ).fetchone()[0] == 0
        assert pipeline.connection.execute(
            "SELECT count(*) FROM market.option_daily"
        ).fetchone()[0] == 0
        assert pipeline.connection.execute(
            "SELECT status, count(*) FROM metadata.generation_runs GROUP BY status"
        ).fetchall() == [("FAILED", 1)]


def test_immutable_preflight_failure_does_not_create_an_audit_run(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "preflight-failure.duckdb"
    config = _small_config(smoke_config_path)
    with AuthoringPipeline(database, config) as pipeline:
        pipeline.create_smoke_snapshot()
        run_count = pipeline.connection.execute(
            "SELECT count(*) FROM metadata.generation_runs"
        ).fetchone()[0]

    incompatible = replace(config, generator_config_id="different-config-id")
    with AuthoringPipeline(database, incompatible) as pipeline:
        with pytest.raises(ValueError, match="generator_config_id cannot change"):
            pipeline.create_smoke_snapshot()
        assert pipeline.connection.execute(
            "SELECT count(*) FROM metadata.generation_runs"
        ).fetchone()[0] == run_count


def test_manifest_failure_reports_that_database_commit_succeeded(
    tmp_path: Path,
    smoke_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "manifest-failure.duckdb"

    def fail_manifest(*_args, **_kwargs) -> None:
        raise OSError("injected manifest publication failure")

    monkeypatch.setattr(
        "synthetic_derivatives.authoring.pipeline.write_snapshot_manifest",
        fail_manifest,
    )
    with AuthoringPipeline(database, _small_config(smoke_config_path)) as pipeline:
        with pytest.raises(RuntimeError, match="transaction committed"):
            pipeline.create_smoke_snapshot()
        assert pipeline.connection.execute(
            "SELECT status FROM metadata.generation_runs"
        ).fetchone()[0] == "COMPLETED"
        assert pipeline.connection.execute(
            "SELECT current_revision FROM metadata.snapshots"
        ).fetchone()[0] == 1
        assert pipeline.connection.execute(
            "SELECT count(*) FROM market.underlying_daily"
        ).fetchone()[0] == 1
