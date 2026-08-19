"""DuckDB reads and persistence for authoring snapshot lifecycle metadata."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import duckdb

from synthetic_derivatives.authoring.config_models import GeneratorConfig
from synthetic_derivatives.authoring.schema_ddl import SCHEMA_VERSION


class SnapshotIdentityRecord(NamedTuple):
    status: str
    generator_config_id: str
    generator_version: str
    seed: int
    rng: str


class SnapshotSummaryRecord(NamedTuple):
    status: str
    current_revision: int
    generator_config_id: str
    generator_version: str
    quantlib_version: str
    duckdb_version: str
    seed: int
    rng: str


class DateBounds(NamedTuple):
    minimum: date | None
    maximum: date | None
    count: int


class SnapshotStore:
    """Persist lifecycle state while leaving transaction order to the façade."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        config: GeneratorConfig,
        database: Path,
        *,
        quantlib_version: str,
    ) -> None:
        self.connection = connection
        self.config = config
        self.database = database
        self.quantlib_version = quantlib_version

    def relation_exists(self, qualified_name: str) -> bool:
        schema_name, relation_name = qualified_name.split(".", 1)
        return bool(
            self.connection.execute(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = ? AND table_name = ?
                """,
                [schema_name, relation_name],
            ).fetchone()[0]
        )

    def ensure_snapshot_is_editable(self) -> None:
        raw = self.connection.execute(
            """
            SELECT status, generator_config_id, generator_version, seed, rng
            FROM metadata.snapshots WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        if raw is None:
            self.connection.execute(
                """
                INSERT INTO metadata.snapshots (
                    snapshot_id, schema_version, status, generator_config_id,
                    generator_version, quantlib_version, duckdb_version, seed, rng
                ) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?)
                """,
                [
                    self.config.snapshot_id,
                    SCHEMA_VERSION,
                    self.config.generator_config_id,
                    self.config.generator_version,
                    self.quantlib_version,
                    duckdb.__version__,
                    self.config.seed,
                    self.config.rng,
                ],
            )
            return
        row = SnapshotIdentityRecord(*raw)
        if row.status == "FROZEN":
            raise RuntimeError(
                "snapshot is FROZEN; clone it under a new snapshot_id before editing"
            )
        if row.generator_config_id != self.config.generator_config_id:
            raise ValueError("generator_config_id cannot change within one snapshot")
        if row.generator_version != self.config.generator_version:
            raise ValueError("generator_version cannot change within one snapshot")
        if row.seed != self.config.seed or row.rng != self.config.rng:
            raise ValueError("snapshot seed and RNG identity cannot change")

    def assert_snapshot_identity(self) -> None:
        row = self.connection.execute(
            """
            SELECT generator_config_id, generator_version, seed, rng
            FROM metadata.snapshots WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        if row is None:
            raise ValueError("snapshot has not been created")
        expected = (
            self.config.generator_config_id,
            self.config.generator_version,
            self.config.seed,
            self.config.rng,
        )
        if tuple(row) != expected:
            raise ValueError("snapshot catalog identity conflicts with config")

    def date_bounds(self) -> DateBounds:
        row = self.connection.execute(
            """
            SELECT min(date), max(date), count(DISTINCT date)
            FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        return DateBounds(*row)

    def start_run(
        self,
        run_id: str,
        operation: str,
        start_date: date,
        end_date: date,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata.generation_runs (
                run_id, snapshot_id, operation, status,
                generator_config_id, generator_version,
                requested_start_date, requested_end_date
            ) VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, ?)
            """,
            [
                run_id,
                self.config.snapshot_id,
                operation,
                self.config.generator_config_id,
                self.config.generator_version,
                start_date,
                end_date,
            ],
        )

    def complete_run(
        self,
        run_id: str,
        status: str,
        stats: dict[str, dict[str, int]],
    ) -> None:
        self.connection.execute(
            """
            UPDATE metadata.generation_runs
            SET status = ?, table_stats = ?, completed_at = current_timestamp
            WHERE run_id = ?
            """,
            [status, json.dumps(stats, sort_keys=True), run_id],
        )

    def touch_snapshot(self) -> None:
        self.connection.execute(
            """
            UPDATE metadata.snapshots
            SET updated_at = current_timestamp
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        )

    def freeze_snapshot(self) -> None:
        self.connection.execute(
            """
            UPDATE metadata.snapshots
            SET status = 'FROZEN', frozen_at = current_timestamp,
                updated_at = current_timestamp
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        )

    def record_revision(self, run_id: str) -> None:
        counts = self.counts()
        current_revision = self.connection.execute(
            "SELECT current_revision FROM metadata.snapshots WHERE snapshot_id = ?",
            [self.config.snapshot_id],
        ).fetchone()[0]
        revision = current_revision + 1
        self.connection.execute(
            """
            INSERT INTO metadata.snapshot_revisions (
                snapshot_id, revision, run_id, underlying_count,
                option_contract_count, underlying_daily_count,
                option_daily_count, pricing_metadata_count,
                underlying_dependence_count, option_chain_spec_count,
                intraday_bridge_spec_count, underlying_volume_model_count,
                option_pricing_audit_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                self.config.snapshot_id,
                revision,
                run_id,
                counts["underlying_count"],
                counts["option_contract_count"],
                counts["underlying_daily_count"],
                counts["option_daily_count"],
                counts["pricing_metadata_count"],
                counts["underlying_dependence_count"],
                counts["option_chain_spec_count"],
                counts["intraday_bridge_spec_count"],
                counts["underlying_volume_model_count"],
                counts["option_pricing_audit_count"],
            ],
        )
        self.connection.execute(
            "UPDATE metadata.snapshots SET current_revision = ? WHERE snapshot_id = ?",
            [revision, self.config.snapshot_id],
        )

    def record_failed_run(
        self,
        run_id: str,
        operation: str,
        start_date: date,
        end_date: date,
        error: Exception,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata.generation_runs (
                run_id, snapshot_id, operation, status,
                generator_config_id, generator_version,
                requested_start_date, requested_end_date, error_message, completed_at
            ) VALUES (?, ?, ?, 'FAILED', ?, ?, ?, ?, ?, current_timestamp)
            """,
            [
                run_id,
                self.config.snapshot_id,
                operation,
                self.config.generator_config_id,
                self.config.generator_version,
                start_date,
                end_date,
                str(error),
            ],
        )

    def counts(self) -> dict[str, int]:
        tables = {
            "underlying_count": "market.underlyings",
            "option_contract_count": "market.option_contracts",
            "underlying_daily_count": "market.underlying_daily",
            "option_daily_count": "market.option_daily",
            "pricing_metadata_count": "market.pricing_metadata",
            "underlying_dependence_count": "market.underlying_dependence",
            "option_chain_spec_count": "market.option_chain_specs",
            "intraday_bridge_spec_count": "market.intraday_bridge_specs",
            "underlying_volume_model_count": "market.underlying_volume_models",
            "option_pricing_audit_count": "market.option_pricing_audit",
        }
        return {
            name: (
                self.connection.execute(
                    f"SELECT count(*) FROM {table} WHERE snapshot_id = ?",
                    [self.config.snapshot_id],
                ).fetchone()[0]
                if self.relation_exists(table)
                else 0
            )
            for name, table in tables.items()
        }

    def summary(self) -> dict[str, Any]:
        raw = self.connection.execute(
            """
            SELECT status, current_revision, generator_config_id,
                   generator_version, quantlib_version, duckdb_version,
                   seed, rng
            FROM metadata.snapshots
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        snapshot = SnapshotSummaryRecord(*raw) if raw else None
        date_bounds = self.date_bounds()
        return {
            "database": str(self.database),
            "snapshot_id": self.config.snapshot_id,
            "status": snapshot.status if snapshot else "NOT_CREATED",
            "revision": snapshot.current_revision if snapshot else 0,
            "generator_config_id": (
                snapshot.generator_config_id if snapshot else None
            ),
            "generator_version": snapshot.generator_version if snapshot else None,
            "quantlib_version": (
                snapshot.quantlib_version if snapshot else self.quantlib_version
            ),
            "duckdb_version": (
                snapshot.duckdb_version if snapshot else duckdb.__version__
            ),
            "seed": snapshot.seed if snapshot else self.config.seed,
            "rng": snapshot.rng if snapshot else self.config.rng,
            "date_min": (
                date_bounds.minimum.isoformat() if date_bounds.minimum else None
            ),
            "date_max": (
                date_bounds.maximum.isoformat() if date_bounds.maximum else None
            ),
            "business_date_count": date_bounds.count,
            **self.counts(),
        }
