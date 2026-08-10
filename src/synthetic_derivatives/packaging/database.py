"""Materialize the three-relation public database for one BSM Greeks task."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
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
from synthetic_derivatives.packaging.contracts import (
    BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_VARIANT_ID,
    LOGICAL_CHECKSUM_ID,
    PACKAGE_SCHEMA_VERSION,
    SELECTION_POLICY_ID,
    canonical_json_text,
    market_greeks_method_contract,
)
from synthetic_derivatives.solver.bsm import bsm_analytic_values
from synthetic_derivatives.solver.bsm_market_greeks import (
    solve_market_greeks_submission,
    solve_market_implied_root,
)
from synthetic_derivatives.tasks.bsm_market_greeks import BSMMarketGreeksInput
from synthetic_derivatives.verifier.bsm_market_greeks import (
    trusted_market_greeks_submission,
)


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
        "solver_visible.greeks_task_inputs",
        (
            _Column("row_id", "VARCHAR"),
            _Column("task_id", "VARCHAR"),
            _Column("snapshot_id", "VARCHAR"),
            _Column("valuation_date", "DATE"),
            _Column("underlying_id", "VARCHAR"),
            _Column("option_id", "VARCHAR"),
            _Column("call_put", "VARCHAR"),
            _Column("spot", "DECIMAL(24,8)"),
            _Column("strike", "DECIMAL(24,8)"),
            _Column("expiry", "DATE"),
            _Column("time_to_expiry_actual365", "DOUBLE"),
            _Column("bid", "DECIMAL(24,8)"),
            _Column("ask", "DECIMAL(24,8)"),
            _Column("contract_multiplier", "DECIMAL(24,8)"),
            _Column("currency", "VARCHAR"),
            _Column("risk_free_rate", "DOUBLE"),
            _Column("dividend_yield", "DOUBLE"),
            _Column("calendar", "VARCHAR"),
            _Column("day_count", "VARCHAR"),
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
    _Table(
        "solver_visible.greeks_task_contract",
        (
            _Column("task_id", "VARCHAR"),
            _Column("method_id", "VARCHAR"),
            _Column("contract_json", "JSON"),
        ),
        ("task_id",),
    ),
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


def stable_package_task_id(
    *,
    parent_logical_checksum: str,
    valuation_date: date,
    selected_underlyings: Sequence[str],
) -> str:
    """Implement the frozen public-safe stable identity formula."""

    ordered = tuple(str(item) for item in selected_underlyings)
    if ordered != tuple(sorted(ordered)) or len(ordered) != len(set(ordered)):
        raise ValueError("selected underlyings must be unique and canonically ordered")
    payload = "|".join(
        (
            parent_logical_checksum,
            BSM_MARKET_GREEKS_VARIANT_ID,
            valuation_date.isoformat(),
            canonical_json_text(list(ordered)),
            BSM_MARKET_GREEKS_METHOD_ID,
            PACKAGE_SCHEMA_VERSION,
        )
    )
    return "bsm-mig-v1-" + sha256(payload.encode("utf-8")).hexdigest()[:24]


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


def _is_rounding_tie(value: float) -> bool:
    scaled = abs(Decimal(str(value)) * Decimal("100000000"))
    return scaled - int(scaled) == Decimal("0.5")


def _run_publication_gates(rows: Sequence[BSMMarketGreeksInput]) -> None:
    solver = solve_market_greeks_submission(rows)
    trusted = trusted_market_greeks_submission(rows)
    if solver.to_dict() != trusted.to_dict():
        raise ValueError("stdlib and QuantLib canonical answers differ")
    for row in rows:
        sigma = solve_market_implied_root(row)
        values = bsm_analytic_values(row.as_greeks_input(sigma))
        if any(
            _is_rounding_tie(value)
            for value in (
                sigma,
                values.delta,
                values.gamma,
                values.vega,
                values.theta,
                values.rho,
            )
        ):
            raise ValueError("selected row lies exactly on a decimal rounding tie")


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


def _validate_public_connection(connection: duckdb.DuckDBPyConnection) -> None:
    _validate_schema(connection)
    metadata = connection.execute("SELECT * FROM metadata.public_task").fetchall()
    if len(metadata) != 1:
        raise ValueError("BSM Greeks database requires one metadata row")
    meta = metadata[0]
    if (
        meta[0] != BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION
        or meta[2:5] != ("bsm_greeks", "1.0.0", BSM_MARKET_GREEKS_VARIANT_ID)
        or meta[7] != "FROZEN"
        or meta[9:12] != ("USD", 8, 160)
        or meta[19] != SELECTION_POLICY_ID
    ):
        raise ValueError("BSM Greeks metadata identity is invalid")
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
    method = load_bsm_greeks_contract_from_connection(connection)
    if method != market_greeks_method_contract():
        raise ValueError("BSM Greeks method contract drifted")
    for table in BSM_GREEKS_PUBLIC_TABLES:
        order = ",".join(table.order_by)
        for index, row in enumerate(
            connection.execute(f"SELECT * FROM {table.name} ORDER BY {order}").fetchall()
        ):
            assert_no_private_leakage(
                dict(zip(table.column_names, row, strict=True)),
                f"{table.name}[{index}]",
            )


def assert_bsm_greeks_database_safe(database: str | Path) -> None:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM Greeks database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection)
    finally:
        connection.close()


def bsm_greeks_logical_checksum(database: str | Path) -> str:
    path = Path(database)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection)
        hasher = sha256()
        hasher.update(f"{LOGICAL_CHECKSUM_ID}\n".encode("utf-8"))
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
    finally:
        connection.close()


def load_bsm_greeks_contract_from_connection(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT method_id, contract_json FROM solver_visible.greeks_task_contract"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != BSM_MARKET_GREEKS_METHOD_ID:
        raise ValueError("database has an invalid market-Greeks method row")
    contract = _json(rows[0][1])
    if not isinstance(contract, dict):
        raise ValueError("market-Greeks contract must be a JSON object")
    return contract


def load_bsm_greeks_contract(database: str | Path) -> dict[str, Any]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return load_bsm_greeks_contract_from_connection(connection)
    finally:
        connection.close()


def load_bsm_greeks_inputs_from_connection(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[BSMMarketGreeksInput, ...]:
    table = next(
        item
        for item in BSM_GREEKS_PUBLIC_TABLES
        if item.name == "solver_visible.greeks_task_inputs"
    )
    order = ",".join(table.order_by)
    rows = connection.execute(
        f"SELECT * FROM {table.name} ORDER BY {order}"
    ).fetchall()
    return tuple(
        BSMMarketGreeksInput.from_mapping(
            dict(zip(table.column_names, row, strict=True))
        )
        for row in rows
    )


def load_bsm_greeks_inputs(
    database: str | Path,
) -> tuple[BSMMarketGreeksInput, ...]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return load_bsm_greeks_inputs_from_connection(connection)
    finally:
        connection.close()


def materialize_bsm_greeks_database(
    public_market_database: str | Path,
    output_database: str | Path,
    source_manifest: PublicDatabaseManifest,
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
            "1.0.0",
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
        input_rows = []
        for row in inputs:
            mapping = row.to_tool_mapping()
            input_rows.append(
                tuple(
                    mapping[column.name]
                    for column in BSM_GREEKS_PUBLIC_TABLES[1].columns
                )
            )
        contract_row = (
            task_id,
            BSM_MARKET_GREEKS_METHOD_ID,
            canonical_json_text(market_greeks_method_contract()),
        )
        _insert(connection, BSM_GREEKS_PUBLIC_TABLES[0], [metadata])
        _insert(connection, BSM_GREEKS_PUBLIC_TABLES[1], input_rows)
        _insert(connection, BSM_GREEKS_PUBLIC_TABLES[2], [contract_row])
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
    "assert_bsm_greeks_database_safe",
    "bsm_greeks_logical_checksum",
    "load_bsm_greeks_contract",
    "load_bsm_greeks_inputs",
    "materialize_bsm_greeks_database",
    "stable_package_task_id",
]
