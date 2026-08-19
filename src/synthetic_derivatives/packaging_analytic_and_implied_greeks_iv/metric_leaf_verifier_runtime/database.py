"""Shared database, dependency, and logical-identity runtime code."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

import duckdb

from .models import BSMMarketMetricInput
from .profiles import (
    MetricSpec,
    _LOGICAL_CHECKSUM_ID,
    _NUMERAIRE_ID,
    _RISK_NEUTRAL_MEASURE_ID,
    _SELECTION_POLICY_ID,
    _TASK_FAMILY,
    _spec_from_config,
)


_PINNED_DUCKDB_VERSION = "1.5.5"


@dataclass(frozen=True)
class _Column:
    name: str
    duckdb_type: str


@dataclass(frozen=True)
class _Table:
    name: str
    columns: tuple[_Column, ...]
    order_by: tuple[str, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


_PUBLIC_TABLES = (
    _Table(
        "metadata.public_task",
        tuple(
            _Column(name, duckdb_type)
            for name, duckdb_type in (
                ("schema_version", "VARCHAR"),
                ("task_id", "VARCHAR"),
                ("task_family", "VARCHAR"),
                ("task_version", "VARCHAR"),
                ("variant_id", "VARCHAR"),
                ("snapshot_id", "VARCHAR"),
                ("snapshot_revision", "INTEGER"),
                ("status", "VARCHAR"),
                ("valuation_date", "DATE"),
                ("currency", "VARCHAR"),
                ("underlying_count", "INTEGER"),
                ("option_row_count", "INTEGER"),
                ("joint_market_contract_id", "VARCHAR"),
                ("p_dependence_spec_id", "VARCHAR"),
                ("q_dependence_spec_id", "VARCHAR"),
                ("dependence_policy_id", "VARCHAR"),
                ("risk_neutral_measure_id", "VARCHAR"),
                ("numeraire_id", "VARCHAR"),
                ("rate_path_id", "VARCHAR"),
                ("selection_policy_id", "VARCHAR"),
            )
        ),
        ("task_id",),
    ),
    _Table(
        "solver_visible.underlying_market_inputs",
        tuple(
            _Column(name, duckdb_type)
            for name, duckdb_type in (
                ("task_id", "VARCHAR"),
                ("snapshot_id", "VARCHAR"),
                ("valuation_date", "DATE"),
                ("underlying_id", "VARCHAR"),
                ("spot", "DECIMAL(24,8)"),
                ("currency", "VARCHAR"),
                ("risk_free_rate", "DOUBLE"),
                ("dividend_yield", "DOUBLE"),
                ("calendar", "VARCHAR"),
                ("day_count", "VARCHAR"),
            )
        ),
        ("valuation_date", "underlying_id"),
    ),
    _Table(
        "solver_visible.option_quote_inputs",
        tuple(
            _Column(name, duckdb_type)
            for name, duckdb_type in (
                ("row_id", "VARCHAR"),
                ("task_id", "VARCHAR"),
                ("snapshot_id", "VARCHAR"),
                ("valuation_date", "DATE"),
                ("underlying_id", "VARCHAR"),
                ("option_id", "VARCHAR"),
                ("call_put", "VARCHAR"),
                ("strike", "DECIMAL(24,8)"),
                ("expiry", "DATE"),
                ("time_to_expiry_actual365", "DOUBLE"),
                ("bid", "DECIMAL(24,8)"),
                ("ask", "DECIMAL(24,8)"),
                ("contract_multiplier", "DECIMAL(24,8)"),
                ("exercise_style", "VARCHAR"),
                ("settlement_type", "VARCHAR"),
            )
        ),
        (
            "valuation_date",
            "underlying_id",
            "expiry",
            "strike",
            "call_put",
            "option_id",
        ),
    ),
)
_PUBLIC_JOIN_FIELDS = ("task_id", "snapshot_id", "valuation_date", "underlying_id")


def _require_pinned_duckdb() -> None:
    if duckdb.__version__ != _PINNED_DUCKDB_VERSION:
        raise RuntimeError(
            f"trusted package verifier requires duckdb=={_PINNED_DUCKDB_VERSION}, "
            f"found {duckdb.__version__}"
        )


def _canonical_json_text(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest_file(path: str | Path) -> str:
    """Return a frozen SHA-256 file identity."""

    return sha256(Path(path).read_bytes()).hexdigest()


def _validate_schema(connection: duckdb.DuckDBPyConnection) -> None:
    actual = connection.execute(
        """
        SELECT table_schema || '.' || table_name, table_type
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    expected = sorted((table.name, "BASE TABLE") for table in _PUBLIC_TABLES)
    if actual != expected:
        raise ValueError("single-metric database relations differ from the allowlist")
    if connection.execute(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'market'"
    ).fetchone()[0]:
        raise ValueError("single-metric database must not contain a market schema")
    for table in _PUBLIC_TABLES:
        actual_columns = tuple(
            (str(row[0]), str(row[1]))
            for row in connection.execute(f"DESCRIBE {table.name}").fetchall()
        )
        expected_columns = tuple(
            (column.name, column.duckdb_type) for column in table.columns
        )
        if actual_columns != expected_columns:
            raise ValueError(f"{table.name} column contract changed")


