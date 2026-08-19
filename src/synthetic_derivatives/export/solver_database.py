"""Deterministically project a frozen authoring snapshot into a public DuckDB.

This module is a data-boundary implementation, not a market model.  It selects
already-materialized observations, restricts the declared P/Q driver law to a
principal sub-universe, and writes no prices, implied volatilities, Greeks, or
new stochastic transitions.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from synthetic_derivatives.export.contracts import (
    LOGICAL_CHECKSUM_CONTRACT_ID,
    PINNED_DUCKDB_VERSION,
    PUBLIC_DATABASE_SCHEMA_VERSION,
    PUBLIC_TABLE_CONTRACTS,
    SAMPLING_CONTRACT_ID,
    SOLVER_DATABASE_EXPORT_VERSION,
    PublicDatabaseManifest,
    PublicTableContract,
    SolverDatabaseExportContract,
    assert_no_private_leakage,
)


@dataclass(frozen=True)
class _AuthoringLogicalQuery:
    identity: str
    columns: tuple[str, ...]
    sql: str
    json_columns: frozenset[str] = frozenset()


# Hash only immutable snapshot identity and relations that can affect a public
# projection. Operational UUIDs, wall clocks, and private answer/audit tables
# are deliberately outside this logical contract.
_AUTHORING_LOGICAL_QUERIES = (
    _AuthoringLogicalQuery(
        "metadata.snapshots",
        (
            "snapshot_id",
            "schema_version",
            "status",
            "generator_config_id",
            "generator_version",
            "quantlib_version",
            "duckdb_version",
            "seed",
            "rng",
            "current_revision",
        ),
        """
        SELECT snapshot_id, schema_version, status, generator_config_id,
               generator_version, quantlib_version, duckdb_version, seed, rng,
               current_revision
        FROM metadata.snapshots
        WHERE snapshot_id = ?
        ORDER BY snapshot_id
        """,
    ),
    _AuthoringLogicalQuery(
        "solver_visible.underlying_daily",
        (
            "snapshot_id",
            "date",
            "underlying_id",
            "spot_close",
        ),
        """
        SELECT snapshot_id, date, underlying_id, spot_close
        FROM solver_visible.underlying_daily
        WHERE snapshot_id = ?
        ORDER BY snapshot_id, date, underlying_id
        """,
    ),
    _AuthoringLogicalQuery(
        "solver_visible.option_daily",
        (
            "snapshot_id",
            "date",
            "underlying_id",
            "option_id",
            "call_put",
            "strike",
            "expiry",
            "exercise_style",
            "settlement_type",
            "contract_multiplier",
            "bid",
            "ask",
        ),
        """
        SELECT snapshot_id, date, underlying_id, option_id, call_put, strike,
               expiry, exercise_style, settlement_type, contract_multiplier,
               bid, ask
        FROM solver_visible.option_daily
        WHERE snapshot_id = ?
        ORDER BY snapshot_id, date, underlying_id, expiry, strike, call_put,
                 option_id
        """,
    ),
    _AuthoringLogicalQuery(
        "solver_visible.pricing_metadata",
        (
            "snapshot_id",
            "valuation_date",
            "underlying_id",
            "currency",
            "discount_curve",
            "risk_free_rate",
            "dividend_curve",
            "dividend_yield",
            "calendar",
            "day_count",
        ),
        """
        SELECT snapshot_id,
               CAST(valuation_timestamp AT TIME ZONE 'UTC' AS DATE),
               underlying_id, currency, discount_curve, risk_free_rate,
               dividend_curve, dividend_yield, calendar, day_count
        FROM solver_visible.pricing_metadata
        WHERE snapshot_id = ?
        ORDER BY snapshot_id, valuation_timestamp, underlying_id
        """,
        frozenset({"discount_curve", "dividend_curve"}),
    ),
    _AuthoringLogicalQuery(
        "solver_visible.underlying_dependence",
        (
            "snapshot_id",
            "dependence_spec_id",
            "measure",
            "source_dependence_spec_id",
            "mapping_id",
            "mapping_type",
            "risk_neutral_measure_id",
            "numeraire_id",
            "rate_path_id",
            "driver_order",
            "formulation",
            "factor_loading_matrix",
            "idiosyncratic_diagonal",
            "correlation_matrix",
            "matrix_dtype",
            "factorization_method",
            "factorization_order",
            "time_grid",
            "regime_id",
        ),
        """
        SELECT snapshot_id, dependence_spec_id, measure,
               source_dependence_spec_id, mapping_id, mapping_type,
               risk_neutral_measure_id, numeraire_id, rate_path_id,
               driver_order, formulation, factor_loading_matrix,
               idiosyncratic_diagonal, correlation_matrix, matrix_dtype,
               factorization_method, factorization_order, time_grid, regime_id
        FROM solver_visible.underlying_dependence
        WHERE snapshot_id = ?
        ORDER BY snapshot_id, measure, dependence_spec_id
        """,
        frozenset(
            {
                "driver_order",
                "factor_loading_matrix",
                "idiosyncratic_diagonal",
                "correlation_matrix",
            }
        ),
    ),
)


_NULLABLE_PUBLIC_COLUMNS = frozenset(
    {
        ("solver_visible.underlying_dependence", "source_dependence_spec_id"),
        ("solver_visible.underlying_dependence", "mapping_id"),
        ("solver_visible.underlying_dependence", "mapping_type"),
        ("solver_visible.underlying_dependence", "risk_neutral_measure_id"),
        ("solver_visible.underlying_dependence", "numeraire_id"),
        ("solver_visible.underlying_dependence", "rate_path_id"),
    }
)


def _canonical_json_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_object(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_canonical_json_object(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("public JSON values must be finite")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("declared JSON column contains invalid JSON") from error
    return value


def _canonical_json_text(value: Any) -> str:
    canonical = _canonical_json_object(_decode_json(value))
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_hash_value(value: Any, *, is_json: bool = False) -> Any:
    if value is None:
        return None
    if is_json:
        return {"json": _canonical_hash_json(_decode_json(value))}
    if isinstance(value, Decimal):
        return {"decimal": format(value, "f")}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            rendered = value.isoformat(timespec="microseconds")
        else:
            rendered = value.astimezone(timezone.utc).isoformat(timespec="microseconds")
        return {"datetime": rendered}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("logical checksum input must be finite")
        return {"float64": format(value, ".17g")}
    if isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported logical checksum type: {type(value).__name__}")


def _canonical_hash_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_hash_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_canonical_hash_json(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("logical checksum JSON input must be finite")
        return {"float64": format(value, ".17g")}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported logical checksum JSON type: {type(value).__name__}")


def _hash_rows(
    hasher: Any,
    table_name: str,
    column_names: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    json_columns: frozenset[str] = frozenset(),
    header_columns: Sequence[str] | None = None,
) -> None:
    header = json.dumps(
        {
            "table": table_name,
            "columns": list(header_columns or column_names),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    hasher.update(header.encode("utf-8"))
    hasher.update(b"\n")
    for row in rows:
        payload = [
            _canonical_hash_value(value, is_json=column in json_columns)
            for column, value in zip(column_names, row, strict=True)
        ]
        hasher.update(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        hasher.update(b"\n")


def _single_parent_identity(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[str, int, str, str]:
    rows = connection.execute(
        """
        SELECT snapshot_id, current_revision, status, duckdb_version
        FROM metadata.snapshots
        ORDER BY snapshot_id
        """
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("authoring parent must contain exactly one snapshot")
    snapshot_id, revision, status, duckdb_version = rows[0]
    return str(snapshot_id), int(revision), str(status), str(duckdb_version)


def _authoring_checksum_from_connection(
    connection: duckdb.DuckDBPyConnection, snapshot_id: str
) -> str:
    hasher = sha256()
    hasher.update(b"authoring-logical-snapshot-v1\n")
    for query in _AUTHORING_LOGICAL_QUERIES:
        rows = connection.execute(query.sql, [snapshot_id]).fetchall()
        _hash_rows(
            hasher,
            query.identity,
            query.columns,
            rows,
            json_columns=query.json_columns,
        )
    return hasher.hexdigest()


def canonical_authoring_logical_checksum(database: str | Path) -> str:
    """Hash parent logical content while excluding run UUIDs and wall clocks."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"authoring parent database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        snapshot_id, _, _, _ = _single_parent_identity(connection)
        return _authoring_checksum_from_connection(connection, snapshot_id)
    finally:
        connection.close()


