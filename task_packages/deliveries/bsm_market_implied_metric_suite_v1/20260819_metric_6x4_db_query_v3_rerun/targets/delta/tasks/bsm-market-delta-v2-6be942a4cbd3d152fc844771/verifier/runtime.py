"""Self-contained trusted runtime for one single-metric BSM task.

This file is copied byte-for-byte into every delivered leaf verifier.  It must
therefore remain independent of the ``synthetic_derivatives`` package.  Its only
non-stdlib dependencies are the verifier-image pins ``duckdb==1.5.5`` and
``QuantLib==1.39``.

The public quotes describe unit, cash-settled European options on positive USD
ex-dividend spots under the common USD money-market pricing measure ``Q``.  The
known state is the valuation-date spot and the public flat continuously
compounded rate/dividend curves; calendar time is Actual/365 Fixed.  For every
row, the exact DECIMAL(24,8) bid/ask midpoint is formed in decimal arithmetic and
cast once to binary64.  The runtime then performs exactly 80 binary64 bisection
updates on ``[1e-6, 5.0]`` using QuantLib's analytic constant-parameter BSM law.
An IV task publishes that final root.  A Greek task evaluates only its declared
Greek at the unrounded root, applies the frozen unit scaling, and publishes it.
All outputs use ``Decimal(str(binary64))``, ``ROUND_HALF_EVEN``, and exactly eight
decimal places; complete submissions are compared by exact equality.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

import duckdb
import QuantLib as ql


_PINNED_DUCKDB_VERSION = "1.5.5"
_PINNED_QUANTLIB_VERSION = "1.39"

_DATABASE_SCHEMA_VERSION = "bsm-market-metric-task-duckdb-v1.0.0"
_TASK_FAMILY = "bsm_greeks"
_TASK_VERSION = "1.0.0"
_SELECTION_POLICY_ID = (
    "two-nearest-expiries-five-abs-log-forward-moneyness-pairs-v1"
)
_LOGICAL_CHECKSUM_ID = "sha256-bsm-greeks-canonical-logical-rows-v2"
_RISK_NEUTRAL_MEASURE_ID = "USD-MONEY-MARKET-Q-v1"
_NUMERAIRE_ID = "USD-MONEY-MARKET-ACCOUNT-v1"
_SUCCESS_IV_STATUS = "CONVERGED_FIXED_ITERATIONS"
_ORACLE_CONFIG_SCHEMA_VERSION = "bsm-market-metric-oracle-config-v1.0.0"
_IV_METHOD_ID = "bsm-bisection-float64-80-v1"
_GREEKS_METHOD_ID = "bsm-analytic-float64-greeks-v1"

_LOWER_VOLATILITY = 0.000001
_UPPER_VOLATILITY = 5.0
_BISECTION_ITERATIONS = 80
_OUTPUT_QUANTUM = Decimal("0.00000001")
_QUOTE_QUANTUM = Decimal("0.00000001")
_DECIMAL_24_8_ABSOLUTE_LIMIT = Decimal("10000000000000000")

_ROW_ID_PATTERN = re.compile(r"^row_[0-9]{6}$")
_SIGNED_DECIMAL8_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_NONNEGATIVE_DECIMAL8_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.[0-9]{8}$"
)


@dataclass(frozen=True)
class _MetricSpec:
    target: str
    output_field: str
    variant_id: str
    method_id: str
    submission_schema_version: str
    verifier_id: str
    task_id_pattern: str
    decimal_constraint: str
    needs_iv_status: bool


# This whitelist is intentionally duplicated in the copied runtime.  A leaf
# verifier must not trust arbitrary field/method instructions from a mutable
# oracle_config.json and must not import the authoring registry.
_METRIC_SPECS = (
    _MetricSpec(
        "iv",
        "market_implied_volatility",
        "bsm_market_implied_iv_v1",
        "bsm-mid-iv-bisection80-v1",
        "bsm-market-implied-iv-submission-v1.0.0",
        "quantlib-bsm-market-implied-iv-verifier-v1",
        r"^bsm-market-iv-v1-[0-9a-f]{24}$",
        "positive_decimal8",
        True,
    ),
    _MetricSpec(
        "delta",
        "unit_delta",
        "bsm_market_implied_delta_v1",
        "bsm-mid-iv-bisection80-analytic-delta-v1",
        "bsm-market-implied-delta-submission-v1.0.0",
        "quantlib-bsm-market-implied-delta-verifier-v1",
        r"^bsm-market-delta-v1-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    _MetricSpec(
        "gamma",
        "unit_gamma",
        "bsm_market_implied_gamma_v1",
        "bsm-mid-iv-bisection80-analytic-gamma-v1",
        "bsm-market-implied-gamma-submission-v1.0.0",
        "quantlib-bsm-market-implied-gamma-verifier-v1",
        r"^bsm-market-gamma-v1-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    _MetricSpec(
        "vega_1volpt",
        "unit_vega_1volpt",
        "bsm_market_implied_vega_1volpt_v1",
        "bsm-mid-iv-bisection80-analytic-vega-1volpt-v1",
        "bsm-market-implied-vega-1volpt-submission-v1.0.0",
        "quantlib-bsm-market-implied-vega-1volpt-verifier-v1",
        r"^bsm-market-vega-1volpt-v1-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    _MetricSpec(
        "theta_1calendar_day",
        "unit_theta_1calendar_day",
        "bsm_market_implied_theta_1calendar_day_v1",
        "bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1",
        "bsm-market-implied-theta-1calendar-day-submission-v1.0.0",
        "quantlib-bsm-market-implied-theta-1calendar-day-verifier-v1",
        r"^bsm-market-theta-1calendar-day-v1-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    _MetricSpec(
        "rho_1pct",
        "unit_rho_1pct",
        "bsm_market_implied_rho_1pct_v1",
        "bsm-mid-iv-bisection80-analytic-rho-1pct-v1",
        "bsm-market-implied-rho-1pct-submission-v1.0.0",
        "quantlib-bsm-market-implied-rho-1pct-verifier-v1",
        r"^bsm-market-rho-1pct-v1-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
)
_METRIC_SPEC_BY_TARGET = {spec.target: spec for spec in _METRIC_SPECS}


def _oracle_config(spec: _MetricSpec) -> dict[str, Any]:
    return {
        "oracle_config_schema_version": _ORACLE_CONFIG_SCHEMA_VERSION,
        "target": spec.target,
        "output_field": spec.output_field,
        "variant_id": spec.variant_id,
        "method_id": spec.method_id,
        "submission_schema_version": spec.submission_schema_version,
        "verifier_id": spec.verifier_id,
        "database_schema_version": _DATABASE_SCHEMA_VERSION,
        "task_version": _TASK_VERSION,
        "task_id_pattern": spec.task_id_pattern,
        "decimal_constraint": spec.decimal_constraint,
        "needs_iv_status": spec.needs_iv_status,
        "quantlib_version": _PINNED_QUANTLIB_VERSION,
        "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
        "iv_method_id": _IV_METHOD_ID,
        "greeks_method_id": _GREEKS_METHOD_ID,
        "pricing_measure_id": _RISK_NEUTRAL_MEASURE_ID,
        "numeraire_id": _NUMERAIRE_ID,
        "day_count": "Actual365Fixed",
        "rate_compounding": "continuous",
        "iterations": _BISECTION_ITERATIONS,
        "volatility_bracket": [_LOWER_VOLATILITY, _UPPER_VOLATILITY],
        "canonical_precision": 8,
        "canonical_rounding": "ROUND_HALF_EVEN",
    }


def _spec_from_config(method_config: Mapping[str, Any]) -> _MetricSpec:
    if not isinstance(method_config, Mapping):
        raise ValueError("trusted verifier requires an oracle config object")
    target = method_config.get("target")
    spec = _METRIC_SPEC_BY_TARGET.get(target) if isinstance(target, str) else None
    if spec is None or dict(method_config) != _oracle_config(spec):
        raise ValueError("trusted verifier received a different oracle config")
    return spec


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
_INPUT_FIELDS = (
    "row_id",
    "task_id",
    "snapshot_id",
    "valuation_date",
    "underlying_id",
    "option_id",
    "call_put",
    "spot",
    "strike",
    "expiry",
    "time_to_expiry_actual365",
    "bid",
    "ask",
    "contract_multiplier",
    "currency",
    "risk_free_rate",
    "dividend_yield",
    "calendar",
    "day_count",
    "exercise_style",
    "settlement_type",
)
_SUBMISSION_FIELDS = (
    "task_id",
    "submission_schema_version",
    "method_id",
    "status",
    "rows",
)


def _require_pinned_duckdb() -> None:
    if duckdb.__version__ != _PINNED_DUCKDB_VERSION:
        raise RuntimeError(
            f"trusted package verifier requires duckdb=={_PINNED_DUCKDB_VERSION}, "
            f"found {duckdb.__version__}"
        )


def _require_pinned_quantlib() -> None:
    if ql.__version__ != _PINNED_QUANTLIB_VERSION:
        raise RuntimeError(
            "trusted package verifier requires QuantLib=="
            f"{_PINNED_QUANTLIB_VERSION}, found {ql.__version__}"
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


def _date_value(value: Any, field: str) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} must be an ISO date") from error
    raise ValueError(f"{field} must be a date")


def _binary64(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _quote_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{field} must be a decimal-compatible value")
    try:
        result = Decimal(str(value))
        stored = result.quantize(_QUOTE_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a finite DECIMAL(24,8) value") from error
    if not result.is_finite() or result != stored:
        raise ValueError(f"{field} must be exactly representable at 8 decimal places")
    if abs(stored) >= _DECIMAL_24_8_ABSOLUTE_LIMIT:
        raise ValueError(f"{field} must fit DECIMAL(24,8)")
    if stored < 0:
        raise ValueError(f"{field} must be non-negative")
    return stored


def _matches_any_task_id(value: str) -> bool:
    return any(re.fullmatch(spec.task_id_pattern, value) for spec in _METRIC_SPECS)


@dataclass(frozen=True)
class BSMMarketMetricInput:
    """One validated solver-visible option quote joined to its market state."""

    row_id: str
    task_id: str
    snapshot_id: str
    valuation_date: date
    underlying_id: str
    option_id: str
    call_put: str
    spot: Any
    strike: Any
    expiry: date
    time_to_expiry_actual365: Any
    bid: Any
    ask: Any
    contract_multiplier: Any
    currency: str
    risk_free_rate: Any
    dividend_yield: Any
    calendar: str
    day_count: str
    exercise_style: str
    settlement_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.row_id, str) or not _ROW_ID_PATTERN.fullmatch(self.row_id):
            raise ValueError("row_id must use row_000001 format")
        if not isinstance(self.task_id, str) or not _matches_any_task_id(self.task_id):
            raise ValueError("task_id does not match a single-metric identity")
        for field in ("snapshot_id", "underlying_id", "option_id"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field):
                raise ValueError(f"{field} must be a non-empty string")
        object.__setattr__(
            self, "valuation_date", _date_value(self.valuation_date, "valuation_date")
        )
        object.__setattr__(self, "expiry", _date_value(self.expiry, "expiry"))
        if self.call_put not in {"call", "put"}:
            raise ValueError("call_put must be 'call' or 'put'")
        if self.currency != "USD":
            raise ValueError("the declared pricing measure and numeraire require USD")
        if self.calendar != "WeekendsOnly" or self.day_count != "Actual365Fixed":
            raise ValueError("single-metric task requires WeekendsOnly/Actual365Fixed")
        if self.exercise_style != "european" or self.settlement_type != "cash":
            raise ValueError("single-metric task requires cash-settled European options")

        spot = _binary64(self.spot, "spot")
        strike = _binary64(self.strike, "strike")
        tau = _binary64(self.time_to_expiry_actual365, "time_to_expiry_actual365")
        _binary64(self.risk_free_rate, "risk_free_rate")
        _binary64(self.dividend_yield, "dividend_yield")
        multiplier = _binary64(self.contract_multiplier, "contract_multiplier")
        bid = _quote_decimal(self.bid, "bid")
        ask = _quote_decimal(self.ask, "ask")
        if spot <= 0.0 or strike <= 0.0:
            raise ValueError("spot and strike must be positive")
        if tau <= 0.0:
            raise ValueError("time_to_expiry_actual365 must be positive")
        if multiplier <= 0.0:
            raise ValueError("contract_multiplier must be positive")
        elapsed_days = (self.expiry - self.valuation_date).days
        if elapsed_days <= 0 or tau != elapsed_days / 365.0:
            raise ValueError(
                "time_to_expiry_actual365 must equal positive calendar days / 365"
            )
        if bid > ask:
            raise ValueError("bid must not exceed ask")

    @property
    def canonical_order_key(self) -> tuple[Any, ...]:
        return (
            self.valuation_date,
            self.underlying_id,
            self.expiry,
            Decimal(str(self.strike)),
            self.call_put,
            self.option_id,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BSMMarketMetricInput:
        if not isinstance(value, Mapping) or set(value) != set(_INPUT_FIELDS):
            raise ValueError("single-metric input has missing or extra fields")
        return cls(**{field: value[field] for field in _INPUT_FIELDS})


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
    connection: duckdb.DuckDBPyConnection, spec: _MetricSpec
) -> tuple[BSMMarketMetricInput, ...]:
    _validate_schema(connection)
    metadata = connection.execute("SELECT * FROM metadata.public_task").fetchall()
    if len(metadata) != 1:
        raise ValueError("single-metric database requires one metadata row")
    meta = metadata[0]
    if (
        meta[0] != _DATABASE_SCHEMA_VERSION
        or not isinstance(meta[1], str)
        or re.fullmatch(spec.task_id_pattern, meta[1]) is None
        or meta[2:5] != (_TASK_FAMILY, _TASK_VERSION, spec.variant_id)
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


@dataclass(frozen=True)
class _NormalizedInput:
    source: BSMMarketMetricInput
    spot: float
    strike: float
    tau: float
    risk_free_rate: float
    dividend_yield: float
    observed_price: float


def _normalize_input(task_input: BSMMarketMetricInput) -> _NormalizedInput:
    bid = _quote_decimal(task_input.bid, "bid")
    ask = _quote_decimal(task_input.ask, "ask")
    observed_price = float((bid + ask) / Decimal(2))
    if not math.isfinite(observed_price):
        raise ValueError("observed midpoint must be finite in binary64")
    return _NormalizedInput(
        source=task_input,
        spot=_binary64(task_input.spot, "spot"),
        strike=_binary64(task_input.strike, "strike"),
        tau=_binary64(task_input.time_to_expiry_actual365, "time_to_expiry_actual365"),
        risk_free_rate=_binary64(task_input.risk_free_rate, "risk_free_rate"),
        dividend_yield=_binary64(task_input.dividend_yield, "dividend_yield"),
        observed_price=observed_price,
    )


def _quantlib_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _quantlib_value(
    task_input: _NormalizedInput, sigma: float, target: str
) -> float:
    _require_pinned_quantlib()
    source = task_input.source
    settings = ql.Settings.instance()
    previous_evaluation_date = settings.evaluationDate
    valuation_date = _quantlib_date(source.valuation_date)
    expiry = _quantlib_date(source.expiry)
    day_count = ql.Actual365Fixed()
    try:
        settings.evaluationDate = valuation_date
        spot = ql.QuoteHandle(ql.SimpleQuote(task_input.spot))
        risk_free_curve = ql.YieldTermStructureHandle(
            ql.FlatForward(
                valuation_date,
                task_input.risk_free_rate,
                day_count,
                ql.Continuous,
                ql.Annual,
            )
        )
        dividend_curve = ql.YieldTermStructureHandle(
            ql.FlatForward(
                valuation_date,
                task_input.dividend_yield,
                day_count,
                ql.Continuous,
                ql.Annual,
            )
        )
        volatility = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(
                valuation_date,
                ql.NullCalendar(),
                sigma,
                day_count,
            )
        )
        process = ql.BlackScholesMertonProcess(
            spot, dividend_curve, risk_free_curve, volatility
        )
        option_type = ql.Option.Call if source.call_put == "call" else ql.Option.Put
        payoff = ql.PlainVanillaPayoff(option_type, task_input.strike)
        option = ql.VanillaOption(payoff, ql.EuropeanExercise(expiry))
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        values = {
            "price": option.NPV,
            "delta": option.delta,
            "gamma": option.gamma,
            "vega_1volpt": lambda: 0.01 * option.vega(),
            "theta_1calendar_day": lambda: option.theta() / 365.0,
            "rho_1pct": lambda: 0.01 * option.rho(),
        }
        try:
            evaluator = values[target]
        except KeyError as error:
            raise ValueError("QuantLib target is not allowlisted") from error
        return float(evaluator())
    finally:
        settings.evaluationDate = previous_evaluation_date


def _market_implied_root(task_input: _NormalizedInput) -> float:
    discounted_spot = task_input.spot * math.exp(
        -task_input.dividend_yield * task_input.tau
    )
    discounted_strike = task_input.strike * math.exp(
        -task_input.risk_free_rate * task_input.tau
    )
    if task_input.source.call_put == "call":
        lower_bound = max(discounted_spot - discounted_strike, 0.0)
        upper_bound = discounted_spot
    else:
        lower_bound = max(discounted_strike - discounted_spot, 0.0)
        upper_bound = discounted_strike
    if not lower_bound <= task_input.observed_price < upper_bound:
        raise ValueError("published single-metric row is outside BSM price bounds")

    low = _LOWER_VOLATILITY
    high = _UPPER_VOLATILITY
    price_low = _quantlib_value(task_input, low, "price")
    price_high = _quantlib_value(task_input, high, "price")
    if not price_low <= task_input.observed_price <= price_high:
        raise ValueError("published single-metric row has no root in the bracket")
    for _ in range(_BISECTION_ITERATIONS):
        midpoint = (low + high) / 2.0
        if _quantlib_value(task_input, midpoint, "price") < task_input.observed_price:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _canonical_decimal(value: float) -> str:
    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError("canonical output must be a finite binary64 value")
    try:
        quantized = Decimal(str(value)).quantize(
            _OUTPUT_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    except InvalidOperation as error:
        raise ValueError("canonical output exceeds decimal contract") from error
    if quantized == 0:
        quantized = Decimal("0").quantize(_OUTPUT_QUANTUM)
    return format(quantized, "f")


def _metric_value(spec: _MetricSpec, normalized: _NormalizedInput, sigma: float) -> str:
    if spec.target == "iv":
        return _canonical_decimal(sigma)
    return _canonical_decimal(_quantlib_value(normalized, sigma, spec.target))


def _expected_submission(
    task_inputs: Iterable[BSMMarketMetricInput], spec: _MetricSpec
) -> dict[str, Any]:
    rows = tuple(sorted(task_inputs, key=lambda item: item.canonical_order_key))
    if len(rows) != 160 or len({row.task_id for row in rows}) != 1:
        raise ValueError("trusted verifier requires one complete 160-row task")
    if re.fullmatch(spec.task_id_pattern, rows[0].task_id) is None:
        raise ValueError("task inputs do not belong to the configured target")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, 161))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("trusted verifier input order is invalid")

    results: list[dict[str, str]] = []
    for row in rows:
        normalized = _normalize_input(row)
        sigma = _market_implied_root(normalized)
        result = {
            "row_id": row.row_id,
            spec.output_field: _metric_value(spec, normalized, sigma),
        }
        if spec.needs_iv_status:
            result["iv_status"] = _SUCCESS_IV_STATUS
        results.append(result)
    return {
        "task_id": rows[0].task_id,
        "submission_schema_version": spec.submission_schema_version,
        "method_id": spec.method_id,
        "status": "completed",
        "rows": results,
    }


def _validate_decimal8(value: Any, spec: _MetricSpec) -> None:
    if not isinstance(value, str) or not _SIGNED_DECIMAL8_PATTERN.fullmatch(value):
        raise ValueError(f"{spec.output_field} must be a canonical decimal8 string")
    if value == "-0.00000000":
        raise ValueError(f"{spec.output_field} must not contain negative zero")
    decimal_value = Decimal(value)
    if spec.decimal_constraint == "positive_decimal8" and decimal_value <= 0:
        raise ValueError(f"{spec.output_field} must be positive")
    if (
        spec.decimal_constraint == "nonnegative_decimal8"
        and not _NONNEGATIVE_DECIMAL8_PATTERN.fullmatch(value)
    ):
        raise ValueError(f"{spec.output_field} must be non-negative")


def _parse_submission(
    submission: Mapping[str, Any], spec: _MetricSpec
) -> dict[str, Any]:
    if not isinstance(submission, Mapping) or set(submission) != set(_SUBMISSION_FIELDS):
        raise ValueError("single-metric submission has missing or extra fields")
    if (
        not isinstance(submission["task_id"], str)
        or re.fullmatch(spec.task_id_pattern, submission["task_id"]) is None
        or submission["submission_schema_version"] != spec.submission_schema_version
        or submission["method_id"] != spec.method_id
        or submission["status"] != "completed"
    ):
        raise ValueError("single-metric submission identity is invalid")
    rows = submission["rows"]
    if not isinstance(rows, list) or len(rows) != 160:
        raise ValueError("single-metric submission must contain exactly 160 rows")
    expected_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_fields.add("iv_status")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("single-metric result row has missing or extra fields")
        if row["row_id"] != f"row_{index:06d}":
            raise ValueError("submission rows are missing, duplicated, or reordered")
        if spec.needs_iv_status and row["iv_status"] != _SUCCESS_IV_STATUS:
            raise ValueError("single-metric IV status is invalid")
        _validate_decimal8(row[spec.output_field], spec)
    return {
        "task_id": submission["task_id"],
        "submission_schema_version": submission["submission_schema_version"],
        "method_id": submission["method_id"],
        "status": submission["status"],
        "rows": [dict(row) for row in rows],
    }


def validate_market_metric_submission_contract(
    submission: Mapping[str, Any], method_config: Mapping[str, Any]
) -> None:
    """Validate the exact single-target submission shape and identity."""

    spec = _spec_from_config(method_config)
    _parse_submission(submission, spec)


def expected_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    method_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute and return the canonical single-target submission."""

    spec = _spec_from_config(method_config)
    return _expected_submission(task_inputs, spec)


