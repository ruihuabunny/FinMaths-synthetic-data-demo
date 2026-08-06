"""Incremental, append-safe authoring pipeline for a DuckDB snapshot."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import QuantLib as ql

from synthetic_derivatives.authoring.config import GeneratorConfig
from synthetic_derivatives.authoring.generator import QuantLibGenerator
from synthetic_derivatives.authoring.schema import (
    SCHEMA_VERSION,
    TABLE_SPECS,
    initialize_schema,
    merge_rows,
)


class AuthoringPipeline(AbstractContextManager["AuthoringPipeline"]):
    def __init__(self, database: str | Path, config: GeneratorConfig):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.generator = QuantLibGenerator(config)
        self.connection = duckdb.connect(str(self.database))
        initialize_schema(self.connection)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.connection.close()

    def create_smoke_snapshot(self) -> dict[str, Any]:
        dates = self.generator.business_dates(
            self.config.start_date, self.config.business_days
        )
        return self.sync_range(dates[0], dates[-1], operation="CREATE_OR_SYNC")

    def append_business_days(self, count: int) -> dict[str, Any]:
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
        dates = self.generator.next_business_dates(last_date, count)
        return self.sync_range(dates[0], dates[-1], operation="APPEND_DATES")

    def sync_config(self) -> dict[str, Any]:
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
        dates = self.generator.business_dates_between(start_date, end_date)
        if not dates:
            raise ValueError("requested range contains no business dates")
        run_id = str(uuid4())
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._ensure_snapshot_is_editable()
            self._assert_underlying_dependence_is_compatible()
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

            stats = self._generate_incremental_rows(dates, run_id)
            self._assert_quality_gates()
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
            self._record_failed_run(run_id, operation, dates[0], dates[-1], error)
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

    def summary(self) -> dict[str, Any]:
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
        row = self.connection.execute(
            """
            SELECT status, generator_config_id, generator_version
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

    def _generate_incremental_rows(
        self, dates: list[date], run_id: str
    ) -> dict[str, dict[str, int]]:
        underlying_dependence_row = self.generator.underlying_dependence_row(run_id)
        underlying_master_rows = [
            self.generator.underlying_master_row(underlying, run_id)
            for underlying in self.config.underlyings
        ]
        option_contract_rows = [
            self.generator.option_contract_row(underlying, template, run_id)
            for underlying in self.config.underlyings
            for template in self.config.option_templates
        ]
        stats = {
            "underlying_dependence": merge_rows(
                self.connection,
                TABLE_SPECS["underlying_dependence"],
                [underlying_dependence_row]
                if underlying_dependence_row is not None
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
        previous_date_by_key: dict[tuple[date, str], date] = {}
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
                    previous_date = self.generator.previous_business_date(market_date)
                    previous_close = underlying.initial_spot
                row = self.generator.underlying_daily_row(
                    underlying, market_date, previous_date, previous_close, run_id
                )
                underlying_daily_rows.append(row)
                existing[market_date] = row[6]

            prior_path_date: date | None = None
            for path_date in sorted(existing):
                if path_date in requested_dates:
                    previous_date_by_key[(path_date, underlying.underlying_id)] = (
                        prior_path_date
                        if prior_path_date is not None
                        else self.generator.previous_business_date(path_date)
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
        metadata_rows = [
            self.generator.pricing_metadata_row(
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
        for contract in option_contract_rows:
            underlying_id = contract[2]
            for market_date in dates:
                if (market_date, contract[1]) in existing_option_daily:
                    continue
                spot = spot_by_key[(market_date, underlying_id)]
                row = self.generator.option_daily_row(
                    underlyings_by_id[underlying_id],
                    contract,
                    market_date,
                    spot,
                    run_id,
                )
                if row is not None:
                    option_daily_rows.append(row)
        stats["option_daily"] = merge_rows(
            self.connection, TABLE_SPECS["option_daily"], option_daily_rows
        )
        return stats

    def _assert_underlying_dependence_is_compatible(self) -> None:
        columns = TABLE_SPECS["underlying_dependence"].columns[:-1]
        existing_rows = self.connection.execute(
            f"""
            SELECT {', '.join(columns)}
            FROM market.underlying_dependence
            WHERE snapshot_id = ?
            ORDER BY dependence_spec_id
            """,
            [self.config.snapshot_id],
        ).fetchall()
        expected_row = self.generator.underlying_dependence_row(
            "compatibility-check"
        )
        if expected_row is None:
            if existing_rows:
                raise ValueError(
                    "underlying_simulation cannot be removed within one snapshot; "
                    "create a new snapshot instead"
                )
            return

        expected_logical_row = tuple(expected_row[:-1])
        if existing_rows:
            if len(existing_rows) != 1 or tuple(existing_rows[0]) != expected_logical_row:
                raise ValueError(
                    "underlying_simulation cannot change within one snapshot; "
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
                "underlying_simulation cannot be added to an existing path; "
                "create a new snapshot instead"
            )

    def _assert_quality_gates(self) -> None:
        self._assert_underlying_dependence_is_compatible()
        checks = {
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
            "orphan_option_daily": """
                SELECT count(*) FROM market.option_daily daily
                LEFT JOIN market.option_contracts contract
                  ON contract.snapshot_id = daily.snapshot_id
                 AND contract.option_id = daily.option_id
                WHERE daily.snapshot_id = ? AND contract.option_id IS NULL
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
        failures = {
            name: self.connection.execute(sql, [self.config.snapshot_id]).fetchone()[0]
            for name, sql in checks.items()
        }
        failures = {name: count for name, count in failures.items() if count}
        if failures:
            raise ValueError(f"snapshot quality gates failed: {failures}")

    def _record_revision(self, run_id: str) -> None:
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
                underlying_dependence_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        tables = {
            "underlying_count": "market.underlyings",
            "option_contract_count": "market.option_contracts",
            "underlying_daily_count": "market.underlying_daily",
            "option_daily_count": "market.option_daily",
            "pricing_metadata_count": "market.pricing_metadata",
            "underlying_dependence_count": "market.underlying_dependence",
        }
        return {
            name: self.connection.execute(
                f"SELECT count(*) FROM {table} WHERE snapshot_id = ?",
                [self.config.snapshot_id],
            ).fetchone()[0]
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
            "seed": summary["seed"],
            "rng": summary["rng"],
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
        }
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
