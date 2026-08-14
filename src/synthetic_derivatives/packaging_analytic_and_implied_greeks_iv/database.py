"""Materialize the three-relation public database for one BSM Greeks task."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import duckdb

from synthetic_derivatives.export import (
    PublicDatabaseManifest,
    assert_public_database_safe,
)
from synthetic_derivatives.export.contracts import (
    PINNED_DUCKDB_VERSION,
    assert_no_private_leakage,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_VARIANT_ID,
    LOGICAL_CHECKSUM_ID,
    PACKAGE_SCHEMA_VERSION,
    SELECTION_POLICY_ID,
    BSM_MARKET_GREEKS_TASK_VERSION,
    canonical_json_bytes,
    canonical_json_text,
    digest_file,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    MetricSpec,
    is_registered_metric_spec,
)
from synthetic_derivatives.solver.analytic_and_implied_greeks_iv.bsm import (
    bsm_analytic_values,
)
from synthetic_derivatives.solver.analytic_and_implied_greeks_iv.bsm_market_greeks import (
    solve_market_greeks_submission,
    solve_market_implied_root,
)
from synthetic_derivatives.tasks.bsm_market_greeks import BSMMarketGreeksInput
from synthetic_derivatives.tasks.bsm_greeks import (
    NUMERAIRE_ID,
    RISK_NEUTRAL_MEASURE_ID,
)
from synthetic_derivatives.verifier.bsm_market_greeks import (
    trusted_market_implied_root,
    trusted_market_greeks_submission,
)
from synthetic_derivatives.verifier.bsm_greeks import trusted_quantlib_values


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


BSM_GREEKS_PUBLIC_TABLES = (
    _Table(
        "metadata.public_task",
        (
            _Column("schema_version", "VARCHAR"),
            _Column("task_id", "VARCHAR"),
            _Column("task_family", "VARCHAR"),
            _Column("task_version", "VARCHAR"),
            _Column("variant_id", "VARCHAR"),
            _Column("snapshot_id", "VARCHAR"),
            _Column("snapshot_revision", "INTEGER"),
            _Column("status", "VARCHAR"),
            _Column("valuation_date", "DATE"),
            _Column("currency", "VARCHAR"),
            _Column("underlying_count", "INTEGER"),
            _Column("option_row_count", "INTEGER"),
            _Column("joint_market_contract_id", "VARCHAR"),
            _Column("p_dependence_spec_id", "VARCHAR"),
            _Column("q_dependence_spec_id", "VARCHAR"),
            _Column("dependence_policy_id", "VARCHAR"),
            _Column("risk_neutral_measure_id", "VARCHAR"),
            _Column("numeraire_id", "VARCHAR"),
            _Column("rate_path_id", "VARCHAR"),
            _Column("selection_policy_id", "VARCHAR"),
        ),
        ("task_id",),
    ),
    _Table(
        "solver_visible.underlying_market_inputs",
        (
            _Column("task_id", "VARCHAR"),
            _Column("snapshot_id", "VARCHAR"),
            _Column("valuation_date", "DATE"),
            _Column("underlying_id", "VARCHAR"),
            _Column("spot", "DECIMAL(24,8)"),
            _Column("currency", "VARCHAR"),
            _Column("risk_free_rate", "DOUBLE"),
            _Column("dividend_yield", "DOUBLE"),
            _Column("calendar", "VARCHAR"),
            _Column("day_count", "VARCHAR"),
        ),
        (
            "valuation_date",
            "underlying_id",
        ),
    ),
    _Table(
        "solver_visible.option_quote_inputs",
        (
            _Column("row_id", "VARCHAR"),
            _Column("task_id", "VARCHAR"),
            _Column("snapshot_id", "VARCHAR"),
            _Column("valuation_date", "DATE"),
            _Column("underlying_id", "VARCHAR"),
            _Column("option_id", "VARCHAR"),
            _Column("call_put", "VARCHAR"),
            _Column("strike", "DECIMAL(24,8)"),
            _Column("expiry", "DATE"),
            _Column("time_to_expiry_actual365", "DOUBLE"),
            _Column("bid", "DECIMAL(24,8)"),
            _Column("ask", "DECIMAL(24,8)"),
            _Column("contract_multiplier", "DECIMAL(24,8)"),
            _Column("exercise_style", "VARCHAR"),
            _Column("settlement_type", "VARCHAR"),
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

UNDERLYING_MARKET_FIELDS = BSM_GREEKS_PUBLIC_TABLES[1].column_names
OPTION_QUOTE_FIELDS = BSM_GREEKS_PUBLIC_TABLES[2].column_names
PUBLIC_JOIN_FIELDS = (
    "task_id",
    "snapshot_id",
    "valuation_date",
    "underlying_id",
)


@dataclass(frozen=True)
class BSMGreeksDatabaseManifest:
    task_id: str
    child_snapshot_id: str
    parent_snapshot_id: str
    parent_snapshot_revision: int
    parent_logical_checksum: str
    public_logical_checksum: str
    valuation_date: date
    selected_underlyings: tuple[str, ...]
    selected_option_ids: tuple[str, ...]
    joint_market_contract_id: str
    p_dependence_spec_id: str
    q_dependence_spec_id: str
    dependence_policy_id: str
    risk_neutral_measure_id: str
    numeraire_id: str
    rate_path_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION,
            "task_id": self.task_id,
            "child_snapshot_id": self.child_snapshot_id,
            "snapshot_revision": 1,
            "parent_snapshot_id": self.parent_snapshot_id,
            "parent_snapshot_revision": self.parent_snapshot_revision,
            "parent_logical_checksum": self.parent_logical_checksum,
            "public_logical_checksum": self.public_logical_checksum,
            "valuation_date": self.valuation_date.isoformat(),
            "selected_underlyings": list(self.selected_underlyings),
            "selected_option_ids": list(self.selected_option_ids),
            "joint_market_contract_id": self.joint_market_contract_id,
            "p_dependence_spec_id": self.p_dependence_spec_id,
            "q_dependence_spec_id": self.q_dependence_spec_id,
            "dependence_policy_id": self.dependence_policy_id,
            "risk_neutral_measure_id": self.risk_neutral_measure_id,
            "numeraire_id": self.numeraire_id,
            "rate_path_id": self.rate_path_id,
        }


@dataclass(frozen=True)
class BSMMetricDatabaseProjection:
    """Frozen identities produced by one metric-specific DB projection."""

    target: str
    derived_task_id: str
    derived_snapshot_id: str
    source_database_file_digest: str
    derived_database_file_digest: str
    source_market_content_digest: str
    derived_market_content_digest: str
    derived_logical_checksum: str


def stable_package_task_id(
    *,
    parent_logical_checksum: str,
    valuation_date: date,
    selected_underlyings: Sequence[str],
    solver_interface_digest: str,
) -> str:
    """Implement the frozen public-safe stable identity formula."""

    ordered = tuple(str(item) for item in selected_underlyings)
    if ordered != tuple(sorted(ordered)) or len(ordered) != len(set(ordered)):
        raise ValueError("selected underlyings must be unique and canonically ordered")
    if (
        not isinstance(solver_interface_digest, str)
        or len(solver_interface_digest) != 64
        or any(character not in "0123456789abcdef" for character in solver_interface_digest)
    ):
        raise ValueError("solver interface digest must be lowercase SHA-256")
    payload = "|".join(
        (
            parent_logical_checksum,
            BSM_MARKET_GREEKS_VARIANT_ID,
            valuation_date.isoformat(),
            canonical_json_text(list(ordered)),
            BSM_MARKET_GREEKS_METHOD_ID,
            solver_interface_digest,
            PACKAGE_SCHEMA_VERSION,
        )
    )
    return "bsm-mig-v2-" + sha256(payload.encode("utf-8")).hexdigest()[:24]


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _validate_joint_provenance(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[str, str, str, str, str, str, str]:
    rows = connection.execute(
        """
        SELECT dependence_spec_id, measure, source_dependence_spec_id,
               mapping_type, risk_neutral_measure_id, numeraire_id,
               rate_path_id, driver_order, factor_loading_matrix,
               idiosyncratic_diagonal, correlation_matrix
        FROM solver_visible.underlying_dependence
        ORDER BY measure, dependence_spec_id
        """
    ).fetchall()
    if len(rows) != 2 or {str(row[1]) for row in rows} != {"P", "Q"}:
        raise ValueError("public market child must contain one P/Q dependence pair")
    by_measure = {str(row[1]): row for row in rows}
    physical = by_measure["P"]
    pricing = by_measure["Q"]
    if pricing[2] != physical[0]:
        raise ValueError("Q dependence does not identify its public P source")
    if any(pricing[index] is None for index in (3, 4, 5, 6)):
        raise ValueError("Q dependence lacks common pricing identities")
    drivers = _json(pricing[7])
    loadings = _json(pricing[8])
    diagonal = _json(pricing[9])
    correlation = _json(pricing[10])
    if not (
        isinstance(drivers, list)
        and isinstance(loadings, list)
        and isinstance(diagonal, list)
        and isinstance(correlation, list)
        and len(drivers) == len(loadings) == len(diagonal) == len(correlation) == 8
    ):
        raise ValueError("Q dependence dimensions do not match eight underlyings")
    reconstructed = [
        [
            sum(
                float(left) * float(right)
                for left, right in zip(loadings[row], loadings[column], strict=True)
            )
            + (float(diagonal[row]) if row == column else 0.0)
            for column in range(8)
        ]
        for row in range(8)
    ]
    if reconstructed != correlation:
        raise ValueError("Q correlation is not the declared factor-loading covariance")
    if not any(
        abs(float(correlation[row][column])) > 0.0
        for row in range(8)
        for column in range(row)
    ):
        raise ValueError("golden task requires legal non-diagonal Q dependence")
    context = connection.execute(
        """
        SELECT DISTINCT risk_neutral_measure_id, numeraire_id, rate_path_id,
                        currency
        FROM solver_visible.pricing_context
        ORDER BY 1, 2, 3, 4
        """
    ).fetchall()
    if len(context) != 1 or tuple(context[0][:3]) != tuple(pricing[4:7]):
        raise ValueError("pricing contexts do not share the Q dependence identities")
    common = tuple(str(item) for item in context[0])
    joint_payload = canonical_json_text(
        {
            "currency": common[3],
            "risk_neutral_measure_id": common[0],
            "numeraire_id": common[1],
            "rate_path_id": common[2],
            "valuation_dates": [
                row[0].isoformat()
                for row in connection.execute(
                    "SELECT DISTINCT valuation_date FROM solver_visible.pricing_context ORDER BY 1"
                ).fetchall()
            ],
        }
    )
    joint_id = "same-currency-common-q-context-v1-" + sha256(
        joint_payload.encode("utf-8")
    ).hexdigest()[:20]
    return (
        str(physical[0]),
        str(pricing[0]),
        str(pricing[3]),
        common[0],
        common[1],
        common[2],
        joint_id,
    )


def _select_option_ids(
    connection: duckdb.DuckDBPyConnection,
    valuation_date: date,
) -> tuple[str, ...]:
    contexts = {
        str(row[0]): (Decimal(row[1]), float(row[2]), float(row[3]))
        for row in connection.execute(
            """
            SELECT state.underlying_id, state.spot_close,
                   context.risk_free_rate, context.dividend_yield
            FROM solver_visible.underlying_state AS state
            JOIN solver_visible.pricing_context AS context
              USING (snapshot_id, valuation_date, underlying_id)
            WHERE state.valuation_date = ?
              AND state.measure = 'P' AND context.measure = 'Q'
            ORDER BY state.underlying_id
            """,
            [valuation_date],
        ).fetchall()
    }
    if len(contexts) != 8:
        raise ValueError("golden selector requires eight complete underlying contexts")
    option_rows = connection.execute(
        """
        SELECT contract.underlying_id, contract.expiry, contract.strike,
               contract.call_put, contract.option_id
        FROM solver_visible.option_contracts AS contract
        JOIN solver_visible.option_chain_quotes AS quote
          USING (snapshot_id, underlying_id, option_id)
        WHERE quote.valuation_date = ? AND contract.expiry > ?
        ORDER BY contract.underlying_id, contract.expiry, contract.strike,
                 contract.call_put, contract.option_id
        """,
        [valuation_date, valuation_date],
    ).fetchall()
    grouped: dict[tuple[str, date, Decimal], list[tuple[str, str]]] = defaultdict(list)
    expiries: dict[str, set[date]] = defaultdict(set)
    for underlying_id, expiry, strike, call_put, option_id in option_rows:
        key = (str(underlying_id), expiry, Decimal(str(strike)))
        grouped[key].append((str(call_put), str(option_id)))
        expiries[str(underlying_id)].add(expiry)
    selected: list[str] = []
    for underlying_id in sorted(contexts):
        selected_expiries = sorted(expiries[underlying_id])[:2]
        if len(selected_expiries) != 2:
            raise ValueError("each underlying must have two live expiries")
        spot, rate, dividend = contexts[underlying_id]
        for expiry in selected_expiries:
            tau = (expiry - valuation_date).days / 365.0
            forward = float(spot) * math.exp((rate - dividend) * tau)
            candidates = []
            for key, pairs in grouped.items():
                if key[:2] != (underlying_id, expiry):
                    continue
                if sorted(pair[0] for pair in pairs) != ["call", "put"]:
                    raise ValueError("selected strike does not have one call/put pair")
                strike = key[2]
                candidates.append(
                    (abs(math.log(float(strike) / forward)), strike, pairs)
                )
            nearest = sorted(candidates, key=lambda item: (item[0], item[1]))[:5]
            if len(nearest) != 5:
                raise ValueError("each selected expiry must have five strikes")
            for _, _, pairs in nearest:
                selected.extend(option_id for _, option_id in pairs)
    if len(selected) != 160 or len(selected) != len(set(selected)):
        raise ValueError("golden selector must produce exactly 160 unique rows")
    return tuple(sorted(selected))


def _fetch_task_inputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    option_ids: Sequence[str],
    task_id: str,
    child_snapshot_id: str,
    valuation_date: date,
) -> tuple[BSMMarketGreeksInput, ...]:
    placeholders = ",".join("?" for _ in option_ids)
    source = connection.execute(
        f"""
        SELECT quote.valuation_date, contract.underlying_id,
               contract.option_id, contract.call_put, state.spot_close,
               contract.strike, contract.expiry, quote.bid, quote.ask,
               contract.contract_multiplier, context.currency,
               context.risk_free_rate, context.dividend_yield,
               context.calendar, context.day_count, contract.exercise_style,
               contract.settlement_type
        FROM solver_visible.option_contracts AS contract
        JOIN solver_visible.option_chain_quotes AS quote
          USING (snapshot_id, underlying_id, option_id)
        JOIN solver_visible.underlying_state AS state
          USING (snapshot_id, valuation_date, underlying_id)
        JOIN solver_visible.pricing_context AS context
          USING (snapshot_id, valuation_date, underlying_id)
        WHERE quote.valuation_date = ?
          AND contract.option_id IN ({placeholders})
        ORDER BY quote.valuation_date, contract.underlying_id,
                 contract.expiry, contract.strike, contract.call_put,
                 contract.option_id
        """,
        [valuation_date, *option_ids],
    ).fetchall()
    if len(source) != len(option_ids):
        raise ValueError("selected option identifiers did not resolve exactly")
    rows = []
    for index, row in enumerate(source, start=1):
        expiry = row[6]
        rows.append(
            BSMMarketGreeksInput(
                row_id=f"row_{index:06d}",
                task_id=task_id,
                snapshot_id=child_snapshot_id,
                valuation_date=row[0],
                underlying_id=str(row[1]),
                option_id=str(row[2]),
                call_put=str(row[3]),
                spot=row[4],
                strike=row[5],
                expiry=expiry,
                time_to_expiry_actual365=(expiry - row[0]).days / 365.0,
                bid=row[7],
                ask=row[8],
                contract_multiplier=row[9],
                currency=str(row[10]),
                risk_free_rate=float(row[11]),
                dividend_yield=float(row[12]),
                calendar=str(row[13]),
                day_count=str(row[14]),
                exercise_style=str(row[15]),
                settlement_type=str(row[16]),
            )
        )
    return tuple(rows)


def split_bsm_greeks_query_rows(
    rows: Sequence[BSMMarketGreeksInput],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Project internal joined rows into the two solver-visible query payloads."""

    underlying_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    options: list[dict[str, Any]] = []
    for row in rows:
        mapping = row.to_tool_mapping()
        key = tuple(mapping[field] for field in PUBLIC_JOIN_FIELDS)
        underlying = {field: mapping[field] for field in UNDERLYING_MARKET_FIELDS}
        existing = underlying_by_key.setdefault(key, underlying)
        if existing != underlying:
            raise ValueError("option rows disagree on one underlying pricing context")
        options.append({field: mapping[field] for field in OPTION_QUOTE_FIELDS})
    underlyings = tuple(
        sorted(
            underlying_by_key.values(),
            key=lambda item: (
                item["valuation_date"],
                item["underlying_id"],
            ),
        )
    )
    if len(underlyings) != 8 or len(options) != 160:
        raise ValueError("public query projections require 8 underlying and 160 option rows")
    return underlyings, tuple(options)


