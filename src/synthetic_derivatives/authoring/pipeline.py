"""Public authoring façade and explicit DuckDB transaction orchestration."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import QuantLib as ql

from synthetic_derivatives.authoring.backends import (
    DEFAULT_AUTHORING_BACKEND_REGISTRY,
    AuthoringBackendRegistry,
)
from synthetic_derivatives.authoring.compatibility import (
    SnapshotCompatibilityValidator,
)
from synthetic_derivatives.authoring.config_models import GeneratorConfig
from synthetic_derivatives.authoring.manifest import write_snapshot_manifest
from synthetic_derivatives.authoring.materialization import IncrementalMaterializer
from synthetic_derivatives.authoring.quality_gates import SnapshotQualityGates
from synthetic_derivatives.authoring.schema_migrations import initialize_schema
from synthetic_derivatives.authoring.snapshot_store import SnapshotStore
from synthetic_derivatives.model_families import TDGBM_BSM_MODEL_FAMILY_ID


class AuthoringPipeline(AbstractContextManager["AuthoringPipeline"]):
    """Own the public lifecycle, connection, and complete transaction order."""

    def __init__(
        self,
        database: str | Path,
        config: GeneratorConfig,
        *,
        model_family_id: str = TDGBM_BSM_MODEL_FAMILY_ID,
        backend_registry: AuthoringBackendRegistry | None = None,
    ):
        registry = backend_registry or DEFAULT_AUTHORING_BACKEND_REGISTRY
        backend = registry.resolve(model_family_id)
        backend.validate_config(config)
        self.model_family_id = model_family_id
        self.authoring_backend = backend
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.underlying_generator = backend.create_underlying_generator(config)
        self.option_generator = backend.create_option_generator(config)

        if self.database.exists():
            probe = duckdb.connect(str(self.database), read_only=True)
            snapshot_table_exists = probe.execute(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'metadata' AND table_name = 'snapshots'
                """
            ).fetchone()[0]
            frozen = bool(
                snapshot_table_exists
                and probe.execute(
                    "SELECT count(*) FROM metadata.snapshots WHERE status = 'FROZEN'"
                ).fetchone()[0]
            )
            if frozen:
                self.connection = probe
                self._bind_components()
                return
            probe.close()
        self.connection = duckdb.connect(str(self.database))
        initialize_schema(self.connection)
        self._bind_components()

    def _bind_components(self) -> None:
        self.store = SnapshotStore(
            self.connection,
            self.config,
            self.database,
            quantlib_version=ql.__version__,
        )
        self.compatibility = SnapshotCompatibilityValidator(
            self.connection,
            self.config,
            self.underlying_generator,
            self.option_generator,
            self.store,
        )
        self.materializer = IncrementalMaterializer(
            self.connection,
            self.config,
            self.underlying_generator,
            self.option_generator,
        )
        self.quality_gates = SnapshotQualityGates(
            self.connection,
            self.config,
            self.underlying_generator,
            self.compatibility,
            self.store,
        )

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.connection.close()

    def create_smoke_snapshot(self) -> dict[str, Any]:
        dates = self.underlying_generator.business_dates(
            self.config.start_date, self.config.business_days
        )
        return self.sync_range(dates[0], dates[-1], operation="CREATE_OR_SYNC")

    def append_business_days(self, count: int) -> dict[str, Any]:
        if count < 1:
            raise ValueError("append count must be positive")
        last_date = self.store.date_bounds().maximum
        if last_date is None:
            return self.create_smoke_snapshot()
        dates = self.underlying_generator.next_business_dates(last_date, count)
        return self.sync_range(dates[0], dates[-1], operation="APPEND_DATES")

    def sync_config(self) -> dict[str, Any]:
        bounds = self.store.date_bounds()
        if bounds.minimum is None or bounds.maximum is None:
            return self.create_smoke_snapshot()
        return self.sync_range(
            bounds.minimum, bounds.maximum, operation="SYNC_CONFIG"
        )

    def sync_range(
        self, start_date: date, end_date: date, operation: str = "SYNC_RANGE"
    ) -> dict[str, Any]:
        """Materialize one range while visibly retaining the full transaction law.

        Preflight occurs before the RUNNING audit row. Once that row is staged,
        any failure rolls back all market changes and is recorded separately as
        one FAILED run. The manifest is published only after a successful commit.
        """

        self._reject_legacy_authoring_iv_config()
        dates = self.underlying_generator.business_dates_between(start_date, end_date)
        if not dates:
            raise ValueError("requested range contains no business dates")
        run_id = str(uuid4())
        run_started = False

        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.compatibility.preflight_existing_snapshot()
            self.store.start_run(run_id, operation, dates[0], dates[-1])
            run_started = True

            stats = self.materializer.materialize(dates, run_id)
            self.quality_gates.validate_materialized_snapshot()
            changed = sum(
                table_stats["inserted"] for table_stats in stats.values()
            )
            status = "COMPLETED" if changed else "NOOP"
            if changed:
                self.store.record_revision(run_id)
            self.store.touch_snapshot()
            self.store.complete_run(run_id, status, stats)
            self.connection.execute("COMMIT")
        except Exception as error:
            self.connection.execute("ROLLBACK")
            if run_started:
                self.store.record_failed_run(
                    run_id, operation, dates[0], dates[-1], error
                )
            raise

        summary = self.summary()
        self._publish_committed_manifest(summary)
        return {
            "run_id": run_id,
            "status": status,
            "snapshot_id": self.config.snapshot_id,
            "table_stats": stats,
            "summary": summary,
        }

    def freeze(self) -> dict[str, Any]:
        self._reject_legacy_authoring_iv_config()
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.compatibility.preflight_existing_snapshot()
            self.quality_gates.validate_materialized_snapshot()
            self.store.freeze_snapshot()
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        summary = self.summary()
        self._publish_committed_manifest(summary)
        return summary

    def validate(self) -> dict[str, Any]:
        self.compatibility.assert_snapshot_identity()
        self.quality_gates.validate_materialized_snapshot()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return self.store.summary()

    def _reject_legacy_authoring_iv_config(self) -> None:
        q_pricing = self.config.q_pricing
        if q_pricing is not None and q_pricing.legacy_authoring_iv_solver_present:
            raise RuntimeError(
                "legacy authoring-IV config is read-only under the current "
                "pipeline; use config schema 1.6.0 with new config, generator, "
                "and snapshot identities"
            )

    def _publish_committed_manifest(self, summary: dict[str, Any]) -> None:
        try:
            write_snapshot_manifest(self.database, summary)
        except Exception as error:
            raise RuntimeError(
                "DuckDB transaction committed, but manifest publication failed"
            ) from error
