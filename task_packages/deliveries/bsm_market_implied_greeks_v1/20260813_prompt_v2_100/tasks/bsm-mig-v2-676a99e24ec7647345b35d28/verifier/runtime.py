"""Standalone trusted runtime for one BSM market-Greeks package.

This module is copied into an authored task package.  It deliberately imports no
project module: its complete runtime dependency set is the Python standard
library, ``duckdb==1.5.5``, and ``QuantLib==1.39``.

The verified pricing object is a unit, cash-settled European option on a positive
USD ex-dividend spot.  Prices are under the common pricing measure ``Q`` attached
to the USD money-market numeraire, conditional on the valuation-date spot,
contract, flat continuously compounded rate/dividend curves, and visible bid/ask
quote.  Calendar time uses Actual/365 Fixed.  For each row, the verifier converts
the exact DECIMAL(24,8) bid/ask midpoint to binary64 once, performs exactly 80
binary64 bisection updates on ``[1e-6, 5.0]`` using QuantLib's exact analytic
constant-parameter BSM transition/pricing law, and evaluates the five declared
unit Greeks at the unrounded final root.  Published values are then quantized to
eight decimal places with ``ROUND_HALF_EVEN`` and compared exactly.
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

_DATABASE_SCHEMA_VERSION = "bsm-greeks-task-duckdb-v2.0.0"
_VARIANT_ID = "bsm_market_implied_greeks_v1"
_TASK_VERSION = "2.0.0"
_METHOD_ID = "bsm-mid-iv-bisection80-analytic-greeks-v1"
_SUBMISSION_SCHEMA_VERSION = "bsm-market-implied-greeks-submission-v2.0.0"
_SELECTION_POLICY_ID = (
    "two-nearest-expiries-five-abs-log-forward-moneyness-pairs-v1"
)
_LOGICAL_CHECKSUM_ID = "sha256-bsm-greeks-canonical-logical-rows-v2"
_RISK_NEUTRAL_MEASURE_ID = "USD-MONEY-MARKET-Q-v1"
_NUMERAIRE_ID = "USD-MONEY-MARKET-ACCOUNT-v1"
_SUCCESS_IV_STATUS = "CONVERGED_FIXED_ITERATIONS"

_LOWER_VOLATILITY = 0.000001
_UPPER_VOLATILITY = 5.0
_BISECTION_ITERATIONS = 80
_OUTPUT_QUANTUM = Decimal("0.00000001")
_QUOTE_QUANTUM = Decimal("0.00000001")
_DECIMAL_24_8_ABSOLUTE_LIMIT = Decimal("10000000000000000")

_ROW_ID_PATTERN = re.compile(r"^row_[0-9]{6}$")
_TASK_ID_PATTERN = re.compile(r"^bsm-mig-v2-[0-9a-f]{24}$")
_DECIMAL8_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_NONNEGATIVE_DECIMAL8_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.[0-9]{8}$"
)

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
_RESULT_FIELDS = (
    "row_id",
    "iv_status",
    "market_implied_volatility",
    "unit_delta",
    "unit_gamma",
    "unit_vega_1volpt",
    "unit_theta_1calendar_day",
    "unit_rho_1pct",
)
_SUBMISSION_FIELDS = (
    "task_id",
    "submission_schema_version",
    "method_id",
    "status",
    "rows",
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


_PUBLIC_TABLES = (
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
        tuple(_Column(name, duckdb_type) for name, duckdb_type in (
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
        )),
        ("valuation_date", "underlying_id"),
    ),
    _Table(
        "solver_visible.option_quote_inputs",
        tuple(_Column(name, duckdb_type) for name, duckdb_type in (
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
        )),
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

_PUBLIC_JOIN_FIELDS = (
    "task_id",
    "snapshot_id",
    "valuation_date",
    "underlying_id",
)


def _require_pinned_duckdb() -> None:
    if duckdb.__version__ != _PINNED_DUCKDB_VERSION:
        raise RuntimeError(
            "trusted package verifier requires duckdb=="
            f"{_PINNED_DUCKDB_VERSION}, found {duckdb.__version__}"
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


def _load_json_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON contract must be an object: {path}")
    return value


def digest_file(path: str | Path) -> str:
    """Return the package's frozen SHA-256 file identity."""

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
    if isinstance(value, bool) or not isinstance(
        value, (str, int, float, Decimal)
    ):
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
    if result < 0:
        raise ValueError(f"{field} must be non-negative")
    return stored