def join_bsm_greeks_query_rows(
    underlying_rows: Sequence[Mapping[str, Any]],
    option_rows: Sequence[Mapping[str, Any]],
) -> tuple[BSMMarketGreeksInput, ...]:
    """Join the two public query results with the declared four-field key."""

    by_key: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in underlying_rows:
        if set(row) != set(UNDERLYING_MARKET_FIELDS):
            raise ValueError("underlying-market row has missing or extra fields")
        key = tuple(row[field] for field in PUBLIC_JOIN_FIELDS)
        if key in by_key:
            raise ValueError("underlying-market join key is duplicated")
        by_key[key] = row
    if len(by_key) != 8:
        raise ValueError("underlying-market query must contain exactly eight rows")
    joined: list[BSMMarketGreeksInput] = []
    for option in option_rows:
        if set(option) != set(OPTION_QUOTE_FIELDS):
            raise ValueError("option-quote row has missing or extra fields")
        key = tuple(option[field] for field in PUBLIC_JOIN_FIELDS)
        underlying = by_key.get(key)
        if underlying is None:
            raise ValueError("option row has no unique underlying-market match")
        combined = dict(underlying)
        combined.update(option)
        for field in ("spot", "strike", "bid", "ask", "contract_multiplier"):
            combined[field] = Decimal(str(combined[field]))
        joined.append(BSMMarketGreeksInput.from_mapping(combined))
    if len(joined) != 160:
        raise ValueError("option-quote query must contain exactly 160 rows")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, 161))
    if tuple(row.row_id for row in joined) != expected_ids:
        raise ValueError("option-query row order is not canonical")
    if tuple(row.canonical_order_key for row in joined) != tuple(
        sorted(row.canonical_order_key for row in joined)
    ):
        raise ValueError("option-query rows are not in canonical submission order")
    return tuple(joined)