def _table_mappings(
    connection: duckdb.DuckDBPyConnection, table: _Table
) -> tuple[dict[str, Any], ...]:
    rows = connection.execute(
        f"SELECT * FROM {table.name} ORDER BY {','.join(table.order_by)}"
    ).fetchall()
    return tuple(dict(zip(table.column_names, row, strict=True)) for row in rows)


def _load_inputs_from_connection(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[BSMMarketMetricInput, ...]:
    underlying_rows = _table_mappings(connection, _PUBLIC_TABLES[1])
    option_rows = _table_mappings(connection, _PUBLIC_TABLES[2])
    if len(underlying_rows) != 8 or len(option_rows) != 160:
        raise ValueError("single-metric database has invalid public row counts")
    underlying_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in underlying_rows:
        key = tuple(row[field] for field in _PUBLIC_JOIN_FIELDS)
        if key in underlying_by_key:
            raise ValueError("underlying-market join key is duplicated")
        underlying_by_key[key] = row
    joined = []
    for option in option_rows:
        key = tuple(option[field] for field in _PUBLIC_JOIN_FIELDS)
        underlying = underlying_by_key.get(key)
        if underlying is None:
            raise ValueError("option row has no underlying-market match")
        mapping = dict(underlying)
        mapping.update(option)
        joined.append(BSMMarketMetricInput.from_mapping(mapping))
    return tuple(joined)


def _validate_public_connection(
    connection: duckdb.DuckDBPyConnection, spec: MetricSpec
) -> tuple[BSMMarketMetricInput, ...]:
    _validate_schema(connection)
    metadata = connection.execute("SELECT * FROM metadata.public_task").fetchall()
    if len(metadata) != 1:
        raise ValueError("single-metric database requires one metadata row")
    meta = metadata[0]
    if (
        meta[0] != spec.database_schema_version
        or not isinstance(meta[1], str)
        or re.fullmatch(spec.task_id_pattern, meta[1]) is None
        or meta[2:5] != (_TASK_FAMILY, spec.task_version, spec.variant_id)
        or not isinstance(meta[5], str)
        or not meta[5]
        or meta[6:8] != (1, "FROZEN")
        or type(meta[8]) is not date
        or meta[9:12] != ("USD", 8, 160)
        or any(not isinstance(value, str) or not value for value in meta[12:16])
        or meta[16] != _RISK_NEUTRAL_MEASURE_ID
        or meta[17] != _NUMERAIRE_ID
        or not isinstance(meta[18], str)
        or not meta[18]
        or meta[19] != _SELECTION_POLICY_ID
    ):
        raise ValueError("single-metric metadata identity is invalid")

    rows = _load_inputs_from_connection(connection)
    if len({row.underlying_id for row in rows}) != 8:
        raise ValueError("single-metric database does not contain the golden grid")
    if any(
        row.task_id != meta[1]
        or row.snapshot_id != meta[5]
        or row.valuation_date != meta[8]
        for row in rows
    ):
        raise ValueError("single-metric inputs differ from the metadata identity")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, 161))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("single-metric input row IDs are not canonical")

    groups: dict[tuple[str, date, Decimal], set[str]] = defaultdict(set)
    expiries: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        groups[(row.underlying_id, row.expiry, Decimal(str(row.strike)))].add(
            row.call_put
        )
        expiries[row.underlying_id].add(row.expiry)
    if len(groups) != 80 or any(pair != {"call", "put"} for pair in groups.values()):
        raise ValueError("single-metric grid is not five paired strikes per expiry")
    if any(len(items) != 2 for items in expiries.values()):
        raise ValueError("single-metric grid does not have two expiries per underlying")
    return rows


def load_bsm_market_metric_inputs(
    database: str | Path,
    method_config: Mapping[str, Any],
) -> tuple[BSMMarketMetricInput, ...]:
    """Load and validate all public rows for the allowlisted target config."""

    _require_pinned_duckdb()
    spec = _spec_from_config(method_config)
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"single-metric database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return _validate_public_connection(connection, spec)
    finally:
        connection.close()


def _hash_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"decimal": format(value, "f")}
    if type(value) is date:
        return {"date": value.isoformat()}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("logical checksum cannot contain non-finite values")
        return {"float64": format(value, ".17g")}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported logical checksum value: {type(value).__name__}")


def bsm_metric_logical_checksum(
    database: str | Path, method_config: Mapping[str, Any]
) -> str:
    """Validate and hash the canonical logical rows of a derived metric DB."""

    _require_pinned_duckdb()
    spec = _spec_from_config(method_config)
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"single-metric database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection, spec)
        hasher = sha256()
        hasher.update(f"{_LOGICAL_CHECKSUM_ID}\n".encode("utf-8"))
        for table in _PUBLIC_TABLES:
            header = {
                "table": table.name,
                "columns": [
                    f"{column.name}:{column.duckdb_type}" for column in table.columns
                ],
            }
            hasher.update(_canonical_json_text(header).encode("utf-8") + b"\n")
            rows = connection.execute(
                f"SELECT * FROM {table.name} ORDER BY {','.join(table.order_by)}"
            ).fetchall()
            for row in rows:
                hasher.update(
                    _canonical_json_text([_hash_value(value) for value in row]).encode(
                        "utf-8"
                    )
                    + b"\n"
                )
        return hasher.hexdigest()
    finally:
        connection.close()


__all__ = ["bsm_metric_logical_checksum", "digest_file", "load_bsm_market_metric_inputs"]