@dataclass(frozen=True)
class BSMMarketGreeksInput:
    """One validated solver-visible quote row."""

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
        if not isinstance(self.row_id, str) or not _ROW_ID_PATTERN.fullmatch(
            self.row_id
        ):
            raise ValueError("row_id must use row_000001 format")
        if not isinstance(self.task_id, str) or not _TASK_ID_PATTERN.fullmatch(
            self.task_id
        ):
            raise ValueError("task_id does not match the market-Greeks identity")
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
        if self.calendar != "WeekendsOnly":
            raise ValueError("market-Greeks task requires the WeekendsOnly calendar")
        if self.day_count != "Actual365Fixed":
            raise ValueError("market-Greeks task requires Actual365Fixed")
        if self.exercise_style != "european":
            raise ValueError("market-Greeks task requires European exercise")
        if self.settlement_type != "cash":
            raise ValueError("market-Greeks task requires cash settlement")

        spot = _binary64(self.spot, "spot")
        strike = _binary64(self.strike, "strike")
        tau = _binary64(
            self.time_to_expiry_actual365, "time_to_expiry_actual365"
        )
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
            float(self.strike),
            self.call_put,
            self.option_id,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BSMMarketGreeksInput:
        if not isinstance(value, Mapping) or set(value) != set(_INPUT_FIELDS):
            raise ValueError("market-Greeks input has missing or extra fields")
        return cls(**{field: value[field] for field in _INPUT_FIELDS})


@dataclass(frozen=True)
class _CanonicalMarketGreeksRow:
    row_id: str
    iv_status: str
    market_implied_volatility: str
    unit_delta: str
    unit_gamma: str
    unit_vega_1volpt: str
    unit_theta_1calendar_day: str
    unit_rho_1pct: str

    def __post_init__(self) -> None:
        if not isinstance(self.row_id, str) or not _ROW_ID_PATTERN.fullmatch(
            self.row_id
        ):
            raise ValueError("canonical result has an invalid row_id")
        if self.iv_status != _SUCCESS_IV_STATUS:
            raise ValueError("canonical result has an invalid IV status")
        if (
            not isinstance(self.market_implied_volatility, str)
            or not _NONNEGATIVE_DECIMAL8_PATTERN.fullmatch(
                self.market_implied_volatility
            )
            or Decimal(self.market_implied_volatility) <= 0
        ):
            raise ValueError("market implied volatility must be positive decimal8")
        for field in (
            "unit_delta",
            "unit_gamma",
            "unit_vega_1volpt",
            "unit_theta_1calendar_day",
            "unit_rho_1pct",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not _DECIMAL8_PATTERN.fullmatch(value):
                raise ValueError(f"{field} must be a canonical decimal8 string")
            if value == "-0.00000000":
                raise ValueError(f"{field} must not contain negative zero")
        for field in ("unit_gamma", "unit_vega_1volpt"):
            if not _NONNEGATIVE_DECIMAL8_PATTERN.fullmatch(getattr(self, field)):
                raise ValueError(f"{field} must be non-negative")

    def to_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in _RESULT_FIELDS}

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> _CanonicalMarketGreeksRow:
        if not isinstance(value, Mapping) or set(value) != set(_RESULT_FIELDS):
            raise ValueError("market-Greeks row has missing or extra fields")
        return cls(**{field: value[field] for field in _RESULT_FIELDS})