_ROUNDING_QUANTUM = Decimal("0.00000001")
_ROUNDING_BOUNDARY_GUARD = Decimal("0.00000000001")


def _near_rounding_half_boundary(value: float) -> bool:
    absolute = abs(Decimal(str(value)))
    quantum_index = (absolute / _ROUNDING_QUANTUM).to_integral_value(
        rounding=ROUND_FLOOR
    )
    half_boundary = (quantum_index + Decimal("0.5")) * _ROUNDING_QUANTUM
    return abs(absolute - half_boundary) <= _ROUNDING_BOUNDARY_GUARD


def _run_publication_gates(rows: Sequence[BSMMarketGreeksInput]) -> None:
    solver = solve_market_greeks_submission(rows)
    trusted = trusted_market_greeks_submission(rows)
    if solver.to_dict() != trusted.to_dict():
        raise ValueError("stdlib and QuantLib canonical answers differ")
    for row in rows:
        solver_sigma = solve_market_implied_root(row)
        solver_values = bsm_analytic_values(row.as_greeks_input(solver_sigma))
        trusted_sigma = trusted_market_implied_root(row)
        trusted_values = trusted_quantlib_values(
            row.as_greeks_input(trusted_sigma)
        )
        if any(
            _near_rounding_half_boundary(value)
            for value in (
                solver_sigma,
                solver_values.delta,
                solver_values.gamma,
                solver_values.vega,
                solver_values.theta,
                solver_values.rho,
                trusted_sigma,
                trusted_values.delta,
                trusted_values.gamma,
                trusted_values.vega,
                trusted_values.theta,
                trusted_values.rho,
            )
        ):
            raise ValueError("selected row is too close to a decimal rounding boundary")


