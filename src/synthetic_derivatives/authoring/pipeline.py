"""Incremental, append-safe orchestration for one DuckDB market snapshot.

The pipeline is the write boundary: it validates immutable identities, orders
P-path generation before Q-pricing, commits market rows and revision metadata
atomically, and publishes the adjacent manifest only after commit.  Financial
transition laws remain in the two generator modules.
"""

from __future__ import annotations

import json
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
from synthetic_derivatives.authoring.config import GeneratorConfig
from synthetic_derivatives.authoring.schema import (
    SCHEMA_VERSION,
    TABLE_SPECS,
    assert_no_private_metadata_leakage,
    initialize_schema,
    merge_rows,
)
from synthetic_derivatives.model_families import TDGBM_BSM_MODEL_FAMILY_ID


class AuthoringPipeline(AbstractContextManager["AuthoringPipeline"]):
    """Coordinate deterministic generators and transactional DuckDB writes.

    The pipeline owns ordering, idempotent MERGE behavior, immutable-snapshot
    checks, revisions and manifest publication.  Financial transitions and
    pricing remain separated in ``UnderlyingDailyGenerator`` and
    ``OptionDailyGenerator`` respectively.
    """

    def __init__(
        self,
        database: str | Path,
        config: GeneratorConfig,
        *,
        model_family_id: str = TDGBM_BSM_MODEL_FAMILY_ID,
        backend_registry: AuthoringBackendRegistry | None = None,
    ):
        """Open ``database`` and initialize/migrate it to the current schema."""

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
                return
            probe.close()
        self.connection = duckdb.connect(str(self.database))
        initialize_schema(self.connection)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Close the owned DuckDB connection on normal or exceptional exit."""

        self.connection.close()

    def create_smoke_snapshot(self) -> dict[str, Any]:
        """Generate the complete business-date horizon declared by config.

        The historical command name is retained for CLI compatibility; the
        method is not limited to the original five-day smoke profile.
        """

        dates = self.underlying_generator.business_dates(
            self.config.start_date, self.config.business_days
        )
        return self.sync_range(dates[0], dates[-1], operation="CREATE_OR_SYNC")

    def append_business_days(self, count: int) -> dict[str, Any]:
        """Append exactly ``count`` business dates after the persisted maximum."""

        if count < 1:
            raise ValueError("append count must be positive")
        last_date = self.connection.execute(
            """
            SELECT max(date)
            FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()[0]
        if last_date is None:
            return self.create_smoke_snapshot()
        dates = self.underlying_generator.next_business_dates(last_date, count)
        return self.sync_range(dates[0], dates[-1], operation="APPEND_DATES")

    def sync_config(self) -> dict[str, Any]:
        """Reconcile immutable config rows over the existing snapshot date span.

        Legacy configs may add supported entities.  Config 1.2+ dependence and
        config 1.3+ chain contracts are checked as immutable wholes before any
        row is inserted.
        """

        bounds = self.connection.execute(
            """
            SELECT min(date), max(date)
            FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        if bounds[0] is None:
            return self.create_smoke_snapshot()
        return self.sync_range(bounds[0], bounds[1], operation="SYNC_CONFIG")

    def sync_range(
        self, start_date: date, end_date: date, operation: str = "SYNC_RANGE"
    ) -> dict[str, Any]:
        """Idempotently author one inclusive business-date range.

        All market rows, quality gates, run completion and revision updates are
        one DuckDB transaction.  On failure that transaction is rolled back;
        a separate FAILED audit row is then recorded without partial market data.
        """

        self._reject_legacy_authoring_iv_config()
        dates = self.underlying_generator.business_dates_between(start_date, end_date)
        if not dates:
            raise ValueError("requested range contains no business dates")
        run_id = str(uuid4())
        run_started = False
        self.connection.execute("BEGIN TRANSACTION")
        try:
            # Run immutable-contract checks before creating any market rows.
            self._ensure_snapshot_is_editable()
            self._assert_underlying_dependence_is_compatible()
            self._assert_observation_specs_are_compatible()
            self._assert_option_chain_is_compatible()
            self._assert_option_contracts_are_compatible()
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
                    dates[0],
                    dates[-1],
                ],
            )
            run_started = True

            stats = self._generate_incremental_rows(dates, run_id)
            self._assert_quality_gates()
            # A successful idempotent rerun is a NOOP and does not create a
            # revision merely because an audit run occurred.
            changed = sum(table_stats["inserted"] for table_stats in stats.values())
            status = "COMPLETED" if changed else "NOOP"
            if changed:
                self._record_revision(run_id)
            self.connection.execute(
                """
                UPDATE metadata.snapshots
                SET updated_at = current_timestamp
                WHERE snapshot_id = ?
                """,
                [self.config.snapshot_id],
            )
            self.connection.execute(
                """
                UPDATE metadata.generation_runs
                SET status = ?, table_stats = ?, completed_at = current_timestamp
                WHERE run_id = ?
                """,
                [status, json.dumps(stats, sort_keys=True), run_id],
            )
            self.connection.execute("COMMIT")
        except Exception as error:
            self.connection.execute("ROLLBACK")
            # Failure lineage survives a rolled-back data batch only after the
            # run itself began. Immutable-contract preflight failures, including
            # attempts to edit a frozen snapshot, must not mutate that snapshot.
            if run_started:
                self._record_failed_run(
                    run_id, operation, dates[0], dates[-1], error
                )
            raise
        summary = self.summary()
        self._write_manifest(summary)
        return {
            "run_id": run_id,
            "status": status,
            "snapshot_id": self.config.snapshot_id,
            "table_stats": stats,
            "summary": summary,
        }

    def freeze(self) -> dict[str, Any]:
        """Run final gates and irreversibly mark the logical snapshot FROZEN."""

        self._reject_legacy_authoring_iv_config()
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._ensure_snapshot_is_editable()
            self._assert_quality_gates()
            self.connection.execute(
                """
                UPDATE metadata.snapshots
                SET status = 'FROZEN', frozen_at = current_timestamp,
                    updated_at = current_timestamp
                WHERE snapshot_id = ?
                """,
                [self.config.snapshot_id],
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        summary = self.summary()
        self._write_manifest(summary)
        return summary

    def validate(self) -> dict[str, Any]:
        """Run all read-only contract, replay, completeness and leakage gates."""

        self._assert_snapshot_identity()
        self._assert_quality_gates()
        return self.summary()

    def _reject_legacy_authoring_iv_config(self) -> None:
        """Keep legacy IV-authored snapshot identities read-only.

        Config parsing accepts 1.5 so maintainers can inspect an existing
        database with its historical contract.  A mutating operation cannot
        use it: current generation emits quotes but no IV audit rows, which
        would mix two different output contracts under one snapshot ID.
        """

        q_pricing = self.config.q_pricing
        if (
            q_pricing is not None
            and q_pricing.legacy_authoring_iv_solver_present
        ):
            raise RuntimeError(
                "legacy authoring-IV config is read-only under the current "
                "pipeline; use config schema 1.6.0 with new config, generator, "
                "and snapshot identities"
            )

    def summary(self) -> dict[str, Any]:
        """Return manifest-ready identity, date bounds and logical row counts."""

        snapshot = self.connection.execute(
            """
            SELECT status, current_revision, generator_config_id,
                   generator_version, quantlib_version, duckdb_version,
                   seed, rng
            FROM metadata.snapshots
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        counts = self._counts()
        date_bounds = self.connection.execute(
            """
            SELECT min(date), max(date), count(DISTINCT date)
            FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        return {
            "database": str(self.database),
            "snapshot_id": self.config.snapshot_id,
            "status": snapshot[0] if snapshot else "NOT_CREATED",
            "revision": snapshot[1] if snapshot else 0,
            "generator_config_id": snapshot[2] if snapshot else None,
            "generator_version": snapshot[3] if snapshot else None,
            "quantlib_version": snapshot[4] if snapshot else ql.__version__,
            "duckdb_version": snapshot[5] if snapshot else duckdb.__version__,
            "seed": snapshot[6] if snapshot else self.config.seed,
            "rng": snapshot[7] if snapshot else self.config.rng,
            "date_min": date_bounds[0].isoformat() if date_bounds[0] else None,
            "date_max": date_bounds[1].isoformat() if date_bounds[1] else None,
            "business_date_count": date_bounds[2],
            **counts,
        }

    def _ensure_snapshot_is_editable(self) -> None:
        """Create a DRAFT catalog row or validate immutable snapshot identity."""

        row = self.connection.execute(
            """
            SELECT status, generator_config_id, generator_version, seed, rng
            FROM metadata.snapshots WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()
        if row is None:
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
                    ql.__version__,
                    duckdb.__version__,
                    self.config.seed,
                    self.config.rng,
                ],
            )
            return
        if row[0] == "FROZEN":
            raise RuntimeError(
                "snapshot is FROZEN; clone it under a new snapshot_id before editing"
            )
        if row[1] != self.config.generator_config_id:
            raise ValueError("generator_config_id cannot change within one snapshot")
        if row[2] != self.config.generator_version:
            raise ValueError("generator_version cannot change within one snapshot")
        if row[3] != self.config.seed or row[4] != self.config.rng:
            raise ValueError("snapshot seed and RNG identity cannot change")

    def _assert_snapshot_identity(self) -> None:
        """Validate the immutable catalog identity without requiring DRAFT."""

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

    def _generate_incremental_rows(
        self, dates: list[date], run_id: str
    ) -> dict[str, dict[str, int]]:
        """Insert missing business-key rows without rewriting historical state.

        Underlying closes are generated chronologically from the last persisted,
        quantized close.  For the current GBM that value and the interval dates
        form the complete Markov restart state.  Date-partitioned factor and
        idiosyncratic streams make this append path identical to a one-shot run.

        Chain specs and contract masters are reconstructed from immutable config
        on every run, then merged by stable keys.  Daily option rows are built
        only after the underlying slice exists and receive realized spots, not
        the underlying dependence matrix.
        """

        underlying_dependence_rows = (
            self.underlying_generator.underlying_dependence_rows(run_id)
        )
        intraday_bridge_spec_rows = (
            self.underlying_generator.intraday_bridge_spec_rows(run_id)
        )
        underlying_volume_model_rows = (
            self.underlying_generator.underlying_volume_model_rows(run_id)
        )
        option_chain_spec_row = self.option_generator.option_chain_spec_row(run_id)
        # Private provenance and immutable masters are always reconstructed
        # from config. MERGE inserts them once and compatibility checks protect
        # existing economic definitions from silent updates.
        underlying_master_rows = [
            self.underlying_generator.underlying_master_row(underlying, run_id)
            for underlying in self.config.underlyings
        ]
        option_contract_rows = [
            self.option_generator.option_contract_row(underlying, template, run_id)
            for underlying in self.config.underlyings
            for template in self.config.option_templates
        ]
        stats = {
            "intraday_bridge_specs": merge_rows(
                self.connection,
                TABLE_SPECS["intraday_bridge_specs"],
                intraday_bridge_spec_rows,
            ),
            "underlying_volume_models": merge_rows(
                self.connection,
                TABLE_SPECS["underlying_volume_models"],
                underlying_volume_model_rows,
            ),
            "underlying_dependence": merge_rows(
                self.connection,
                TABLE_SPECS["underlying_dependence"],
                underlying_dependence_rows,
            ),
            "option_chain_specs": merge_rows(
                self.connection,
                TABLE_SPECS["option_chain_specs"],
                [option_chain_spec_row]
                if option_chain_spec_row is not None
                else [],
            ),
            "underlyings": merge_rows(
                self.connection, TABLE_SPECS["underlyings"], underlying_master_rows
            ),
            "option_contracts": merge_rows(
                self.connection, TABLE_SPECS["option_contracts"], option_contract_rows
            ),
        }

        underlying_daily_rows: list[tuple[Any, ...]] = []
        requested_dates = set(dates)
        previous_date_by_key: dict[tuple[date, str], date | None] = {}
        # Each underlying path advances chronologically from its last persisted,
        # quantized close. Filling a gap before a later close would invalidate
        # all subsequent Markov transitions and is therefore rejected.
        for underlying in self.config.underlyings:
            existing = {
                row[0]: row[1]
                for row in self.connection.execute(
                    """
                    SELECT date, spot_close
                    FROM market.underlying_daily
                    WHERE snapshot_id = ? AND underlying_id = ?
                    ORDER BY date
                    """,
                    [self.config.snapshot_id, underlying.underlying_id],
                ).fetchall()
            }
            for market_date in dates:
                if market_date in existing:
                    continue
                later_dates = [value for value in existing if value > market_date]
                if later_dates:
                    raise ValueError(
                        f"cannot fill a historical path gap for {underlying.underlying_id}; "
                        "create a new snapshot instead"
                    )
                prior_dates = [value for value in existing if value < market_date]
                if prior_dates:
                    previous_date = max(prior_dates)
                    previous_close = existing[previous_date]
                else:
                    if market_date != self.config.start_date:
                        raise ValueError(
                            f"new underlying {underlying.underlying_id} must be generated "
                            f"from configured start_date {self.config.start_date}"
                        )
                    previous_date = None
                    previous_close = underlying.initial_spot
                if previous_date is None:
                    row = self.underlying_generator.initial_underlying_daily_row(
                        underlying, run_id
                    )
                else:
                    row = self.underlying_generator.underlying_daily_row(
                        underlying, market_date, previous_date, previous_close, run_id
                    )
                underlying_daily_rows.append(row)
                # Column 6 is spot_close in the canonical underlying row spec.
                existing[market_date] = row[6]

            prior_path_date: date | None = None
            for path_date in sorted(existing):
                if path_date in requested_dates:
                    previous_date_by_key[(path_date, underlying.underlying_id)] = (
                        prior_path_date
                        if prior_path_date is not None
                        else None
                    )
                prior_path_date = path_date

        stats["underlying_daily"] = merge_rows(
            self.connection, TABLE_SPECS["underlying_daily"], underlying_daily_rows
        )

        existing_metadata = {
            (row[0], row[1])
            for row in self.connection.execute(
                """
                SELECT valuation_date, underlying_id
                FROM market.pricing_metadata
                WHERE snapshot_id = ? AND valuation_date BETWEEN ? AND ?
                """,
                [self.config.snapshot_id, dates[0], dates[-1]],
            ).fetchall()
        }
        # Metadata must use the actual predecessor date, including multi-day
        # weekend intervals, so deterministic drift/variance reductions replay.
        metadata_rows = [
            self.underlying_generator.pricing_metadata_row(
                underlying,
                market_date,
                previous_date_by_key[(market_date, underlying.underlying_id)],
                run_id,
            )
            for underlying in self.config.underlyings
            for market_date in dates
            if (market_date, underlying.underlying_id) not in existing_metadata
        ]
        stats["pricing_metadata"] = merge_rows(
            self.connection, TABLE_SPECS["pricing_metadata"], metadata_rows
        )

        spot_rows = self.connection.execute(
            """
            SELECT date, underlying_id, spot_close
            FROM market.underlying_daily
            WHERE snapshot_id = ? AND date BETWEEN ? AND ?
            """,
            [self.config.snapshot_id, dates[0], dates[-1]],
        ).fetchall()
        spot_by_key = {(row[0], row[1]): row[2] for row in spot_rows}
        existing_option_daily = {
            (row[0], row[1])
            for row in self.connection.execute(
                """
                SELECT date, option_id
                FROM market.option_daily
                WHERE snapshot_id = ? AND date BETWEEN ? AND ?
                """,
                [self.config.snapshot_id, dates[0], dates[-1]],
            ).fetchall()
        }
        underlyings_by_id = {
            item.underlying_id: item for item in self.config.underlyings
        }
        option_daily_rows: list[tuple[Any, ...]] = []
        # Options are priced only after the realized spot slice is materialized.
        # The option generator receives no Lambda/D/R object; dependence reaches
        # it only through spot_by_key.
        for contract in option_contract_rows:
            underlying_id = contract[2]
            for market_date in dates:
                quote_key = (market_date, contract[1])
                quote_exists = quote_key in existing_option_daily
                if quote_exists:
                    continue
                spot = spot_by_key[(market_date, underlying_id)]
                result = self.option_generator.option_daily_result(
                    underlyings_by_id[underlying_id],
                    contract,
                    market_date,
                    spot,
                    run_id,
                )
                if result is not None:
                    option_daily_rows.append(result.quote_row)
        stats["option_daily"] = merge_rows(
            self.connection, TABLE_SPECS["option_daily"], option_daily_rows
        )
        return stats

    def _assert_underlying_dependence_is_compatible(self) -> None:
        """Protect the dependence contract and its generated path as one unit.

        Retrofitting, removing, or changing Lambda/driver order after any path
        exists would combine transitions from different joint laws.  Such a
        change must therefore use a new snapshot ID.  Legacy configs remain
        valid only when no dependence row is present.
        """

        columns = TABLE_SPECS["underlying_dependence"].columns[:-1]
        existing_rows = self.connection.execute(
            f"""
            SELECT {', '.join(columns)}
            FROM market.underlying_dependence
            WHERE snapshot_id = ?
            ORDER BY CASE measure WHEN 'P' THEN 0 ELSE 1 END,
                     dependence_spec_id
            """,
            [self.config.snapshot_id],
        ).fetchall()
        expected_rows = self.underlying_generator.underlying_dependence_rows(
            "compatibility-check"
        )
        if not expected_rows:
            if existing_rows:
                raise ValueError(
                    "underlying dependence cannot be removed within one snapshot; "
                    "create a new snapshot instead"
                )
            return

        expected_logical_rows = [tuple(row[:-1]) for row in expected_rows]
        if existing_rows:
            if [tuple(row) for row in existing_rows] != expected_logical_rows:
                raise ValueError(
                    "underlying dependence cannot change within one snapshot; "
                    "create a new snapshot instead"
                )
            return

        existing_path_count = self.connection.execute(
            """
            SELECT count(*) FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()[0]
        if existing_path_count:
            raise ValueError(
                "underlying dependence cannot be added to an existing path; "
                "create a new snapshot instead"
            )

    def _relation_exists(self, qualified_name: str) -> bool:
        """Return whether a table/view exists, including in legacy read-only DBs."""

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

    def _assert_observation_specs_are_compatible(self) -> None:
        """Protect bridge and volume laws from removal, retrofit or mutation."""

        expected_by_table = {
            "intraday_bridge_specs": (
                self.underlying_generator.intraday_bridge_spec_rows(
                    "compatibility-check"
                ),
                "bridge_spec_id",
                "intraday bridge contract",
            ),
            "underlying_volume_models": (
                self.underlying_generator.underlying_volume_model_rows(
                    "compatibility-check"
                ),
                "volume_spec_id, underlying_id",
                "underlying volume model contract",
            ),
        }
        existing_path_count = self.connection.execute(
            """
            SELECT count(*) FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()[0]
        for table_key, (expected_rows, ordering, label) in expected_by_table.items():
            specification = TABLE_SPECS[table_key]
            if not self._relation_exists(specification.name):
                if expected_rows:
                    raise RuntimeError(
                        f"{label} requires DuckDB schema {SCHEMA_VERSION}"
                    )
                continue
            logical_columns = specification.columns[:-1]
            existing_rows = self.connection.execute(
                f"""
                SELECT {', '.join(logical_columns)}
                FROM {specification.name}
                WHERE snapshot_id = ?
                ORDER BY {ordering}
                """,
                [self.config.snapshot_id],
            ).fetchall()
            expected_logical_rows = sorted(
                (tuple(row[:-1]) for row in expected_rows),
                key=lambda row: tuple(str(value) for value in row),
            )
            actual_logical_rows = sorted(
                (tuple(row) for row in existing_rows),
                key=lambda row: tuple(str(value) for value in row),
            )
            if not expected_logical_rows:
                if actual_logical_rows:
                    raise ValueError(
                        f"{label} cannot be removed within one snapshot; "
                        "create a new snapshot instead"
                    )
                continue
            if actual_logical_rows:
                if actual_logical_rows != expected_logical_rows:
                    raise ValueError(
                        f"{label} conflicts with config and cannot change "
                        "within one snapshot"
                    )
                continue
            if existing_path_count:
                raise ValueError(
                    f"{label} cannot be added to an existing path; "
                    "create a new snapshot instead"
                )

    def _assert_option_chain_is_compatible(self) -> None:
        """Keep listing, grid, strike-rounding and roll rules immutable.

        The private spec is compared without its run-lineage column.  A chain
        may be inserted only before any contract exists; retrofitting rules onto
        an existing master would make its original listing state unknowable.
        """

        columns = TABLE_SPECS["option_chain_specs"].columns[:-1]
        existing_rows = self.connection.execute(
            f"""
            SELECT {', '.join(columns)}
            FROM market.option_chain_specs
            WHERE snapshot_id = ?
            ORDER BY chain_id
            """,
            [self.config.snapshot_id],
        ).fetchall()
        expected_row = self.option_generator.option_chain_spec_row(
            "compatibility-check"
        )
        if expected_row is None:
            if existing_rows:
                raise ValueError(
                    "option_chain cannot be removed within one snapshot; "
                    "create a new snapshot instead"
                )
            return

        expected_logical_row = tuple(expected_row[:-1])
        if existing_rows:
            if len(existing_rows) != 1 or tuple(existing_rows[0]) != expected_logical_row:
                raise ValueError(
                    "option_chain cannot change within one snapshot; "
                    "create a new snapshot instead"
                )
            return

        existing_contract_count = self.connection.execute(
            """
            SELECT count(*) FROM market.option_contracts
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()[0]
        if existing_contract_count:
            raise ValueError(
                "option_chain cannot be added to existing contracts; "
                "create a new snapshot instead"
            )

    def _assert_option_contracts_are_compatible(self) -> None:
        """Reject any rewrite of a listed chain contract under a stable ID.

        Comparing the full expected set also catches changes that leave the
        chain spec text untouched, such as editing an underlying's listing spot.
        The run-lineage field is excluded because it is not economic identity.
        """

        if self.config.option_chain is None:
            return
        columns = TABLE_SPECS["option_contracts"].columns[:-1]
        existing_rows = self.connection.execute(
            f"""
            SELECT {', '.join(columns)}
            FROM market.option_contracts
            WHERE snapshot_id = ?
            ORDER BY option_id
            """,
            [self.config.snapshot_id],
        ).fetchall()
        if not existing_rows:
            return
        expected_rows = sorted(
            (
                tuple(
                    self.option_generator.option_contract_row(
                        underlying, template, "compatibility-check"
                    )[:-1]
                )
                for underlying in self.config.underlyings
                for template in self.config.option_templates
            ),
            key=lambda row: row[1],
        )
        if [tuple(row) for row in existing_rows] != expected_rows:
            raise ValueError(
                "listed option-chain contracts cannot change within one snapshot; "
                "create a new snapshot instead"
            )

    def _assert_quality_gates(self) -> None:
        """Validate cross-table completeness and immutable contract identity.

        These are implementation-integrity gates, not a replacement for the
        no-arbitrage conditions supplied by the selected pricing model and
        numeraire.  In particular, no check treats derivative contracts as
        correlation drivers.
        """

        self._assert_underlying_dependence_is_compatible()
        self._assert_observation_specs_are_compatible()
        self._assert_common_q_context()
        self._assert_option_chain_is_compatible()
        self._assert_option_contracts_are_compatible()
        checks = {
            "option_chain_contract_without_spec": """
                SELECT count(*) FROM market.option_contracts contract
                LEFT JOIN market.option_chain_specs chain
                  ON chain.snapshot_id = contract.snapshot_id
                 AND chain.chain_id = contract.chain_id
                WHERE contract.snapshot_id = ?
                  AND contract.chain_id IS NOT NULL
                  AND chain.chain_id IS NULL
            """,
            "incomplete_option_chain_contract": """
                SELECT count(*) FROM market.option_contracts contract
                JOIN market.option_chain_specs chain
                  ON chain.snapshot_id = contract.snapshot_id
                 AND chain.chain_id = contract.chain_id
                WHERE contract.snapshot_id = ?
                  AND (contract.listing_date IS NULL
                       OR contract.listing_spot IS NULL
                       OR (chain.grid_type = 'moneyness'
                           AND contract.strike_moneyness IS NULL)
                       OR (chain.grid_type = 'strike'
                           AND contract.strike_moneyness IS NOT NULL))
            """,
            "orphan_option_contract": """
                SELECT count(*) FROM market.option_contracts option_contract
                LEFT JOIN market.underlyings underlying
                  ON underlying.snapshot_id = option_contract.snapshot_id
                 AND underlying.underlying_id = option_contract.underlying_id
                WHERE option_contract.snapshot_id = ? AND underlying.underlying_id IS NULL
            """,
            "orphan_underlying_daily": """
                SELECT count(*) FROM market.underlying_daily daily
                LEFT JOIN market.underlyings underlying
                  ON underlying.snapshot_id = daily.snapshot_id
                 AND underlying.underlying_id = daily.underlying_id
                WHERE daily.snapshot_id = ? AND underlying.underlying_id IS NULL
            """,
            "orphan_underlying_volume_model": """
                SELECT count(*) FROM market.underlying_volume_models model
                LEFT JOIN market.underlyings underlying
                  ON underlying.snapshot_id = model.snapshot_id
                 AND underlying.underlying_id = model.underlying_id
                WHERE model.snapshot_id = ? AND underlying.underlying_id IS NULL
            """,
            "orphan_option_daily": """
                SELECT count(*) FROM market.option_daily daily
                LEFT JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.option_id = daily.option_id
                WHERE daily.snapshot_id = ? AND contract.option_id IS NULL
            """,
            "option_quote_contract_mismatch": """
                SELECT count(*) FROM market.option_daily daily
                JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.option_id = daily.option_id
                WHERE daily.snapshot_id = ?
                  AND (daily.underlying_id != contract.underlying_id
                       OR daily.call_put != contract.call_put
                       OR daily.strike != contract.strike
                       OR daily.expiry != contract.expiry
                       OR daily.exercise_style != contract.exercise_style
                       OR daily.settlement_type != contract.settlement_type
                       OR daily.contract_multiplier != contract.contract_multiplier)
            """,
            "missing_pricing_metadata": """
                SELECT count(*) FROM market.underlying_daily daily
                LEFT JOIN market.pricing_metadata pricing
                  ON pricing.snapshot_id = daily.snapshot_id
                 AND pricing.valuation_date = daily.date
                 AND pricing.underlying_id = daily.underlying_id
                WHERE daily.snapshot_id = ? AND pricing.underlying_id IS NULL
            """,
            "missing_option_quote": """
                SELECT count(*)
                FROM market.underlying_daily daily
                JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.underlying_id = daily.underlying_id
                 AND daily.date < contract.expiry
                LEFT JOIN market.option_daily quote
                  ON quote.snapshot_id = daily.snapshot_id
                 AND quote.date = daily.date
                 AND quote.option_id = contract.option_id
                WHERE daily.snapshot_id = ? AND quote.option_id IS NULL
            """,
        }
        if not self._relation_exists("market.underlying_volume_models"):
            checks.pop("orphan_underlying_volume_model")
        failures = {
            name: self.connection.execute(sql, [self.config.snapshot_id]).fetchone()[0]
            for name, sql in checks.items()
        }
        failures = {name: count for name, count in failures.items() if count}
        if failures:
            raise ValueError(f"snapshot quality gates failed: {failures}")
        counts = self._counts()
        if self.config.schema_version == "1.8.0":
            if counts["intraday_bridge_spec_count"] != 1:
                raise ValueError(
                    "config-1.8 snapshot requires exactly one intraday bridge spec"
                )
            if counts["underlying_volume_model_count"] != len(
                self.config.underlyings
            ):
                raise ValueError(
                    "config-1.8 snapshot requires one volume model per underlying"
                )
        elif (
            counts["intraday_bridge_spec_count"]
            or counts["underlying_volume_model_count"]
        ):
            raise ValueError("legacy snapshot must not contain observation-law specs")
        self._assert_underlying_observation_replay()
        self._assert_solver_visible_observation_privacy()

    def _assert_underlying_observation_replay(self) -> None:
        """Exactly replay every config-1.8 OHLCV row from the initial state."""

        if self.config.schema_version != "1.8.0":
            return
        maximum_date = self.connection.execute(
            """
            SELECT max(date) FROM market.underlying_daily
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchone()[0]
        if maximum_date is None:
            raise ValueError("config-1.8 snapshot contains no underlying rows")
        dates = self.underlying_generator.business_dates_between(
            self.config.start_date, maximum_date
        )
        expected_rows: list[tuple[Any, ...]] = []
        for underlying in self.config.underlyings:
            previous_date: date | None = None
            previous_close = underlying.initial_spot
            for market_date in dates:
                if previous_date is None:
                    row = self.underlying_generator.initial_underlying_daily_row(
                        underlying, "observation-replay"
                    )
                else:
                    row = self.underlying_generator.underlying_daily_row(
                        underlying,
                        market_date,
                        previous_date,
                        previous_close,
                        "observation-replay",
                    )
                expected_rows.append(tuple(row[:-1]))
                previous_date = market_date
                previous_close = row[6]
        expected_rows.sort(key=lambda row: (row[1], row[2]))

        logical_columns = TABLE_SPECS["underlying_daily"].columns[:-1]
        actual_rows = self.connection.execute(
            f"""
            SELECT {', '.join(logical_columns)}
            FROM market.underlying_daily
            WHERE snapshot_id = ?
            ORDER BY date, underlying_id
            """,
            [self.config.snapshot_id],
        ).fetchall()
        if [tuple(row) for row in actual_rows] != expected_rows:
            raise ValueError(
                "underlying OHLCV rows conflict with exact config-1.8 replay"
            )

    def _assert_solver_visible_observation_privacy(self) -> None:
        """Reject private observation fields or values in solver-visible views."""

        forbidden_fields = {
            "bridge_spec_id",
            "volume_spec_id",
            "stream_namespace",
            "base_volume",
            "volume_log_stddev",
            "bridge_increment",
        }
        normalized_forbidden = {
            "".join(character for character in value if character.isalnum())
            for value in forbidden_fields
        }
        visible_columns = self.connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'solver_visible'
            """
        ).fetchall()
        leaked_columns = [
            column
            for (column,) in visible_columns
            if "".join(
                character
                for character in column.casefold()
                if character.isalnum()
            )
            in normalized_forbidden
        ]
        if leaked_columns:
            raise ValueError(
                f"private observation fields leaked into solver_visible: {leaked_columns}"
            )

        metadata_rows = self.connection.execute(
            """
            SELECT physical_dynamics, pricing_dynamics
            FROM solver_visible.pricing_metadata
            WHERE snapshot_id = ?
            """,
            [self.config.snapshot_id],
        ).fetchall()
        decoded_rows: list[tuple[Any, Any]] = []
        for physical, pricing in metadata_rows:
            decoded = (json.loads(physical), json.loads(pricing))
            assert_no_private_metadata_leakage(decoded)
            decoded_rows.append(decoded)
        if self.config.schema_version != "1.8.0":
            return
        bridge = self.config.intraday_bridge
        volume = self.config.volume_model
        if bridge is None or volume is None:
            raise ValueError("config-1.8 observation contracts are required")
        visible_payload = json.dumps(decoded_rows, sort_keys=True, default=str)
        for private_value in (
            bridge.bridge_spec_id,
            bridge.stream_namespace,
            volume.volume_spec_id,
            volume.stream_namespace,
        ):
            if private_value in visible_payload:
                raise ValueError(
                    "private observation contract value leaked into solver_visible"
                )

    def _assert_common_q_context(self) -> None:
        """Freeze the common Q, numeraire and flat rate-path identity.

        Config 1.7's drift-only mapping keeps P/Q Brownian covariance fixed,
        while every same-currency vanilla margin uses the one declared pricing
        measure, money-market numeraire and flat rate path. Correlation remains
        absent from the option generator's marginal pricing inputs.
        """

        q_dependence = self.config.q_underlying_dependence
        if q_dependence is None:
            return
        q_pricing = self.config.q_pricing
        if q_pricing is None:
            raise ValueError("Q dependence requires a common q_pricing contract")
        expected_context = (
            self.config.currency,
            q_pricing.risk_neutral_measure_id,
            q_pricing.numeraire_id,
            q_pricing.rate_path_id,
            q_dependence.dependence_spec_id,
        )
        if (
            q_dependence.risk_neutral_measure_id,
            q_dependence.numeraire_id,
            q_dependence.rate_path_id,
        ) != expected_context[1:4]:
            raise ValueError(
                "Q dependence and q_pricing measure/numeraire/rate-path IDs differ"
            )
        if len({underlying.risk_free_rate for underlying in self.config.underlyings}) != 1:
            raise ValueError(
                "one flat rate_path_id cannot identify different underlying rates"
            )
        persisted_contexts = set(
            self.connection.execute(
                """
                SELECT DISTINCT
                    currency,
                    json_extract_string(
                        pricing_dynamics,
                        '$.q_pricing.risk_neutral_measure_id'
                    ),
                    json_extract_string(
                        pricing_dynamics,
                        '$.q_pricing.numeraire_id'
                    ),
                    json_extract_string(
                        pricing_dynamics,
                        '$.q_pricing.rate_path_id'
                    ),
                    json_extract_string(
                        pricing_dynamics,
                        '$.underlying_dependence.dependence_spec_id'
                    )
                FROM market.pricing_metadata
                WHERE snapshot_id = ?
                """,
                [self.config.snapshot_id],
            ).fetchall()
        )
        if persisted_contexts and persisted_contexts != {expected_context}:
            raise ValueError(
                "pricing metadata does not share one Q/numeraire/rate-path/"
                "dependence identity"
            )

    def _record_revision(self, run_id: str) -> None:
        """Snapshot current logical counts under the next monotonic revision."""

        counts = self._counts()
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

    def _record_failed_run(
        self,
        run_id: str,
        operation: str,
        start_date: date,
        end_date: date,
        error: Exception,
    ) -> None:
        """Persist failure lineage after the market-data transaction rolls back."""

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

    def _counts(self) -> dict[str, int]:
        """Count every manifest/revision table for the configured snapshot."""

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
                if self._relation_exists(table)
                else 0
            )
            for name, table in tables.items()
        }

    def _write_manifest(self, summary: dict[str, Any]) -> None:
        """Atomically publish the current logical revision next to the DuckDB file."""

        manifest_path = self.database.with_suffix(".manifest.json")
        temporary_path = manifest_path.with_suffix(".manifest.json.tmp")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": summary["snapshot_id"],
            "status": summary["status"],
            "revision": summary["revision"],
            "database_file": self.database.name,
            "generator_config_id": summary["generator_config_id"],
            "generator_version": summary["generator_version"],
            "quantlib_version": summary["quantlib_version"],
            "duckdb_version": summary["duckdb_version"],
            "date_min": summary["date_min"],
            "date_max": summary["date_max"],
            "business_date_count": summary["business_date_count"],
            "underlying_count": summary["underlying_count"],
            "option_contract_count": summary["option_contract_count"],
            "underlying_daily_count": summary["underlying_daily_count"],
            "option_daily_count": summary["option_daily_count"],
            "pricing_metadata_count": summary["pricing_metadata_count"],
            "underlying_dependence_count": summary[
                "underlying_dependence_count"
            ],
            "option_chain_spec_count": summary["option_chain_spec_count"],
            "intraday_bridge_spec_count": summary[
                "intraday_bridge_spec_count"
            ],
            "underlying_volume_model_count": summary[
                "underlying_volume_model_count"
            ],
            "option_pricing_audit_count": summary["option_pricing_audit_count"],
        }
        assert_no_private_metadata_leakage(manifest, path="manifest")
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