def verify_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any],
) -> None:
    """Recompute one target from public rows and require exact equality."""

    spec = _spec_from_config(method_config)
    expected = _expected_submission(task_inputs, spec)
    try:
        actual = _parse_submission(submission, spec)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the single-metric schema") from error
    if actual != expected:
        raise ValueError("single-metric canonical submission mismatch")


__all__ = [
    "BSMMarketMetricInput",
    "bsm_metric_logical_checksum",
    "digest_file",
    "expected_market_metric_submission",
    "load_bsm_market_metric_inputs",
    "validate_market_metric_submission_contract",
    "verify_market_metric_submission",
]

# DuckDB-query v3 changes the solver ABI, not the financial calculation.
_DATABASE_SCHEMA_VERSION = "bsm-market-metric-task-duckdb-v2.0.0"
_TASK_VERSION = "2.0.0"

_METRIC_SPECS = (
    _MetricSpec(
        "iv",
        "market_implied_volatility",
        "bsm_market_implied_iv_v1",
        "bsm-mid-iv-bisection80-v1",
        "bsm-market-implied-iv-submission-v2.0.0",
        "quantlib-bsm-market-implied-iv-verifier-v2",
        r"^bsm-market-iv-v2-[0-9a-f]{24}$",
        "positive_decimal8",
        True,
    ),
    _MetricSpec(
        "delta",
        "unit_delta",
        "bsm_market_implied_delta_v1",
        "bsm-mid-iv-bisection80-analytic-delta-v1",
        "bsm-market-implied-delta-submission-v2.0.0",
        "quantlib-bsm-market-implied-delta-verifier-v2",
        r"^bsm-market-delta-v2-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    _MetricSpec(
        "gamma",
        "unit_gamma",
        "bsm_market_implied_gamma_v1",
        "bsm-mid-iv-bisection80-analytic-gamma-v1",
        "bsm-market-implied-gamma-submission-v2.0.0",
        "quantlib-bsm-market-implied-gamma-verifier-v2",
        r"^bsm-market-gamma-v2-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    _MetricSpec(
        "vega_1volpt",
        "unit_vega_1volpt",
        "bsm_market_implied_vega_1volpt_v1",
        "bsm-mid-iv-bisection80-analytic-vega-1volpt-v1",
        "bsm-market-implied-vega-1volpt-submission-v2.0.0",
        "quantlib-bsm-market-implied-vega-1volpt-verifier-v2",
        r"^bsm-market-vega-1volpt-v2-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    _MetricSpec(
        "theta_1calendar_day",
        "unit_theta_1calendar_day",
        "bsm_market_implied_theta_1calendar_day_v1",
        "bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1",
        "bsm-market-implied-theta-1calendar-day-submission-v2.0.0",
        "quantlib-bsm-market-implied-theta-1calendar-day-verifier-v2",
        r"^bsm-market-theta-1calendar-day-v2-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    _MetricSpec(
        "rho_1pct",
        "unit_rho_1pct",
        "bsm_market_implied_rho_1pct_v1",
        "bsm-mid-iv-bisection80-analytic-rho-1pct-v1",
        "bsm-market-implied-rho-1pct-submission-v2.0.0",
        "quantlib-bsm-market-implied-rho-1pct-verifier-v2",
        r"^bsm-market-rho-1pct-v2-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
)
_METRIC_SPEC_BY_TARGET = {spec.target: spec for spec in _METRIC_SPECS}


def _parse_submission_v3(
    submission: Mapping[str, Any],
    spec: _MetricSpec,
    expected_row_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(submission, Mapping) or set(submission) != set(_SUBMISSION_FIELDS):
        raise ValueError("single-metric submission has missing or extra fields")
    if (
        not isinstance(submission["task_id"], str)
        or re.fullmatch(spec.task_id_pattern, submission["task_id"]) is None
        or submission["submission_schema_version"] != spec.submission_schema_version
        or submission["method_id"] != spec.method_id
        or submission["status"] != "completed"
    ):
        raise ValueError("single-metric submission identity is invalid")

    rows = submission["rows"]
    if not isinstance(rows, list):
        raise ValueError("single-metric submission rows must be an array")
    expected_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_fields.add("iv_status")

    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("single-metric result row has missing or extra fields")
        row_id = row["row_id"]
        if not isinstance(row_id, str) or _ROW_ID_PATTERN.fullmatch(row_id) is None:
            raise ValueError("submission row_id must use row_000001 format")
        if row_id in rows_by_id:
            raise ValueError("single-metric submission contains duplicate row_id")
        if spec.needs_iv_status and row["iv_status"] != _SUCCESS_IV_STATUS:
            raise ValueError("single-metric IV status is invalid")
        _validate_decimal8(row[spec.output_field], spec)
        rows_by_id[row_id] = dict(row)

    if expected_row_ids is None:
        aligned_ids = tuple(rows_by_id)
    else:
        aligned_ids = tuple(expected_row_ids)
        if len(set(aligned_ids)) != len(aligned_ids):
            raise ValueError("trusted expected row IDs are duplicated")
        if set(rows_by_id) != set(aligned_ids):
            raise ValueError("submission row_id set differs from the public task")

    return {
        "task_id": submission["task_id"],
        "submission_schema_version": submission["submission_schema_version"],
        "method_id": submission["method_id"],
        "status": submission["status"],
        "rows": [rows_by_id[row_id] for row_id in aligned_ids],
    }


def validate_market_metric_submission_contract(
    submission: Mapping[str, Any], method_config: Mapping[str, Any]
) -> None:
    """Validate strict v3 fields, identities, values, and row-id uniqueness."""

    spec = _spec_from_config(method_config)
    _parse_submission_v3(submission, spec)


def verify_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any],
) -> None:
    """Key-align by row_id and require exact canonical v3 equality."""

    spec = _spec_from_config(method_config)
    expected = _expected_submission(task_inputs, spec)
    expected_row_ids = tuple(row["row_id"] for row in expected["rows"])
    try:
        actual = _parse_submission_v3(submission, spec, expected_row_ids)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the single-metric schema") from error
    if actual != expected:
        raise ValueError("single-metric canonical submission mismatch")