def _create_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA metadata")
    connection.execute("CREATE SCHEMA solver_visible")
    for table in BSM_GREEKS_PUBLIC_TABLES:
        columns = ", ".join(
            f"{column.name} {column.duckdb_type} NOT NULL"
            for column in table.columns
        )
        connection.execute(f"CREATE TABLE {table.name} ({columns})")


def _insert(
    connection: duckdb.DuckDBPyConnection,
    table: _Table,
    rows: Sequence[Sequence[Any]],
) -> None:
    placeholders = ",".join("?" for _ in table.columns)
    connection.executemany(f"INSERT INTO {table.name} VALUES ({placeholders})", rows)


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


def _validate_schema(connection: duckdb.DuckDBPyConnection) -> None:
    actual = connection.execute(
        """
        SELECT table_schema || '.' || table_name, table_type
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    expected = sorted((table.name, "BASE TABLE") for table in BSM_GREEKS_PUBLIC_TABLES)
    if actual != expected:
        raise ValueError("BSM Greeks database relations differ from the allowlist")
    if connection.execute(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'market'"
    ).fetchone()[0]:
        raise ValueError("BSM Greeks database must not contain a market schema")
    for table in BSM_GREEKS_PUBLIC_TABLES:
        actual_columns = tuple(
            (str(row[0]), str(row[1]))
            for row in connection.execute(f"DESCRIBE {table.name}").fetchall()
        )
        expected_columns = tuple(
            (column.name, column.duckdb_type) for column in table.columns
        )
        if actual_columns != expected_columns:
            raise ValueError(f"{table.name} column contract changed")


def _load_single_metadata_row(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[Any, ...]:
    metadata = connection.execute("SELECT * FROM metadata.public_task").fetchall()
    if len(metadata) != 1:
        raise ValueError("BSM Greeks database requires one metadata row")
    return metadata[0]


def _validate_market_grid(
    connection: duckdb.DuckDBPyConnection,
    meta: Sequence[Any],
    *,
    metric_identity: bool = False,
) -> None:
    if metric_identity:
        underlying_rows = _load_table_mappings(
            connection, BSM_GREEKS_PUBLIC_TABLES[1]
        )
        option_rows = _load_table_mappings(connection, BSM_GREEKS_PUBLIC_TABLES[2])
        for table_rows in (underlying_rows, option_rows):
            if any(
                row["task_id"] != meta[1]
                or row["snapshot_id"] != meta[5]
                or row["valuation_date"] != meta[8]
                for row in table_rows
            ):
                raise ValueError(
                    "BSM Greeks public inputs differ from metadata identity"
                )
        validation_task_id = "bsm-mig-v2-" + "0" * 24
        normalized_underlyings = tuple(
            {**row, "task_id": validation_task_id} for row in underlying_rows
        )
        normalized_options = tuple(
            {**row, "task_id": validation_task_id} for row in option_rows
        )
        rows = join_bsm_greeks_query_rows(
            normalized_underlyings, normalized_options
        )
    else:
        rows = load_bsm_greeks_inputs_from_connection(connection)
    if len(rows) != 160 or len({row.underlying_id for row in rows}) != 8:
        raise ValueError("BSM Greeks database does not contain the golden grid")
    groups: dict[tuple[str, date, Decimal], set[str]] = defaultdict(set)
    expiries: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        groups[(row.underlying_id, row.expiry, Decimal(str(row.strike)))].add(
            row.call_put
        )
        expiries[row.underlying_id].add(row.expiry)
    if any(pair != {"call", "put"} for pair in groups.values()):
        raise ValueError("BSM Greeks grid is not call/put paired")
    if any(len(items) != 2 for items in expiries.values()):
        raise ValueError("BSM Greeks grid does not have two expiries per underlying")
    if len(groups) != 80:
        raise ValueError("BSM Greeks grid does not have five strikes per expiry")
    if not metric_identity and any(
        row.task_id != meta[1]
        or row.snapshot_id != meta[5]
        or row.valuation_date != meta[8]
        for row in rows
    ):
        raise ValueError("BSM Greeks public inputs differ from metadata identity")
    for table in BSM_GREEKS_PUBLIC_TABLES:
        order = ",".join(table.order_by)
        for index, row in enumerate(
            connection.execute(f"SELECT * FROM {table.name} ORDER BY {order}").fetchall()
        ):
            assert_no_private_leakage(
                dict(zip(table.column_names, row, strict=True)),
                f"{table.name}[{index}]",
            )


def _validate_public_connection(connection: duckdb.DuckDBPyConnection) -> None:
    _validate_schema(connection)
    meta = _load_single_metadata_row(connection)
    if (
        meta[0] != BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION
        or meta[2:5] != (
            "bsm_greeks",
            BSM_MARKET_GREEKS_TASK_VERSION,
            BSM_MARKET_GREEKS_VARIANT_ID,
        )
        or meta[7] != "FROZEN"
        or meta[9:12] != ("USD", 8, 160)
        or meta[19] != SELECTION_POLICY_ID
    ):
        raise ValueError("BSM Greeks metadata identity is invalid")
    _validate_market_grid(connection, meta)


def _validate_metric_connection(
    connection: duckdb.DuckDBPyConnection,
    metric_spec: MetricSpec,
    *,
    expected_task_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> None:
    if not is_registered_metric_spec(metric_spec):
        raise ValueError("metric spec must be an exact frozen registry entry")
    _validate_schema(connection)
    meta = _load_single_metadata_row(connection)
    task_id = str(meta[1])
    snapshot_id = str(meta[5])
    if (
        meta[0] != metric_spec.database_schema_version
        or meta[2:5]
        != (
            "bsm_greeks",
            metric_spec.task_version,
            metric_spec.variant_id,
        )
        or re.fullmatch(metric_spec.task_id_pattern, task_id) is None
        or not snapshot_id
        or meta[6] != 1
        or meta[7] != "FROZEN"
        or type(meta[8]) is not date
        or meta[9:12] != ("USD", 8, 160)
        or any(
            not isinstance(value, str) or not value for value in meta[12:16]
        )
        or meta[16] != RISK_NEUTRAL_MEASURE_ID
        or meta[17] != NUMERAIRE_ID
        or not isinstance(meta[18], str)
        or not meta[18]
        or meta[19] != SELECTION_POLICY_ID
    ):
        raise ValueError(
            f"BSM {metric_spec.target} metric metadata identity is invalid"
        )
    if expected_task_id is not None and task_id != expected_task_id:
        raise ValueError("BSM metric task identity differs from the expected task")
    if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
        raise ValueError(
            "BSM metric snapshot identity differs from the expected snapshot"
        )
    _validate_market_grid(connection, meta, metric_identity=True)


def assert_bsm_greeks_database_safe(database: str | Path) -> None:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM Greeks database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection)
    finally:
        connection.close()


def assert_bsm_metric_database_safe(
    database: str | Path,
    metric_spec: MetricSpec,
    *,
    expected_task_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> None:
    """Validate one derived DB against its frozen target-specific identity."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM metric database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_metric_connection(
            connection,
            metric_spec,
            expected_task_id=expected_task_id,
            expected_snapshot_id=expected_snapshot_id,
        )
    finally:
        connection.close()


