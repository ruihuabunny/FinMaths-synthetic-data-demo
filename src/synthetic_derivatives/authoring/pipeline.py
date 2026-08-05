"""Incremental, append-safe authoring pipeline for a DuckDB snapshot."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import QuantLib as ql

from synthetic_derivatives.authoring.config import GeneratorConfig, option_id
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
            self._validate_additive_config()
            self.connection.execute(
                """
                INSERT INTO metadata.generation_runs (
                    run_id, snapshot_id, operation, status, config_sha256,
                    requested_start_date, requested_end_date
                ) VALUES (?, ?, ?, 'RUNNING', ?, ?, ?)
                """,
                [
                    run_id,
                    self.config.snapshot_id,
                    operation,
                    self.config.sha256,
                    dates[0],
                    dates[-1],
                ],
            )

            stats = self._generate_incremental_rows(dates, run_id)
            self._assert_quality_gates()
            changed = sum(
                table_stats["inserted"] + table_stats["updated"]
                for table_stats in stats.values()
            )
            content_hash = self._content_hash()
            status = "COMPLETED" if changed else "NOOP"
            if changed:
                self._record_revision(run_id, content_hash)
            self.connection.execute(
                """
                UPDATE metadata.snapshots
                SET current_config_sha256 = ?, content_sha256 = ?,
                    updated_at = current_timestamp
                WHERE snapshot_id = ?
                """,
                [self.config.sha256, content_hash, self.config.snapshot_id],
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
            if not (isinstance(error, RuntimeError) and "snapshot is FROZEN" in str(error)):
                self._record_failed_run(run_id, operation, dates[0], dates[-1], error)
            raise
        summary = self.summary()
        self._write_manifest(summary)
        return {
            "run_id": run_id,
            "status": status,
            "snapshot_id": self.config.snapshot_id,
            "content_sha256": content_hash,
            "table_stats": stats,
            "summary": summary,
        }

    def freeze(self) -> dict[str, Any]:
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._ensure_snapshot_is_editable()
            self._assert_quality_gates()
            content_hash = self._content_hash()
            self.connection.execute(
                """
                UPDATE metadata.snapshots
                SET status = 'FROZEN', content_sha256 = ?,
                    frozen_at = current_timestamp, updated_at = current_timestamp
                WHERE snapshot_id = ?
                """,
                [content_hash, self.config.snapshot_id],
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
            SELECT status, current_revision, content_sha256,
                   generator_config_id, generator_version,
                   current_config_sha256, quantlib_version, duckdb_version,
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
            "content_sha256": snapshot[2] if snapshot else None,
            "generator_config_id": snapshot[3] if snapshot else None,
            "generator_version": snapshot[4] if snapshot else None,
            "config_sha256": snapshot[5] if snapshot else self.config.sha256,
            "quantlib_version": snapshot[6] if snapshot else ql.__version__,
            "duckdb_version": snapshot[7] if snapshot else duckdb.__version__,
            "seed": snapshot[8] if snapshot else self.config.seed,
            "rng": snapshot[9] if snapshot else self.config.rng,
            "date_min": date_bounds[0].isoformat() if date_bounds[0] else None,
            "date_max": date_bounds[1].isoformat() if date_bounds[1] else None,
            "business_date_count": date_bounds[2],
            **counts,
        }

    def _ensure_snapshot_is_editable(self) -> None:
        row = self.connection.execute(
            "SELECT status, generator_config_id FROM metadata.snapshots WHERE snapshot_id = ?",
            [self.config.snapshot_id],
        ).fetchone()
        if row is None:
            self.connection.execute(
                """
                INSERT INTO metadata.snapshots (
                    snapshot_id, schema_version, status, generator_config_id,
                    generator_version, current_config_sha256, quantlib_version,
                    duckdb_version, seed, rng
                ) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    self.config.snapshot_id,
                    SCHEMA_VERSION,
                    self.config.generator_config_id,
                    self.config.generator_version,
                    self.config.sha256,
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

    def _validate_additive_config(self) -> None:
        configured_underlyings = {item.underlying_id for item in self.config.underlyings}
        existing_underlyings = {
            row[0]
            for row in self.connection.execute(
                "SELECT underlying_id FROM market.underlyings WHERE snapshot_id = ?",
                [self.config.snapshot_id],
            ).fetchall()
        }
        removed = existing_underlyings - configured_underlyings
        if removed:
            raise ValueError(f"config cannot remove existing underlyings: {sorted(removed)}")

        configured_options = {
            option_id(underlying.underlying_id, template.template_id)
            for underlying in self.config.underlyings
            for template in self.config.option_templates
        }
        existing_options = {
            row[0]
            for row in self.connection.execute(
                "SELECT option_id FROM market.option_contracts WHERE snapshot_id = ?",
                [self.config.snapshot_id],
            ).fetchall()
        }
        removed_options = existing_options - configured_options
        if removed_options:
            raise ValueError(f"config cannot remove existing options: {sorted(removed_options)}")

    def _generate_incremental_rows(
        self, dates: list[date], run_id: str
    ) -> dict[str, dict[str, int]]:
        underlying_master_rows = [
            self.generator.underlying_master_row(underlying, run_id)
            for underlying in self.config.underlyings
        ]
        option_contract_rows = [
            self.generator.option_contract_row(underlying, template, run_id)
            for underlying in self.config.underlyings
            for template in self.config.option_templates
        ]
        self._assert_master_definitions_are_immutable(
            "market.underlyings", "underlying_id", underlying_master_rows, "underlyings"
        )
        self._assert_master_definitions_are_immutable(
            "market.option_contracts", "option_id", option_contract_rows, "option_contracts"
        )
        stats = {
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

    def _assert_master_definitions_are_immutable(
        self,
        table: str,
        id_column: str,
        candidate_rows: list[tuple[Any, ...]],
        spec_name: str,
    ) -> None:
        existing = {
            row[0]: row[1]
            for row in self.connection.execute(
                f"SELECT {id_column}, row_sha256 FROM {table} WHERE snapshot_id = ?",
                [self.config.snapshot_id],
            ).fetchall()
        }
        spec = TABLE_SPECS[spec_name]
        id_index = spec.columns.index(id_column)
        hash_index = spec.columns.index("row_sha256")
        changed = [
            row[id_index]
            for row in candidate_rows
            if row[id_index] in existing and existing[row[id_index]] != row[hash_index]
        ]
        if changed:
            raise ValueError(
                f"existing entity definitions are immutable: {sorted(changed)}; "
                "use a new snapshot_id for corrections"
            )

    def _assert_quality_gates(self) -> None:
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

    def _record_revision(self, run_id: str, content_hash: str) -> None:
        counts = self._counts()
        current_revision = self.connection.execute(
            "SELECT current_revision FROM metadata.snapshots WHERE snapshot_id = ?",
            [self.config.snapshot_id],
        ).fetchone()[0]
        revision = current_revision + 1
        self.connection.execute(
            """
            INSERT INTO metadata.snapshot_revisions (
                snapshot_id, revision, run_id, config_sha256, content_sha256,
                underlying_count, option_contract_count, underlying_daily_count,
                option_daily_count, pricing_metadata_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                self.config.snapshot_id,
                revision,
                run_id,
                self.config.sha256,
                content_hash,
                counts["underlying_count"],
                counts["option_contract_count"],
                counts["underlying_daily_count"],
                counts["option_daily_count"],
                counts["pricing_metadata_count"],
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
                run_id, snapshot_id, operation, status, config_sha256,
                requested_start_date, requested_end_date, error_message, completed_at
            ) VALUES (?, ?, ?, 'FAILED', ?, ?, ?, ?, current_timestamp)
            """,
            [
                run_id,
                self.config.snapshot_id,
                operation,
                self.config.sha256,
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
        }
        return {
            name: self.connection.execute(
                f"SELECT count(*) FROM {table} WHERE snapshot_id = ?",
                [self.config.snapshot_id],
            ).fetchone()[0]
            for name, table in tables.items()
        }

    def _content_hash(self) -> str:
        digest = hashlib.sha256()
        tables_and_order = (
            ("market.underlyings", "underlying_id"),
            ("market.option_contracts", "option_id"),
            ("market.underlying_daily", "date, underlying_id"),
            ("market.option_daily", "date, option_id"),
            ("market.pricing_metadata", "valuation_timestamp, underlying_id"),
        )
        for table, ordering in tables_and_order:
            rows = self.connection.execute(
                f"""
                SELECT row_sha256 FROM {table}
                WHERE snapshot_id = ? ORDER BY {ordering}
                """,
                [self.config.snapshot_id],
            ).fetchall()
            digest.update(f"{table}\n".encode("utf-8"))
            for row in rows:
                digest.update(row[0].encode("ascii"))
                digest.update(b"\n")
        return digest.hexdigest()

    def _write_manifest(self, summary: dict[str, Any]) -> None:
        """Atomically publish the current logical revision next to the DuckDB file."""

        manifest_path = self.database.with_suffix(".manifest.json")
        temporary_path = manifest_path.with_suffix(".manifest.json.tmp")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": summary["snapshot_id"],
            "status": summary["status"],
            "revision": summary["revision"],
            "content_sha256": summary["content_sha256"],
            "database_file": self.database.name,
            "generator_config_id": summary["generator_config_id"],
            "generator_version": summary["generator_version"],
            "config_sha256": summary["config_sha256"],
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
            "hash_scope": "ordered logical row hashes; operational run timestamps excluded",
        }
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
