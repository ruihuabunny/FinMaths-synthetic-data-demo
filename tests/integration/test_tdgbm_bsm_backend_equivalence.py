from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb

from synthetic_derivatives.authoring.backends import AuthoringBackendRegistry
from synthetic_derivatives.authoring.config import GeneratorConfig, load_generator_config
from synthetic_derivatives.authoring.option_daily_generator import OptionDailyGenerator
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.authoring.underlying_daily_generator import (
    UnderlyingDailyGenerator,
)


class _PreRegistryDirectBackend:
    """Test reconstruction of the pipeline's former direct constructors."""

    model_family_id = "tdgbm_bsm"
    backend_id = "test-pre-registry-direct-constructors"

    def validate_config(self, config: GeneratorConfig) -> None:
        assert isinstance(config, GeneratorConfig)

    def create_underlying_generator(
        self, config: GeneratorConfig
    ) -> UnderlyingDailyGenerator:
        return UnderlyingDailyGenerator(config)

    def create_option_generator(
        self, config: GeneratorConfig
    ) -> OptionDailyGenerator:
        return OptionDailyGenerator(config)


def _stable_rows(database: Path) -> dict[str, list[tuple[Any, ...]]]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        def rows(relation: str, excluded: str | None = None) -> list[tuple[Any, ...]]:
            columns = [
                row[0]
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM {relation}"
                ).fetchall()
                if row[0] != excluded
            ]
            projection = ", ".join(
                f'CAST("{column}" AS VARCHAR)' for column in columns
            )
            return connection.execute(
                f"SELECT {projection} FROM {relation} ORDER BY ALL"
            ).fetchall()

        views = [
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'solver_visible'
                ORDER BY table_name
                """
            ).fetchall()
        ]
        result = {
            f"solver_visible.{view}": rows(f"solver_visible.{view}")
            for view in views
        }
        for table, run_column in (
            ("underlyings", "created_run_id"),
            ("underlying_dependence", "created_run_id"),
            ("option_chain_specs", "created_run_id"),
            ("option_contracts", "created_run_id"),
            ("underlying_daily", "generated_run_id"),
            ("option_daily", "generated_run_id"),
            ("pricing_metadata", "generated_run_id"),
        ):
            result[f"market.{table}"] = rows(f"market.{table}", run_column)
        return result
    finally:
        connection.close()


def _logical_digest(rows: dict[str, list[tuple[Any, ...]]]) -> str:
    return sha256(repr(rows).encode("utf-8")).hexdigest()


def test_backend_dispatch_preserves_current_16_rows_checksum_and_assignments(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    config = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json"
    )
    config = replace(config, business_days=2)
    default_database = tmp_path / "registry.duckdb"
    direct_database = tmp_path / "pre-registry-direct.duckdb"

    with AuthoringPipeline(default_database, config) as pipeline:
        default_result = pipeline.create_smoke_snapshot()
        assert pipeline.authoring_backend.backend_id == "tdgbm_bsm_authoring_v1"
    with AuthoringPipeline(
        direct_database,
        config,
        backend_registry=AuthoringBackendRegistry((_PreRegistryDirectBackend(),)),
    ) as pipeline:
        direct_result = pipeline.create_smoke_snapshot()

    default_rows = _stable_rows(default_database)
    direct_rows = _stable_rows(direct_database)
    assert default_rows == direct_rows
    assert _logical_digest(default_rows) == _logical_digest(direct_rows)
    assert default_result["table_stats"] == direct_result["table_stats"]
    assert default_result["summary"] | {"database": "ignored"} == (
        direct_result["summary"] | {"database": "ignored"}
    )