def _logical_checksum_from_connection(
    connection: duckdb.DuckDBPyConnection,
    checksum_identity: str,
) -> str:
    hasher = sha256()
    hasher.update(f"{checksum_identity}\n".encode("utf-8"))
    for table in BSM_GREEKS_PUBLIC_TABLES:
        header = {
            "table": table.name,
            "columns": [
                f"{column.name}:{column.duckdb_type}" for column in table.columns
            ],
        }
        hasher.update(canonical_json_text(header).encode("utf-8") + b"\n")
        order = ",".join(table.order_by)
        for row in connection.execute(
            f"SELECT * FROM {table.name} ORDER BY {order}"
        ).fetchall():
            hasher.update(
                canonical_json_text([_hash_value(value) for value in row]).encode(
                    "utf-8"
                )
                + b"\n"
            )
    return hasher.hexdigest()


def bsm_greeks_logical_checksum(database: str | Path) -> str:
    path = Path(database)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection)
        return _logical_checksum_from_connection(connection, LOGICAL_CHECKSUM_ID)
    finally:
        connection.close()


def bsm_metric_logical_checksum(
    database: str | Path,
    metric_spec: MetricSpec,
    *,
    expected_task_id: str | None = None,
    expected_snapshot_id: str | None = None,
) -> str:
    """Validate and hash canonical rows using the metric-specific contract."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM metric database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_metric_connection(
            connection,
            metric_spec,
            expected_task_id=expected_task_id,
            expected_snapshot_id=expected_snapshot_id,
        )
        return _logical_checksum_from_connection(connection, LOGICAL_CHECKSUM_ID)
    finally:
        connection.close()


def market_content_projection(
    database: str | Path,
) -> dict[str, list[dict[str, Any]]]:
    """Return canonical solver-visible market rows without pure identities.

    Task, snapshot, and row-position identities are deliberately excluded.  All
    remaining values use the same tagged scalar representation as the logical
    checksum.  The frozen natural keys make the projection independent of
    physical table row order.
    """

    path = Path(database)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"BSM market database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        projections = (
            (
                BSM_GREEKS_PUBLIC_TABLES[1],
                frozenset(("task_id", "snapshot_id")),
                ("valuation_date", "underlying_id"),
            ),
            (
                BSM_GREEKS_PUBLIC_TABLES[2],
                frozenset(("row_id", "task_id", "snapshot_id")),
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
        normalized: list[list[dict[str, Any]]] = []
        for table, excluded, natural_key in projections:
            actual_columns = tuple(
                (str(row[0]), str(row[1]))
                for row in connection.execute(f"DESCRIBE {table.name}").fetchall()
            )
            expected_columns = tuple(
                (column.name, column.duckdb_type) for column in table.columns
            )
            if actual_columns != expected_columns:
                raise ValueError(f"{table.name} column contract changed")

            projected_columns = tuple(
                column.name for column in table.columns if column.name not in excluded
            )
            rows = connection.execute(
                f"SELECT {','.join(projected_columns)} "
                f"FROM {table.name} ORDER BY {','.join(natural_key)}"
            ).fetchall()
            if not rows:
                raise ValueError(f"{table.name} must not be empty")
            natural_key_indexes = tuple(
                projected_columns.index(field) for field in natural_key
            )
            natural_keys = {
                canonical_json_text(
                    [_hash_value(row[index]) for index in natural_key_indexes]
                )
                for row in rows
            }
            if len(natural_keys) != len(rows):
                raise ValueError(f"{table.name} natural key is duplicated")
            normalized.append(
                [
                    {
                        name: _hash_value(value)
                        for name, value in zip(projected_columns, row, strict=True)
                    }
                    for row in rows
                ]
            )
        return {
            "underlying_market": normalized[0],
            "option_quotes": normalized[1],
        }
    finally:
        connection.close()


def market_content_digest(database: str | Path) -> str:
    """Hash only canonical solver-visible economic and contract content."""

    return sha256(canonical_json_bytes(market_content_projection(database))).hexdigest()


def _load_table_mappings(
    connection: duckdb.DuckDBPyConnection,
    table: _Table,
) -> tuple[dict[str, Any], ...]:
    order = ",".join(table.order_by)
    return tuple(
        dict(zip(table.column_names, row, strict=True))
        for row in connection.execute(
            f"SELECT * FROM {table.name} ORDER BY {order}"
        ).fetchall()
    )


def load_bsm_greeks_inputs_from_connection(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[BSMMarketGreeksInput, ...]:
    return join_bsm_greeks_query_rows(
        _load_table_mappings(connection, BSM_GREEKS_PUBLIC_TABLES[1]),
        _load_table_mappings(connection, BSM_GREEKS_PUBLIC_TABLES[2]),
    )


def load_bsm_greeks_inputs(
    database: str | Path,
) -> tuple[BSMMarketGreeksInput, ...]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return load_bsm_greeks_inputs_from_connection(connection)
    finally:
        connection.close()


def load_bsm_greeks_underlying_market(
    database: str | Path,
) -> tuple[dict[str, Any], ...]:
    """Return exactly the canonical eight-row underlying query payload."""

    underlyings, _ = split_bsm_greeks_query_rows(load_bsm_greeks_inputs(database))
    return underlyings


def load_bsm_greeks_option_quotes(
    database: str | Path,
) -> tuple[dict[str, Any], ...]:
    """Return exactly the canonical 160-row option query payload."""

    _, options = split_bsm_greeks_query_rows(load_bsm_greeks_inputs(database))
    return options


def _query_payload_rows(
    connection: duckdb.DuckDBPyConnection,
    table: _Table,
) -> tuple[dict[str, Any], ...]:
    rows = _load_table_mappings(connection, table)
    return tuple(
        {
            name: (
                value.isoformat()
                if type(value) is date
                else format(value, "f")
                if isinstance(value, Decimal)
                else value
            )
            for name, value in row.items()
        }
        for row in rows
    )


def load_bsm_metric_query_payloads(
    database: str | Path,
    metric_spec: MetricSpec,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Load validated derived-DB query payloads with their metric task identity."""

    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM metric database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_metric_connection(connection, metric_spec)
        return (
            _query_payload_rows(connection, BSM_GREEKS_PUBLIC_TABLES[1]),
            _query_payload_rows(connection, BSM_GREEKS_PUBLIC_TABLES[2]),
        )
    finally:
        connection.close()