@dataclass(frozen=True)
class MarketGreeksSubmission:
    """Strict canonical submission contract used by the packaged pytest suite."""

    task_id: str
    rows: tuple[_CanonicalMarketGreeksRow, ...]
    submission_schema_version: str = _SUBMISSION_SCHEMA_VERSION
    method_id: str = _METHOD_ID
    status: str = "completed"

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _TASK_ID_PATTERN.fullmatch(
            self.task_id
        ):
            raise ValueError("submission task_id is invalid")
        if self.submission_schema_version != _SUBMISSION_SCHEMA_VERSION:
            raise ValueError("submission schema version is invalid")
        if self.method_id != _METHOD_ID:
            raise ValueError("submission method ID is invalid")
        if self.status != "completed":
            raise ValueError("submission status must be completed")
        if not self.rows:
            raise ValueError("submission must contain rows")
        row_ids = [row.row_id for row in self.rows]
        expected = [f"row_{index:06d}" for index in range(1, len(self.rows) + 1)]
        if row_ids != expected:
            raise ValueError("submission rows are missing, duplicated, or reordered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "submission_schema_version": self.submission_schema_version,
            "method_id": self.method_id,
            "status": self.status,
            "rows": [row.to_dict() for row in self.rows],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MarketGreeksSubmission:
        if not isinstance(value, Mapping) or set(value) != set(_SUBMISSION_FIELDS):
            raise ValueError("market-Greeks submission has missing or extra fields")
        raw_rows = value["rows"]
        if not isinstance(raw_rows, list):
            raise ValueError("submission rows must be an array")
        return cls(
            task_id=value["task_id"],
            submission_schema_version=value["submission_schema_version"],
            method_id=value["method_id"],
            status=value["status"],
            rows=tuple(_CanonicalMarketGreeksRow.from_mapping(row) for row in raw_rows),
        )


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


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
        raise ValueError("BSM Greeks database relations differ from the allowlist")
    if connection.execute(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'market'"
    ).fetchone()[0]:
        raise ValueError("BSM Greeks database must not contain a market schema")
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


def _load_inputs_from_connection(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[BSMMarketGreeksInput, ...]:
    tables = _PUBLIC_TABLES[1:]
    raw = []
    for table in tables:
        order = ",".join(table.order_by)
        raw.append(
            tuple(
                dict(zip(table.column_names, row, strict=True))
                for row in connection.execute(
                    f"SELECT * FROM {table.name} ORDER BY {order}"
                ).fetchall()
            )
        )
    underlying_rows, option_rows = raw
    if len(underlying_rows) != 8 or len(option_rows) != 160:
        raise ValueError("BSM Greeks database has invalid public row counts")
    underlying_by_key = {}
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
        joined.append(BSMMarketGreeksInput.from_mapping(mapping))
    return tuple(joined)


def _validate_public_connection(connection: duckdb.DuckDBPyConnection) -> None:
    _validate_schema(connection)
    metadata = connection.execute("SELECT * FROM metadata.public_task").fetchall()
    if len(metadata) != 1:
        raise ValueError("BSM Greeks database requires one metadata row")
    meta = metadata[0]
    if (
        meta[0] != _DATABASE_SCHEMA_VERSION
        or not isinstance(meta[1], str)
        or not _TASK_ID_PATTERN.fullmatch(meta[1])
        or meta[2:5] != ("bsm_greeks", _TASK_VERSION, _VARIANT_ID)
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
        raise ValueError("BSM Greeks metadata identity is invalid")

    rows = _load_inputs_from_connection(connection)
    if len(rows) != 160 or len({row.underlying_id for row in rows}) != 8:
        raise ValueError("BSM Greeks database does not contain the golden grid")
    if any(
        row.task_id != meta[1]
        or row.snapshot_id != meta[5]
        or row.valuation_date != meta[8]
        for row in rows
    ):
        raise ValueError("BSM Greeks inputs differ from the metadata identity")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, 161))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("BSM Greeks input row IDs are not canonical")

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


def bsm_greeks_logical_checksum(database: str | Path) -> str:
    """Validate and hash the canonical logical rows of the three-table DB."""

    _require_pinned_duckdb()
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM Greeks database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection)
        hasher = sha256()
        hasher.update(f"{_LOGICAL_CHECKSUM_ID}\n".encode("utf-8"))
        for table in _PUBLIC_TABLES:
            header = {
                "table": table.name,
                "columns": [
                    f"{column.name}:{column.duckdb_type}"
                    for column in table.columns
                ],
            }
            hasher.update(_canonical_json_text(header).encode("utf-8") + b"\n")
            order = ",".join(table.order_by)
            for row in connection.execute(
                f"SELECT * FROM {table.name} ORDER BY {order}"
            ).fetchall():
                encoded = [_hash_value(value) for value in row]
                hasher.update(
                    _canonical_json_text(encoded).encode("utf-8") + b"\n"
                )
        return hasher.hexdigest()
    finally:
        connection.close()


