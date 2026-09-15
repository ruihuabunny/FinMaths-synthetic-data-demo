"""Explicit source-to-target migration steps for mutable authoring databases."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from synthetic_derivatives.authoring.schema_ddl import (
    DDL,
    SCHEMA_BOOTSTRAP,
    SCHEMA_VERSION,
)


@dataclass(frozen=True)
class SchemaMigrationStep:
    """One declared source identity and its preservation work for schema 2.6."""

    source_version: str
    target_version: str
    rebuild_legacy_dependence: bool


SCHEMA_MIGRATION_STEPS = {
    source: SchemaMigrationStep(
        source_version=source,
        target_version=SCHEMA_VERSION,
        rebuild_legacy_dependence=source != "2.5.0",
    )
    for source in ("2.0.0", "2.1.0", "2.2.0", "2.3.0", "2.4.0", "2.5.0")
}
MIGRATABLE_SCHEMA_VERSIONS = frozenset(SCHEMA_MIGRATION_STEPS)


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create schema 2.6 or migrate one explicitly supported mutable source.

    Every route preserves existing logical market observations exactly. Sources
    before 2.5 rebuild only the dependence table's P-only CHECK constraint;
    2.6 observation-law tables are created empty and never retrofitted. Frozen
    snapshots are rejected before any source table is renamed.
    """

    connection.execute(SCHEMA_BOOTSTRAP)
    source_version = _current_schema_version(connection)
    step = _resolve_migration_step(source_version)
    if step is not None:
        _assert_migration_is_mutable(connection)
    legacy_dependence_table = bool(
        step is not None
        and step.rebuild_legacy_dependence
        and _relation_exists(connection, "market", "underlying_dependence")
    )
    if legacy_dependence_table:
        connection.execute(
            """
            ALTER TABLE market.underlying_dependence
            RENAME TO underlying_dependence_schema_2_4
            """
        )

    connection.execute(DDL)
    if legacy_dependence_table:
        _restore_legacy_dependence_rows(connection)
    _apply_additive_columns(connection)
    _record_target_version(connection, source_version)


def _current_schema_version(
    connection: duckdb.DuckDBPyConnection,
) -> str | None:
    row = connection.execute(
        """
        SELECT schema_version FROM metadata.schema_versions
        ORDER BY applied_at DESC, schema_version DESC LIMIT 1
        """
    ).fetchone()
    return str(row[0]) if row else None


def _resolve_migration_step(
    source_version: str | None,
) -> SchemaMigrationStep | None:
    if source_version is None or source_version == SCHEMA_VERSION:
        return None
    try:
        return SCHEMA_MIGRATION_STEPS[source_version]
    except KeyError as error:
        raise RuntimeError(
            f"unsupported DuckDB schema version: {source_version}"
        ) from error


def _relation_exists(
    connection: duckdb.DuckDBPyConnection,
    schema_name: str,
    table_name: str,
) -> bool:
    return bool(
        connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema_name, table_name],
        ).fetchone()[0]
    )


def _assert_migration_is_mutable(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    if not _relation_exists(connection, "metadata", "snapshots"):
        return
    frozen_count = connection.execute(
        "SELECT count(*) FROM metadata.snapshots WHERE status = 'FROZEN'"
    ).fetchone()[0]
    if frozen_count:
        raise RuntimeError(
            "frozen snapshot schema is immutable; open it read-only or "
            "clone it before migration"
        )


def _restore_legacy_dependence_rows(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    connection.execute(
        """
        INSERT INTO market.underlying_dependence (
            snapshot_id, dependence_spec_id, measure,
            source_dependence_spec_id, mapping_id, mapping_type,
            risk_neutral_measure_id, numeraire_id, rate_path_id,
            driver_order, formulation, factor_loading_matrix,
            idiosyncratic_diagonal, correlation_matrix, matrix_dtype,
            factorization_method, factorization_order, time_grid,
            regime_id, generator_config_id, created_run_id
        )
        SELECT
            snapshot_id, dependence_spec_id, measure,
            NULL, NULL, NULL, NULL, NULL, NULL,
            driver_order, formulation, factor_loading_matrix,
            idiosyncratic_diagonal, correlation_matrix, matrix_dtype,
            factorization_method, factorization_order, time_grid,
            regime_id, generator_config_id, created_run_id
        FROM market.underlying_dependence_schema_2_4
        """
    )
    connection.execute("DROP TABLE market.underlying_dependence_schema_2_4")


def _apply_additive_columns(connection: duckdb.DuckDBPyConnection) -> None:
    for column_definition in (
        "underlying_dependence_count BIGINT DEFAULT 0",
        "option_chain_spec_count BIGINT DEFAULT 0",
        "intraday_bridge_spec_count BIGINT DEFAULT 0",
        "underlying_volume_model_count BIGINT DEFAULT 0",
        "option_pricing_audit_count BIGINT DEFAULT 0",
    ):
        connection.execute(
            f"""
            ALTER TABLE metadata.snapshot_revisions
            ADD COLUMN IF NOT EXISTS {column_definition}
            """
        )
    connection.execute(
        """
        ALTER TABLE market.underlyings
        ALTER COLUMN base_implied_volatility DROP NOT NULL
        """
    )
    for column_definition in (
        "chain_id VARCHAR",
        "listing_date DATE",
        "listing_spot DECIMAL(24, 8)",
        "strike_moneyness DECIMAL(24, 8)",
    ):
        connection.execute(
            f"""
            ALTER TABLE market.option_contracts
            ADD COLUMN IF NOT EXISTS {column_definition}
            """
        )
    for column_definition in ("liquidity_filter JSON", "quote_model JSON"):
        connection.execute(
            f"""
            ALTER TABLE market.option_chain_specs
            ADD COLUMN IF NOT EXISTS {column_definition}
            """
        )


def _record_target_version(
    connection: duckdb.DuckDBPyConnection,
    source_version: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO metadata.schema_versions (schema_version)
        VALUES (?) ON CONFLICT DO NOTHING
        """,
        [SCHEMA_VERSION],
    )
    if source_version is not None and source_version != SCHEMA_VERSION:
        connection.execute(
            """
            UPDATE metadata.snapshots
            SET schema_version = ?
            WHERE schema_version = ?
            """,
            [SCHEMA_VERSION, source_version],
        )