def project_bsm_metric_database(
    source_database: str | Path,
    output_database: str | Path,
    metric_spec: MetricSpec,
    derived_task_id: str,
    derived_snapshot_id: str,
) -> BSMMetricDatabaseProjection:
    """Create one metric-specific DB by changing only allowlisted identities."""

    if duckdb.__version__ != PINNED_DUCKDB_VERSION:
        raise RuntimeError(f"packaging requires duckdb=={PINNED_DUCKDB_VERSION}")
    if not is_registered_metric_spec(metric_spec):
        raise ValueError("metric spec must be an exact frozen registry entry")
    if (
        not isinstance(derived_task_id, str)
        or re.fullmatch(metric_spec.task_id_pattern, derived_task_id) is None
    ):
        raise ValueError(
            f"derived task ID is invalid for the {metric_spec.target} target"
        )
    if not isinstance(derived_snapshot_id, str) or not derived_snapshot_id:
        raise ValueError("derived snapshot ID must be a non-empty string")

    source_path = Path(source_database)
    output_path = Path(output_database)
    assert_bsm_greeks_database_safe(source_path)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite metric database: {output_path}")

    source_file_digest = digest_file(source_path)
    source_content_digest = market_content_digest(source_path)
    source = duckdb.connect(str(source_path), read_only=True)
    try:
        metadata = _load_table_mappings(source, BSM_GREEKS_PUBLIC_TABLES[0])[0]
        underlyings = _load_table_mappings(source, BSM_GREEKS_PUBLIC_TABLES[1])
        options = _load_table_mappings(source, BSM_GREEKS_PUBLIC_TABLES[2])
    finally:
        source.close()

    derived_metadata = dict(metadata)
    derived_metadata.update(
        {
            "schema_version": metric_spec.database_schema_version,
            "task_id": derived_task_id,
            "task_version": metric_spec.task_version,
            "variant_id": metric_spec.variant_id,
            "snapshot_id": derived_snapshot_id,
        }
    )
    derived_underlyings = []
    for row in underlyings:
        derived = dict(row)
        derived.update(
            {"task_id": derived_task_id, "snapshot_id": derived_snapshot_id}
        )
        derived_underlyings.append(derived)
    derived_options = []
    for row in options:
        derived = dict(row)
        derived.update(
            {"task_id": derived_task_id, "snapshot_id": derived_snapshot_id}
        )
        derived_options.append(derived)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(temporary))
        _create_tables(connection)
        projected_rows: tuple[Sequence[Mapping[str, Any]], ...] = (
            (derived_metadata,),
            tuple(derived_underlyings),
            tuple(derived_options),
        )
        for table, rows in zip(
            BSM_GREEKS_PUBLIC_TABLES, projected_rows, strict=True
        ):
            _insert(
                connection,
                table,
                [
                    tuple(row[column.name] for column in table.columns)
                    for row in rows
                ],
            )
        connection.close()
        connection = None

        assert_bsm_metric_database_safe(
            temporary,
            metric_spec,
            expected_task_id=derived_task_id,
            expected_snapshot_id=derived_snapshot_id,
        )
        derived_content_digest = market_content_digest(temporary)
        if derived_content_digest != source_content_digest:
            raise ValueError("metric projection changed solver-visible market content")
        derived_logical_checksum = bsm_metric_logical_checksum(
            temporary,
            metric_spec,
            expected_task_id=derived_task_id,
            expected_snapshot_id=derived_snapshot_id,
        )
        derived_file_digest = digest_file(temporary)
        result = BSMMetricDatabaseProjection(
            target=metric_spec.target,
            derived_task_id=derived_task_id,
            derived_snapshot_id=derived_snapshot_id,
            source_database_file_digest=source_file_digest,
            derived_database_file_digest=derived_file_digest,
            source_market_content_digest=source_content_digest,
            derived_market_content_digest=derived_content_digest,
            derived_logical_checksum=derived_logical_checksum,
        )
        os.replace(temporary, output_path)
        return result
    except Exception:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        raise