def load_bsm_greeks_inputs(
    database: str | Path,
) -> tuple[BSMMarketGreeksInput, ...]:
    """Load the validated public rows in their frozen canonical order."""

    _require_pinned_duckdb()
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"BSM Greeks database is missing: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_public_connection(connection)
        return _load_inputs_from_connection(connection)
    finally:
        connection.close()


def verify_package_data_identity(package_root: str | Path) -> None:
    """Verify the two frozen public database identities declared by the package."""

    root = Path(package_root)
    manifest = _load_json_object(root / "manifest.json")
    database = root / "public/task.duckdb"
    try:
        expected_logical = manifest["public_child_snapshot"]["logical_checksum"]
        expected_file = manifest["artifacts"]["public/task.duckdb"]
    except (KeyError, TypeError) as error:
        raise ValueError("package manifest lacks public database identities") from error
    if bsm_greeks_logical_checksum(database) != expected_logical:
        raise ValueError("package database logical checksum mismatch")
    if digest_file(database) != expected_file:
        raise ValueError("package database file digest mismatch")


@dataclass(frozen=True)
class _NormalizedInput:
    source: BSMMarketGreeksInput
    spot: float
    strike: float
    tau: float
    risk_free_rate: float
    dividend_yield: float
    observed_price: float


def _normalize_input(task_input: BSMMarketGreeksInput) -> _NormalizedInput:
    bid = _quote_decimal(task_input.bid, "bid")
    ask = _quote_decimal(task_input.ask, "ask")
    observed_price = float((bid + ask) / Decimal(2))
    if not math.isfinite(observed_price):
        raise ValueError("observed midpoint must be finite in binary64")
    return _NormalizedInput(
        source=task_input,
        spot=_binary64(task_input.spot, "spot"),
        strike=_binary64(task_input.strike, "strike"),
        tau=_binary64(
            task_input.time_to_expiry_actual365, "time_to_expiry_actual365"
        ),
        risk_free_rate=_binary64(task_input.risk_free_rate, "risk_free_rate"),
        dividend_yield=_binary64(task_input.dividend_yield, "dividend_yield"),
        observed_price=observed_price,
    )


def _quantlib_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


@dataclass(frozen=True)
class _QuantLibValues:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


