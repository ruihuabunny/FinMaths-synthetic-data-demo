"""Immutable snapshot/config preflight validation."""

from __future__ import annotations

from typing import Any

import duckdb

from synthetic_derivatives.authoring.config_models import GeneratorConfig
from synthetic_derivatives.authoring.schema_ddl import SCHEMA_VERSION
from synthetic_derivatives.authoring.snapshot_store import SnapshotStore
from synthetic_derivatives.authoring.table_specs import TABLE_SPECS


class SnapshotCompatibilityValidator:
    """Protect existing economic identities before any run row is inserted."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        config: GeneratorConfig,
        underlying_generator: Any,
        option_generator: Any,
        store: SnapshotStore,
    ) -> None:
        self.connection = connection
        self.config = config
        self.underlying_generator = underlying_generator
        self.option_generator = option_generator
        self.store = store

    def preflight_existing_snapshot(self) -> None:
        self.store.ensure_snapshot_is_editable()
        self._assert_underlying_dependence_is_compatible()
        self._assert_observation_specs_are_compatible()
        self._assert_option_chain_is_compatible()
        self._assert_option_contracts_are_compatible()

    def assert_snapshot_identity(self) -> None:
        self.store.assert_snapshot_identity()

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

        return self.store.relation_exists(qualified_name)

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
        expected_contracts = sorted(
            (
                self.option_generator.option_contract_row(
                    underlying, template, "compatibility-check"
                )
                for underlying in self.config.underlyings
                for template in self.config.option_templates
            ),
            key=lambda row: row.option_id,
        )
        expected_rows = [tuple(row[:-1]) for row in expected_contracts]
        if [tuple(row) for row in existing_rows] != expected_rows:
            raise ValueError(
                "listed option-chain contracts cannot change within one snapshot; "
                "create a new snapshot instead"
            )