def sample_underlyings(
    universe: Sequence[str], *, sample_size: int = 8, seed: int
) -> tuple[str, ...]:
    """Select a deterministic without-replacement set, returned canonically."""

    canonical = tuple(sorted(str(item) for item in universe))
    if len(canonical) != len(set(canonical)):
        raise ValueError("underlying universe must be unique")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("sampling seed must be a nonnegative integer")
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size < 1
        or sample_size > len(canonical)
    ):
        raise ValueError("sample_size must be within the underlying universe")
    ranked = sorted(
        canonical,
        key=lambda underlying_id: (
            sha256(
                f"{SAMPLING_CONTRACT_ID}|{seed}|{underlying_id}".encode("utf-8")
            ).digest(),
            underlying_id,
        ),
    )
    return tuple(sorted(ranked[:sample_size]))


def stable_sample_id(
    *,
    parent_logical_checksum: str,
    seed: int,
    underlying_ids: Sequence[str],
    variant_id: str,
) -> str:
    """Derive the selection identity without exposing the private seed."""

    payload = json.dumps(
        {
            "export_version": SOLVER_DATABASE_EXPORT_VERSION,
            "sampling_contract_id": SAMPLING_CONTRACT_ID,
            "variant_id": variant_id,
            "parent_logical_checksum": parent_logical_checksum,
            "sampling_seed": seed,
            "underlying_ids": sorted(str(item) for item in underlying_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "solver-sample-" + sha256(payload.encode("utf-8")).hexdigest()[:24]


def stable_task_id(
    *,
    sample_id: str,
    parent_snapshot_id: str,
    parent_snapshot_revision: int,
    variant_id: str,
    valuation_date: date,
    option_ids: Sequence[str],
) -> str:
    """Derive one task identity from its complete solver-visible selector."""

    payload = json.dumps(
        {
            "export_version": SOLVER_DATABASE_EXPORT_VERSION,
            "sample_id": sample_id,
            "parent_snapshot_id": parent_snapshot_id,
            "parent_snapshot_revision": parent_snapshot_revision,
            "variant_id": variant_id,
            "valuation_date": valuation_date.isoformat(),
            "option_ids": sorted(str(item) for item in option_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "solver-task-" + sha256(payload.encode("utf-8")).hexdigest()[:24]


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


def _project_dependence_rows(
    raw_rows: Sequence[Sequence[Any]],
    *,
    child_snapshot_id: str,
    sample_id: str,
    selected_underlyings: tuple[str, ...],
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    if len(raw_rows) != 2:
        raise ValueError("frozen parent must expose exactly one P/Q dependence pair")
    by_measure = {str(row[1]): row for row in raw_rows}
    if set(by_measure) != {"P", "Q"}:
        raise ValueError("frozen parent must expose one P and one Q dependence row")
    physical = by_measure["P"]
    pricing = by_measure["Q"]
    physical_order = tuple(str(item) for item in _decode_json(physical[9]))
    pricing_order = tuple(str(item) for item in _decode_json(pricing[9]))
    if physical_order != pricing_order:
        raise ValueError("parent P/Q dependence driver order is inconsistent")
    if pricing[2] != physical[0]:
        raise ValueError("parent Q dependence does not identify its source P spec")
    if any(item not in physical_order for item in selected_underlyings):
        raise ValueError("selected underlying is absent from the parent driver order")
    selected_indices = tuple(physical_order.index(item) for item in selected_underlyings)

    def restricted_components(row: Sequence[Any]) -> tuple[list[Any], list[Any], list[Any]]:
        loadings = _decode_json(row[11])
        diagonal = _decode_json(row[12])
        correlation = _decode_json(row[13])
        if (
            not isinstance(loadings, list)
            or not isinstance(diagonal, list)
            or not isinstance(correlation, list)
            or len(loadings) != len(physical_order)
            or len(diagonal) != len(physical_order)
            or len(correlation) != len(physical_order)
            or any(not isinstance(item, list) or len(item) != len(physical_order) for item in correlation)
        ):
            raise ValueError("parent dependence matrices do not match driver order")
        return (
            [loadings[index] for index in selected_indices],
            [diagonal[index] for index in selected_indices],
            [
                [correlation[row_index][column_index] for column_index in selected_indices]
                for row_index in selected_indices
            ],
        )

    physical_components = restricted_components(physical)
    pricing_components = restricted_components(pricing)
    if physical_components != pricing_components:
        raise ValueError("declared drift-only parent must keep identical P/Q covariance")

    identity_payload = json.dumps(
        {
            "sample_id": sample_id,
            "parent_p_spec_id": str(physical[0]),
            "parent_q_spec_id": str(pricing[0]),
            "driver_order": list(selected_underlyings),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    identity_suffix = sha256(identity_payload.encode("utf-8")).hexdigest()[:20]
    physical_id = f"PUBLIC-P-DEPENDENCE-{identity_suffix}"
    pricing_id = f"PUBLIC-Q-DEPENDENCE-{identity_suffix}"
    mapping_id = f"PUBLIC-Q-MAPPING-{identity_suffix}"
    driver_order_json = _canonical_json_text(list(selected_underlyings))

    def common_tail(row: Sequence[Any], components: Sequence[Any]) -> tuple[Any, ...]:
        return (
            driver_order_json,
            row[10],
            _canonical_json_text(components[0]),
            _canonical_json_text(components[1]),
            _canonical_json_text(components[2]),
            row[14],
            row[15],
            row[16],
            row[17],
            row[18],
        )

    physical_public = (
        child_snapshot_id,
        physical_id,
        "P",
        None,
        None,
        None,
        None,
        None,
        None,
        *common_tail(physical, physical_components),
    )
    pricing_public = (
        child_snapshot_id,
        pricing_id,
        "Q",
        physical_id,
        mapping_id,
        pricing[4],
        pricing[5],
        pricing[6],
        pricing[7],
        *common_tail(pricing, pricing_components),
    )
    return physical_public, pricing_public


def _fetch_projection(
    connection: duckdb.DuckDBPyConnection,
    *,
    parent_snapshot_id: str,
    child_snapshot_id: str,
    selected_underlyings: tuple[str, ...],
    contract: SolverDatabaseExportContract,
    sample_id: str,
) -> dict[str, list[tuple[Any, ...]]]:
    underlying_placeholders = _placeholders(selected_underlyings)
    state_source = connection.execute(
        f"""
        SELECT date, underlying_id, spot_close
        FROM authoring_parent.solver_visible.underlying_daily
        WHERE snapshot_id = ? AND date = ?
          AND underlying_id IN ({underlying_placeholders})
        ORDER BY date, underlying_id
        """,
        [parent_snapshot_id, contract.valuation_date, *selected_underlyings],
    ).fetchall()
    if len(state_source) != len(selected_underlyings):
        raise ValueError("valuation date lacks one state row per selected underlying")
    state_rows = [
        (
            child_snapshot_id,
            row[0],
            row[1],
            "P",
            row[2],
        )
        for row in state_source
    ]

    option_parameters: list[Any] = [
        parent_snapshot_id,
        contract.valuation_date,
        *selected_underlyings,
    ]
    option_filter = ""
    if contract.option_ids is not None:
        option_filter = f" AND option_id IN ({_placeholders(contract.option_ids)})"
        option_parameters.extend(contract.option_ids)
    option_source = connection.execute(
        f"""
        SELECT option_id, underlying_id, call_put, strike, expiry,
               exercise_style, settlement_type, contract_multiplier,
               bid, ask
        FROM authoring_parent.solver_visible.option_daily
        WHERE snapshot_id = ? AND date = ?
          AND underlying_id IN ({underlying_placeholders})
          {option_filter}
        ORDER BY underlying_id, expiry, strike, call_put, option_id
        """,
        option_parameters,
    ).fetchall()
    if not option_source:
        raise ValueError("public selection contains no live option quotes")
    selected_option_ids = tuple(sorted(str(row[0]) for row in option_source))
    if len(selected_option_ids) != len(set(selected_option_ids)):
        raise ValueError("selected option identifiers are not unique")
    if contract.option_ids is not None and selected_option_ids != contract.option_ids:
        raise ValueError("explicit option_ids did not resolve exactly in the sampled universe")
    quoted_underlyings = {str(row[1]) for row in option_source}
    if quoted_underlyings != set(selected_underlyings):
        raise ValueError("every sampled underlying must contribute live option quotes")
    contract_rows = [
        (child_snapshot_id, *row[:8])
        for row in option_source
    ]
    quote_rows = [
        (
            child_snapshot_id,
            contract.valuation_date,
            row[1],
            row[0],
            *row[8:],
        )
        for row in option_source
    ]

    raw_dependence = connection.execute(
        """
        SELECT dependence_spec_id, measure, source_dependence_spec_id,
               mapping_id, mapping_type, risk_neutral_measure_id,
               numeraire_id, rate_path_id, snapshot_id, driver_order,
               formulation, factor_loading_matrix, idiosyncratic_diagonal,
               correlation_matrix, matrix_dtype, factorization_method,
               factorization_order, time_grid, regime_id
        FROM authoring_parent.solver_visible.underlying_dependence
        WHERE snapshot_id = ?
        ORDER BY measure, dependence_spec_id
        """,
        [parent_snapshot_id],
    ).fetchall()
    dependence_rows = list(
        _project_dependence_rows(
            raw_dependence,
            child_snapshot_id=child_snapshot_id,
            sample_id=sample_id,
            selected_underlyings=selected_underlyings,
        )
    )
    q_row = next(row for row in dependence_rows if row[2] == "Q")

    pricing_source = connection.execute(
        f"""
        SELECT CAST(valuation_timestamp AT TIME ZONE 'UTC' AS DATE),
               underlying_id, currency, discount_curve, risk_free_rate,
               dividend_curve, dividend_yield, calendar, day_count
        FROM authoring_parent.solver_visible.pricing_metadata
        WHERE snapshot_id = ?
          AND CAST(valuation_timestamp AT TIME ZONE 'UTC' AS DATE) = ?
          AND underlying_id IN ({underlying_placeholders})
        ORDER BY underlying_id
        """,
        [parent_snapshot_id, contract.valuation_date, *selected_underlyings],
    ).fetchall()
    if len(pricing_source) != len(selected_underlyings):
        raise ValueError("valuation date lacks one pricing context per selected underlying")
    pricing_rows = [
        (
            child_snapshot_id,
            row[0],
            row[1],
            "Q",
            q_row[6],
            q_row[7],
            q_row[8],
            row[2],
            _canonical_json_text(row[3]),
            row[4],
            _canonical_json_text(row[5]),
            *row[6:],
        )
        for row in pricing_source
    ]
    return {
        "solver_visible.underlying_state": state_rows,
        "solver_visible.option_contracts": contract_rows,
        "solver_visible.option_chain_quotes": quote_rows,
        "solver_visible.pricing_context": pricing_rows,
        "solver_visible.underlying_dependence": dependence_rows,
    }


def _scan_projected_rows(rows_by_table: Mapping[str, Sequence[Sequence[Any]]]) -> None:
    contracts = {table.qualified_name: table for table in PUBLIC_TABLE_CONTRACTS}
    for table_name, rows in rows_by_table.items():
        table = contracts[table_name]
        for index, row in enumerate(rows):
            assert_no_private_leakage(
                dict(zip(table.column_names, row, strict=True)),
                f"{table_name}[{index}]",
            )


def _sort_projected_rows(
    table: PublicTableContract, rows: Sequence[Sequence[Any]]
) -> list[tuple[Any, ...]]:
    indices = tuple(table.column_names.index(column) for column in table.order_by)
    return sorted(
        (tuple(row) for row in rows),
        key=lambda row: tuple(row[index] for index in indices),
    )


def _insert_rows(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    rows: Sequence[Sequence[Any]],
) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in rows[0])
    connection.executemany(
        f"INSERT INTO {table_name} VALUES ({placeholders})",
        rows,
    )


def _create_public_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA metadata")
    connection.execute("CREATE SCHEMA solver_visible")
    for table in PUBLIC_TABLE_CONTRACTS:
        definitions = ", ".join(
            f"{column.name} {column.duckdb_type}"
            + (
                ""
                if (table.qualified_name, column.name) in _NULLABLE_PUBLIC_COLUMNS
                else " NOT NULL"
            )
            for column in table.columns
        )
        connection.execute(f"CREATE TABLE {table.qualified_name} ({definitions})")


def _validate_public_schema(connection: duckdb.DuckDBPyConnection) -> None:
    rows = connection.execute(
        """
        SELECT table_schema || '.' || table_name, table_type
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    actual = {str(row[0]): str(row[1]) for row in rows}
    expected = {table.qualified_name: "BASE TABLE" for table in PUBLIC_TABLE_CONTRACTS}
    if actual != expected:
        raise ValueError(
            f"public database relations differ from the allowlist: {sorted(actual)}"
        )
    market_schema_count = connection.execute(
        """
        SELECT count(*) FROM information_schema.schemata
        WHERE schema_name = 'market'
        """
    ).fetchone()[0]
    if market_schema_count:
        raise ValueError("public database must not contain a market schema")
    for table in PUBLIC_TABLE_CONTRACTS:
        described = connection.execute(
            f"DESCRIBE {table.qualified_name}"
        ).fetchall()
        actual_columns = tuple((str(row[0]), str(row[1])) for row in described)
        expected_columns = tuple(
            (column.name, column.duckdb_type) for column in table.columns
        )
        if actual_columns != expected_columns:
            raise ValueError(
                f"{table.qualified_name} column contract changed: {actual_columns}"
            )


def _scan_public_connection(connection: duckdb.DuckDBPyConnection) -> None:
    _validate_public_schema(connection)
    for table in PUBLIC_TABLE_CONTRACTS:
        order_by = ", ".join(table.order_by)
        rows = connection.execute(
            f"SELECT * FROM {table.qualified_name} ORDER BY {order_by}"
        ).fetchall()
        for index, row in enumerate(rows):
            assert_no_private_leakage(
                dict(zip(table.column_names, row, strict=True)),
                f"{table.qualified_name}[{index}]",
            )
    metadata = connection.execute(
        """
        SELECT schema_version, export_version, status, duckdb_version
        FROM metadata.public_task
        """
    ).fetchall()
    expected = [
        (
            PUBLIC_DATABASE_SCHEMA_VERSION,
            SOLVER_DATABASE_EXPORT_VERSION,
            "FROZEN",
            PINNED_DUCKDB_VERSION,
        )
    ]
    if metadata != expected:
        raise ValueError("public task identity/version contract is invalid")


def assert_public_database_safe(database: str | Path) -> None:
    """Verify the exact public allowlist and recursively scan every value."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"public database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _scan_public_connection(connection)
    finally:
        connection.close()


def canonical_logical_checksum(database: str | Path) -> str:
    """Hash all canonical public rows, independent of file layout and mtime."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"public database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _scan_public_connection(connection)
        hasher = sha256()
        hasher.update(f"{LOGICAL_CHECKSUM_CONTRACT_ID}\n".encode("utf-8"))
        for table in PUBLIC_TABLE_CONTRACTS:
            order_by = ", ".join(table.order_by)
            rows = connection.execute(
                f"SELECT * FROM {table.qualified_name} ORDER BY {order_by}"
            ).fetchall()
            json_columns = frozenset(
                column.name for column in table.columns if column.duckdb_type == "JSON"
            )
            typed_columns = tuple(
                f"{column.name}:{column.duckdb_type}" for column in table.columns
            )
            _hash_rows(
                hasher,
                table.qualified_name,
                table.column_names,
                rows,
                json_columns=json_columns,
                header_columns=typed_columns,
            )
        return hasher.hexdigest()
    finally:
        connection.close()


def export_solver_database(
    parent_database: str | Path,
    output_database: str | Path,
    contract: SolverDatabaseExportContract,
) -> PublicDatabaseManifest:
    """Atomically materialize one frozen public child from a frozen parent."""

    if duckdb.__version__ != PINNED_DUCKDB_VERSION:
        raise RuntimeError(
            f"export requires duckdb=={PINNED_DUCKDB_VERSION}, got {duckdb.__version__}"
        )
    parent_path = Path(parent_database)
    output_path = Path(output_database)
    if not parent_path.is_file():
        raise FileNotFoundError(f"authoring parent database is missing: {parent_path}")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite public database: {output_path}")
    if parent_path.resolve() == output_path.resolve():
        raise ValueError("public child must be a different file from its parent")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    parent_connection = duckdb.connect(str(parent_path), read_only=True)
    try:
        parent_snapshot_id, parent_revision, parent_status, parent_duckdb_version = (
            _single_parent_identity(parent_connection)
        )
        if parent_status != "FROZEN":
            raise ValueError("authoring parent snapshot must be FROZEN")
        if parent_duckdb_version != PINNED_DUCKDB_VERSION:
            raise ValueError("authoring parent uses a different DuckDB version")
        parent_checksum = _authoring_checksum_from_connection(
            parent_connection, parent_snapshot_id
        )
        universe = tuple(
            str(row[0])
            for row in parent_connection.execute(
                """
                SELECT DISTINCT underlying_id
                FROM solver_visible.underlying_daily
                WHERE snapshot_id = ?
                ORDER BY underlying_id
                """,
                [parent_snapshot_id],
            ).fetchall()
        )
    finally:
        parent_connection.close()
    selected = sample_underlyings(
        universe,
        sample_size=contract.sample_size,
        seed=contract.sampling_seed,
    )
    sample_id = stable_sample_id(
        parent_logical_checksum=parent_checksum,
        seed=contract.sampling_seed,
        underlying_ids=selected,
        variant_id=contract.variant_id,
    )

    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid4().hex}.tmp"
    )
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(temporary_path))
        connection.execute(
            f"ATTACH '{_sql_path(parent_path)}' AS authoring_parent (READ_ONLY)"
        )
        # Resolve options before task identity: explicit and implicit selectors
        # therefore share the same stable business-key identity.
        provisional_child_id = "PUBLIC-SNAPSHOT-PROVISIONAL"
        rows_by_table = _fetch_projection(
            connection,
            parent_snapshot_id=parent_snapshot_id,
            child_snapshot_id=provisional_child_id,
            selected_underlyings=selected,
            contract=contract,
            sample_id=sample_id,
        )
        option_ids = tuple(
            sorted(str(row[1]) for row in rows_by_table["solver_visible.option_contracts"])
        )
        task_id = stable_task_id(
            sample_id=sample_id,
            parent_snapshot_id=parent_snapshot_id,
            parent_snapshot_revision=parent_revision,
            variant_id=contract.variant_id,
            valuation_date=contract.valuation_date,
            option_ids=option_ids,
        )
        child_snapshot_id = (
            "SOLVER-PUBLIC-" + task_id.rsplit("-", 1)[-1].upper()
        )
        rows_by_table = {
            table_name: [(child_snapshot_id, *row[1:]) for row in rows]
            for table_name, rows in rows_by_table.items()
        }
        connection.execute("DETACH authoring_parent")
        _create_public_tables(connection)

        row_counts = {
            "metadata.public_task": 1,
            **{table: len(rows) for table, rows in rows_by_table.items()},
        }
        subset_manifest = {
            "manifest_version": "deterministic-public-subset-v1",
            "selection_rule": (
                "explicit-valuation-date-and-option-ids-v1"
                if contract.option_ids is not None
                else "explicit-valuation-date-all-live-options-v1"
            ),
            "selected_underlyings": list(selected),
            "valuation_date": contract.valuation_date.isoformat(),
            "selected_option_ids": list(option_ids),
            "table_row_counts": dict(sorted(row_counts.items())),
        }
        canonicalization = {
            "checksum_contract_id": LOGICAL_CHECKSUM_CONTRACT_ID,
            "decimal_storage": "DECIMAL(24,8)",
            "float_storage": "IEEE-754-binary64",
            "json_serialization": "UTF-8-sorted-keys-no-insignificant-whitespace",
            "materialization_timestamp_policy": "not-persisted",
            "row_order": {
                table.qualified_name: list(table.order_by)
                for table in PUBLIC_TABLE_CONTRACTS
            },
        }
        metadata_row = (
            PUBLIC_DATABASE_SCHEMA_VERSION,
            SOLVER_DATABASE_EXPORT_VERSION,
            task_id,
            sample_id,
            contract.variant_id,
            child_snapshot_id,
            1,
            "FROZEN",
            parent_snapshot_id,
            parent_revision,
            parent_checksum,
            SAMPLING_CONTRACT_ID,
            _canonical_json_text(subset_manifest),
            PINNED_DUCKDB_VERSION,
            _canonical_json_text(canonicalization),
        )
        rows_by_table = {
            "metadata.public_task": [metadata_row],
            **rows_by_table,
        }
        rows_by_table = {
            table.qualified_name: _sort_projected_rows(
                table, rows_by_table[table.qualified_name]
            )
            for table in PUBLIC_TABLE_CONTRACTS
        }
        _scan_projected_rows(rows_by_table)
        for table in PUBLIC_TABLE_CONTRACTS:
            _insert_rows(
                connection,
                table.qualified_name,
                rows_by_table[table.qualified_name],
            )
        connection.close()
        connection = None

        assert_public_database_safe(temporary_path)
        public_checksum = canonical_logical_checksum(temporary_path)
        manifest = PublicDatabaseManifest(
            task_id=task_id,
            sample_id=sample_id,
            child_snapshot_id=child_snapshot_id,
            parent_snapshot_id=parent_snapshot_id,
            parent_snapshot_revision=parent_revision,
            parent_logical_checksum=parent_checksum,
            public_logical_checksum=public_checksum,
            variant_id=contract.variant_id,
            valuation_date=contract.valuation_date,
            selected_underlyings=selected,
            selected_option_ids=option_ids,
            table_row_counts=row_counts,
        )
        manifest.to_dict()
        os.replace(temporary_path, output_path)
        return manifest
    except Exception:
        if connection is not None:
            connection.close()
        temporary_path.unlink(missing_ok=True)
        raise


@contextmanager
def open_solver_database(
    database: str | Path,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Hand the validated public child to a solver through a read-only handle."""

    path = Path(database)
    assert_public_database_safe(path)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


__all__ = [
    "assert_public_database_safe",
    "canonical_authoring_logical_checksum",
    "canonical_logical_checksum",
    "export_solver_database",
    "open_solver_database",
    "sample_underlyings",
    "stable_sample_id",
    "stable_task_id",
]