def _quantlib_values(task_input: _NormalizedInput, sigma: float) -> _QuantLibValues:
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
            spot,
            dividend_curve,
            risk_free_curve,
            volatility,
        )
        option_type = ql.Option.Call if source.call_put == "call" else ql.Option.Put
        payoff = ql.PlainVanillaPayoff(option_type, task_input.strike)
        option = ql.VanillaOption(payoff, ql.EuropeanExercise(expiry))
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        return _QuantLibValues(
            price=float(option.NPV()),
            delta=float(option.delta()),
            gamma=float(option.gamma()),
            vega=float(0.01 * option.vega()),
            theta=float(option.theta() / 365.0),
            rho=float(0.01 * option.rho()),
        )
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
        raise ValueError("published market-Greeks row is outside BSM price bounds")

    low = _LOWER_VOLATILITY
    high = _UPPER_VOLATILITY
    price_low = _quantlib_values(task_input, low).price
    price_high = _quantlib_values(task_input, high).price
    if not price_low <= task_input.observed_price <= price_high:
        raise ValueError("published market-Greeks row has no root in the bracket")
    for _ in range(_BISECTION_ITERATIONS):
        midpoint = (low + high) / 2.0
        if _quantlib_values(task_input, midpoint).price < task_input.observed_price:
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


def _expected_submission(
    task_inputs: Iterable[BSMMarketGreeksInput],
) -> MarketGreeksSubmission:
    rows = tuple(sorted(task_inputs, key=lambda item: item.canonical_order_key))
    if not rows or len({row.task_id for row in rows}) != 1:
        raise ValueError("trusted verifier requires one non-empty task")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, len(rows) + 1))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("trusted verifier input order is invalid")
    results = []
    for row in rows:
        normalized = _normalize_input(row)
        sigma = _market_implied_root(normalized)
        values = _quantlib_values(normalized, sigma)
        results.append(
            _CanonicalMarketGreeksRow(
                row_id=row.row_id,
                iv_status=_SUCCESS_IV_STATUS,
                market_implied_volatility=_canonical_decimal(sigma),
                unit_delta=_canonical_decimal(values.delta),
                unit_gamma=_canonical_decimal(values.gamma),
                unit_vega_1volpt=_canonical_decimal(values.vega),
                unit_theta_1calendar_day=_canonical_decimal(values.theta),
                unit_rho_1pct=_canonical_decimal(values.rho),
            )
        )
    return MarketGreeksSubmission(task_id=rows[0].task_id, rows=tuple(results))


_EXPECTED_ORACLE_CONFIG = {
    "oracle_config_schema_version": "bsm-greeks-oracle-config-v1.0.0",
    "verifier_id": "quantlib-bsm-market-implied-greeks-verifier-v1",
    "quantlib_version": _PINNED_QUANTLIB_VERSION,
    "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
    "iv_method_id": "bsm-bisection-float64-80-v1",
    "greeks_method_id": "bsm-analytic-float64-greeks-v1",
    "combined_method_id": _METHOD_ID,
    "pricing_measure_id": _RISK_NEUTRAL_MEASURE_ID,
    "numeraire_id": _NUMERAIRE_ID,
    "day_count": "Actual365Fixed",
    "rate_compounding": "continuous",
    "iterations": _BISECTION_ITERATIONS,
    "volatility_bracket": [_LOWER_VOLATILITY, _UPPER_VOLATILITY],
    "canonical_precision": 8,
    "canonical_rounding": "ROUND_HALF_EVEN",
}


def verify_market_greeks_submission(
    task_inputs: Iterable[BSMMarketGreeksInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any] | None = None,
) -> None:
    """Recompute truth from public rows and require exact canonical equality."""

    if method_config is not None and dict(method_config) != _EXPECTED_ORACLE_CONFIG:
        raise ValueError("trusted verifier received a different oracle config")
    expected = _expected_submission(task_inputs)
    try:
        actual = MarketGreeksSubmission.from_mapping(submission)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the market-Greeks schema") from error
    if actual.to_dict() != expected.to_dict():
        raise ValueError("market-Greeks canonical submission mismatch")


__all__ = [
    "BSMMarketGreeksInput",
    "MarketGreeksSubmission",
    "bsm_greeks_logical_checksum",
    "digest_file",
    "load_bsm_greeks_inputs",
    "verify_market_greeks_submission",
    "verify_package_data_identity",
]