def materialize_bsm_greeks_database(
    public_market_database: str | Path,
    output_database: str | Path,
    source_manifest: PublicDatabaseManifest,
    *,
    solver_interface_digest: str,
) -> BSMGreeksDatabaseManifest:
    """Create one immutable three-relation task DB from a generic public child."""

    if duckdb.__version__ != PINNED_DUCKDB_VERSION:
        raise RuntimeError(f"packaging requires duckdb=={PINNED_DUCKDB_VERSION}")
    source_path = Path(public_market_database)
    output_path = Path(output_database)
    assert_public_database_safe(source_path)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite task database: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source = duckdb.connect(str(source_path), read_only=True)
    try:
        option_ids = _select_option_ids(source, source_manifest.valuation_date)
        provenance = _validate_joint_provenance(source)
        task_id = stable_package_task_id(
            parent_logical_checksum=source_manifest.parent_logical_checksum,
            valuation_date=source_manifest.valuation_date,
            selected_underlyings=source_manifest.selected_underlyings,
            solver_interface_digest=solver_interface_digest,
        )
        child_snapshot_id = "BSM-GREEKS-PUBLIC-" + task_id.rsplit("-", 1)[-1].upper()
        inputs = _fetch_task_inputs(
            source,
            option_ids=option_ids,
            task_id=task_id,
            child_snapshot_id=child_snapshot_id,
            valuation_date=source_manifest.valuation_date,
        )
    finally:
        source.close()
    _run_publication_gates(inputs)
    p_spec, q_spec, policy, measure, numeraire, rate_path, joint_id = provenance
    temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(temporary))
        _create_tables(connection)
        metadata = (
            BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION,
            task_id,
            "bsm_greeks",
            BSM_MARKET_GREEKS_TASK_VERSION,
            BSM_MARKET_GREEKS_VARIANT_ID,
            child_snapshot_id,
            1,
            "FROZEN",
            source_manifest.valuation_date,
            "USD",
            8,
            160,
            joint_id,
            p_spec,
            q_spec,
            policy,
            measure,
            numeraire,
            rate_path,
            SELECTION_POLICY_ID,
        )
        underlyings, options = split_bsm_greeks_query_rows(inputs)
        underlying_rows = [
            tuple(row[column.name] for column in BSM_GREEKS_PUBLIC_TABLES[1].columns)
            for row in underlyings
        ]
        option_rows = [
            tuple(row[column.name] for column in BSM_GREEKS_PUBLIC_TABLES[2].columns)
            for row in options
        ]
        _insert(connection, BSM_GREEKS_PUBLIC_TABLES[0], [metadata])
        _insert(connection, BSM_GREEKS_PUBLIC_TABLES[1], underlying_rows)
        _insert(connection, BSM_GREEKS_PUBLIC_TABLES[2], option_rows)
        connection.close()
        connection = None
        assert_bsm_greeks_database_safe(temporary)
        checksum = bsm_greeks_logical_checksum(temporary)
        manifest = BSMGreeksDatabaseManifest(
            task_id=task_id,
            child_snapshot_id=child_snapshot_id,
            parent_snapshot_id=source_manifest.parent_snapshot_id,
            parent_snapshot_revision=source_manifest.parent_snapshot_revision,
            parent_logical_checksum=source_manifest.parent_logical_checksum,
            public_logical_checksum=checksum,
            valuation_date=source_manifest.valuation_date,
            selected_underlyings=source_manifest.selected_underlyings,
            selected_option_ids=tuple(sorted(option_ids)),
            joint_market_contract_id=joint_id,
            p_dependence_spec_id=p_spec,
            q_dependence_spec_id=q_spec,
            dependence_policy_id=policy,
            risk_neutral_measure_id=measure,
            numeraire_id=numeraire,
            rate_path_id=rate_path,
        )
        os.replace(temporary, output_path)
        return manifest
    except Exception:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "BSM_GREEKS_PUBLIC_TABLES",
    "BSMGreeksDatabaseManifest",
    "BSMMetricDatabaseProjection",
    "assert_bsm_greeks_database_safe",
    "assert_bsm_metric_database_safe",
    "bsm_greeks_logical_checksum",
    "bsm_metric_logical_checksum",
    "load_bsm_greeks_inputs",
    "load_bsm_greeks_option_quotes",
    "load_bsm_greeks_underlying_market",
    "load_bsm_metric_query_payloads",
    "join_bsm_greeks_query_rows",
    "market_content_digest",
    "market_content_projection",
    "materialize_bsm_greeks_database",
    "OPTION_QUOTE_FIELDS",
    "project_bsm_metric_database",
    "PUBLIC_JOIN_FIELDS",
    "split_bsm_greeks_query_rows",
    "stable_package_task_id",
    "UNDERLYING_MARKET_FIELDS",
]
