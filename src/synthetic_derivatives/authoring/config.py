"""Generator configuration parsing."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeterministicFunctionNode:
    day_offset: int
    value: float


@dataclass(frozen=True)
class DeterministicFunction:
    function_type: str
    nodes: tuple[DeterministicFunctionNode, ...]
    extrapolation: str = "flat"

    @property
    def initial_value(self) -> float:
        return self.value_at(0.0)

    @property
    def is_constant(self) -> bool:
        return self.function_type == "constant"

    def value_at(self, day_offset: float) -> float:
        if self.is_constant or day_offset <= self.nodes[0].day_offset:
            return self.nodes[0].value
        if day_offset >= self.nodes[-1].day_offset:
            return self.nodes[-1].value

        offsets = [node.day_offset for node in self.nodes]
        left_index = bisect_right(offsets, day_offset) - 1
        left = self.nodes[left_index]
        right = self.nodes[left_index + 1]
        weight = (day_offset - left.day_offset) / (
            right.day_offset - left.day_offset
        )
        return left.value + weight * (right.value - left.value)

    def interval_average(
        self, start_day_offset: float, end_day_offset: float, *, power: int = 1
    ) -> float:
        if end_day_offset <= start_day_offset:
            raise ValueError("deterministic-function interval must be positive")
        if power not in {1, 2}:
            raise ValueError(
                "deterministic-function integration supports powers 1 and 2"
            )
        if self.is_constant:
            return self.nodes[0].value**power

        interior_offsets = [
            float(node.day_offset)
            for node in self.nodes
            if start_day_offset < node.day_offset < end_day_offset
        ]
        boundaries = [start_day_offset, *interior_offsets, end_day_offset]
        integral = 0.0
        for left_offset, right_offset in zip(boundaries, boundaries[1:]):
            left_value = self.value_at(left_offset)
            right_value = self.value_at(right_offset)
            width = right_offset - left_offset
            if power == 1:
                integral += width * (left_value + right_value) / 2.0
            else:
                integral += width * (
                    left_value * left_value
                    + left_value * right_value
                    + right_value * right_value
                ) / 3.0
        return integral / (end_day_offset - start_day_offset)

    def interval_root_mean_square(
        self, start_day_offset: float, end_day_offset: float
    ) -> float:
        return math.sqrt(
            self.interval_average(start_day_offset, end_day_offset, power=2)
        )

    def as_dict(self) -> dict[str, Any]:
        if self.is_constant:
            return {"type": "constant", "value": self.nodes[0].value}
        return {
            "type": self.function_type,
            "nodes": [
                {"day_offset": node.day_offset, "value": node.value}
                for node in self.nodes
            ],
            "extrapolation": self.extrapolation,
        }


@dataclass(frozen=True)
class UnderlyingConfig:
    underlying_id: str
    initial_spot: Decimal
    physical_drift: float
    physical_volatility: float
    physical_drift_function: DeterministicFunction
    physical_volatility_function: DeterministicFunction
    risk_free_rate: float
    dividend_yield: float
    base_implied_volatility: float


@dataclass(frozen=True)
class OptionTemplate:
    """One canonical cell in the authored option grid.

    Legacy configs populate ``strike_moneyness`` without a ``chain_id``.
    Config 1.3 chain cells populate exactly one of ``strike_moneyness`` and
    ``strike_absolute``; both are construction inputs, not mutable daily state.
    The generator later materializes either input as one frozen contract strike.
    """

    template_id: str
    call_put: str
    strike_moneyness: Decimal | None
    expiry_days: int
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal
    chain_id: str | None = None
    strike_absolute: Decimal | None = None


@dataclass(frozen=True)
class OptionChainConfig:
    """Immutable rules used to expand one complete static option chain.

    Exactly one of ``moneyness_grid`` and ``strike_grid`` is populated.
    Moneyness is a listing-time construction input; an absolute grid is already
    in strike units.  Both paths apply the declared increment and materialize a
    frozen contract strike that is never recomputed from later spot observations.

    The first implementation deliberately supports one listing at the snapshot
    start and no rolling.  Later listing/roll behavior requires a new versioned
    contract rather than implicit date-dependent behavior.
    """

    chain_id: str
    expiry_days: tuple[int, ...]
    moneyness_grid: tuple[Decimal, ...] | None
    strike_grid: tuple[Decimal, ...] | None
    call_put: tuple[str, ...]
    listing_rule: str
    roll_rule: str
    strike_increment: Decimal
    strike_rounding: str
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal


class OptionChainBuilder:
    """Expand a validated chain specification into stable option templates.

    This class is intentionally deterministic and contains no market-path or RNG
    input.  Correlation belongs to underlying simulation and cannot change the
    option grid, template IDs, or listed strikes.
    """

    def __init__(self, chain: OptionChainConfig):
        self.chain = chain

    def build_templates(self) -> tuple[OptionTemplate, ...]:
        """Return the canonical expiry/grid/call-put Cartesian product.

        The loop order is part of authoring canonicalization.  IDs also encode
        their economic inputs, so their identity does not depend on row position.
        """

        grid = self.chain.moneyness_grid or self.chain.strike_grid or ()
        return tuple(
            OptionTemplate(
                template_id=self._template_id(expiry_days, strike_input, call_put),
                call_put=call_put,
                strike_moneyness=(
                    strike_input
                    if self.chain.moneyness_grid is not None
                    else None
                ),
                expiry_days=expiry_days,
                exercise_style=self.chain.exercise_style,
                settlement_type=self.chain.settlement_type,
                contract_multiplier=self.chain.contract_multiplier,
                chain_id=self.chain.chain_id,
                strike_absolute=(
                    strike_input if self.chain.strike_grid is not None else None
                ),
            )
            for expiry_days in self.chain.expiry_days
            for strike_input in grid
            for call_put in self.chain.call_put
        )

    def absolute_strike(
        self,
        listing_spot: Decimal,
        moneyness: Decimal | None,
        absolute_strike: Decimal | None = None,
    ) -> Decimal:
        """Freeze one moneyness or absolute-strike input to its listed strike.

        Rounding occurs in units of ``strike_increment`` before the database's
        eight-decimal price canonicalization.  This method is called while the
        contract master is built, never once per valuation date.
        """

        if (moneyness is None) == (absolute_strike is None):
            raise ValueError(
                "exactly one of moneyness and absolute_strike is required"
            )
        if moneyness is not None:
            raw_strike = listing_spot * moneyness
        else:
            if absolute_strike is None:
                raise ValueError("absolute_strike is required")
            raw_strike = absolute_strike
        increment_units = raw_strike / self.chain.strike_increment
        rounded_units = increment_units.quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
        return rounded_units * self.chain.strike_increment

    def _template_id(
        self, expiry_days: int, strike_input: Decimal, call_put: str
    ) -> str:
        """Encode economic grid identity without using generation position."""

        strike_token = format(strike_input.normalize(), "f").replace(".", "p")
        grid_prefix = "M" if self.chain.moneyness_grid is not None else "K"
        return (
            f"{self.chain.chain_id}-{call_put.upper()}-"
            f"{expiry_days:04d}D-{grid_prefix}{strike_token}"
        )


@dataclass(frozen=True)
class UnderlyingSimulationConfig:
    """Canonical dependence contract for P-measure underlying spot paths.

    The author supplies ``factor_loading_matrix`` (Lambda) in ``driver_order``.
    Config parsing derives the idiosyncratic diagonal D and correlation matrix
    R = Lambda Lambda^T + D; a separately supplied R is deliberately rejected.
    Drivers are underlying IDs, never option or other derivative contract IDs.

    The first implementation supports one constant, business-daily regime.  A
    future time-varying implementation must persist its regime/latent state so
    incremental generation can restart from the complete Markov state.
    """

    dependence_spec_id: str
    measure: str
    driver_order: tuple[str, ...]
    formulation: str
    factor_loading_matrix: tuple[tuple[float, ...], ...]
    idiosyncratic_diagonal: tuple[float, ...]
    correlation_matrix: tuple[tuple[float, ...], ...]
    matrix_dtype: str
    factorization_method: str
    factorization_order: str
    time_grid: str
    regime_id: str

    @property
    def factor_count(self) -> int:
        """Return the number of shared Gaussian factors in Lambda."""

        return len(self.factor_loading_matrix[0])

    def driver_index(self, underlying_id: str) -> int:
        """Resolve an underlying ID to its canonical matrix row."""

        try:
            return self.driver_order.index(underlying_id)
        except ValueError as error:
            raise ValueError(
                f"underlying driver is not declared: {underlying_id}"
            ) from error


@dataclass(frozen=True)
class GeneratorConfig:
    """Validated, immutable input contract for one authoring snapshot."""

    schema_version: str
    generator_config_id: str
    generator_version: str
    snapshot_id: str
    seed: int
    rng: str
    start_date: date
    business_days: int
    calendar: str
    day_count: str
    valuation_time_utc: str
    pricing_model: str
    pricing_engine: str
    physical_process: str
    currency: str
    quote_decimal_places: int
    underlyings: tuple[UnderlyingConfig, ...]
    option_templates: tuple[OptionTemplate, ...]
    smile: dict[str, float]
    quote_model: dict[str, Any]
    underlying_simulation: UnderlyingSimulationConfig | None
    option_chain: OptionChainConfig | None


def load_generator_config(path: str | Path) -> GeneratorConfig:
    """Load and validate a versioned generator configuration.

    Versions 1.0/1.1 retain the legacy independent underlying streams.  Version
    1.2 requires the explicit P-measure ``underlying_simulation`` contract.
    Version 1.3 additionally replaces individually authored option templates
    with an explicit, static ``option_chain`` Cartesian-product contract.
    """

    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("generator config must be a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version not in {"1.0.0", "1.1.0", "1.2.0", "1.3.0"}:
        raise ValueError("unsupported generator config schema_version")

    underlyings_list: list[UnderlyingConfig] = []
    for item in raw["underlyings"]:
        if schema_version == "1.0.0" and any(
            isinstance(item[field], dict)
            for field in ("physical_drift", "physical_volatility")
        ):
            raise ValueError(
                "deterministic physical functions require schema_version 1.1.0"
            )
        drift_function = _parse_deterministic_function(
            item["physical_drift"], field_name="physical_drift"
        )
        volatility_function = _parse_deterministic_function(
            item["physical_volatility"], field_name="physical_volatility"
        )
        underlyings_list.append(
            UnderlyingConfig(
                underlying_id=item["underlying_id"],
                initial_spot=Decimal(str(item["initial_spot"])),
                physical_drift=drift_function.initial_value,
                physical_volatility=volatility_function.initial_value,
                physical_drift_function=drift_function,
                physical_volatility_function=volatility_function,
                risk_free_rate=float(item["risk_free_rate"]),
                dividend_yield=float(item["dividend_yield"]),
                base_implied_volatility=float(item["base_implied_volatility"]),
            )
        )
    underlyings = tuple(underlyings_list)
    if schema_version == "1.3.0":
        if "option_templates" in raw:
            raise ValueError(
                "schema_version 1.3.0 uses option_chain, not option_templates"
            )
        option_chain = _parse_option_chain(raw.get("option_chain"))
        builder = OptionChainBuilder(option_chain)
        templates = builder.build_templates()
        _validate_chain_strikes(underlyings, builder)
    else:
        if "option_chain" in raw:
            raise ValueError("option_chain requires schema_version 1.3.0")
        option_chain = None
        templates = tuple(
            OptionTemplate(
                template_id=item["template_id"],
                call_put=item["call_put"],
                strike_moneyness=Decimal(str(item["strike_moneyness"])),
                expiry_days=int(item["expiry_days"]),
                exercise_style=item["exercise_style"],
                settlement_type=item["settlement_type"],
                contract_multiplier=Decimal(str(item["contract_multiplier"])),
            )
            for item in raw["option_templates"]
        )
    _validate_entities(underlyings, templates)
    if schema_version in {"1.2.0", "1.3.0"}:
        underlying_simulation = _parse_underlying_simulation(
            raw.get("underlying_simulation"), underlyings
        )
    else:
        if "underlying_simulation" in raw:
            raise ValueError(
                "underlying_simulation requires schema_version 1.2.0 or 1.3.0"
            )
        underlying_simulation = None
    return GeneratorConfig(
        schema_version=schema_version,
        generator_config_id=raw["generator_config_id"],
        generator_version=raw["generator_version"],
        snapshot_id=raw["snapshot_id"],
        seed=int(raw["seed"]),
        rng=raw["rng"],
        start_date=date.fromisoformat(raw["start_date"]),
        business_days=int(raw["business_days"]),
        calendar=raw["calendar"],
        day_count=raw["day_count"],
        valuation_time_utc=raw["valuation_time_utc"],
        pricing_model=raw["pricing_model"],
        pricing_engine=raw["pricing_engine"],
        physical_process=raw["physical_process"],
        currency=raw["currency"],
        quote_decimal_places=int(raw["quote_decimal_places"]),
        underlyings=underlyings,
        option_templates=templates,
        smile={key: float(value) for key, value in raw["smile"].items()},
        quote_model=dict(raw["quote_model"]),
        underlying_simulation=underlying_simulation,
        option_chain=option_chain,
    )


def _parse_option_chain(raw: Any) -> OptionChainConfig:
    """Validate and canonicalize the first static option-chain contract.

    Requiring sorted unique grids and normalizing call/put order makes equivalent
    configs generate the same tuple order.  Dynamic listings and rolling are
    rejected explicitly because they require persisted exchange-calendar state.
    """

    if not isinstance(raw, dict):
        raise ValueError("schema_version 1.3.0 requires option_chain")
    chain_id = raw.get("chain_id")
    if not isinstance(chain_id, str) or not chain_id:
        raise ValueError("option_chain.chain_id must be non-empty")

    raw_expiry_days = raw.get("expiry_days")
    if (
        not isinstance(raw_expiry_days, list)
        or not raw_expiry_days
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in raw_expiry_days
        )
    ):
        raise ValueError("option_chain.expiry_days must be a non-empty integer array")
    expiry_days = tuple(raw_expiry_days)
    if any(value <= 0 for value in expiry_days) or any(
        right <= left for left, right in zip(expiry_days, expiry_days[1:])
    ):
        raise ValueError(
            "option_chain.expiry_days must be positive and strictly increasing"
        )

    has_moneyness_grid = "moneyness_grid" in raw
    has_strike_grid = "strike_grid" in raw
    if has_moneyness_grid == has_strike_grid:
        raise ValueError(
            "option_chain requires exactly one of moneyness_grid or strike_grid"
        )
    grid_field = "moneyness_grid" if has_moneyness_grid else "strike_grid"
    raw_grid = raw.get(grid_field)
    if not isinstance(raw_grid, list) or not raw_grid:
        raise ValueError(f"option_chain.{grid_field} must be a non-empty array")
    try:
        parsed_grid = tuple(Decimal(str(value)) for value in raw_grid)
    except Exception as error:
        raise ValueError(
            f"option_chain.{grid_field} must contain finite decimals"
        ) from error
    if any(not value.is_finite() or value <= 0 for value in parsed_grid) or any(
        right <= left for left, right in zip(parsed_grid, parsed_grid[1:])
    ):
        raise ValueError(
            f"option_chain.{grid_field} must be finite, positive and strictly increasing"
        )
    moneyness_grid = parsed_grid if has_moneyness_grid else None
    strike_grid = parsed_grid if has_strike_grid else None

    raw_call_put = raw.get("call_put")
    if not isinstance(raw_call_put, list) or set(raw_call_put) != {"call", "put"}:
        raise ValueError("option_chain.call_put must contain paired call and put")
    if len(raw_call_put) != 2:
        raise ValueError("option_chain.call_put must not contain duplicates")
    call_put = ("call", "put")

    if raw.get("listing_rule") != "snapshot_start":
        raise ValueError("option_chain.listing_rule must be snapshot_start")
    if raw.get("roll_rule") != "static":
        raise ValueError("option_chain.roll_rule must be static")
    if raw.get("strike_rounding") != "ROUND_HALF_EVEN":
        raise ValueError(
            "option_chain.strike_rounding must be ROUND_HALF_EVEN"
        )
    try:
        strike_increment = Decimal(str(raw.get("strike_increment")))
        contract_multiplier = Decimal(str(raw.get("contract_multiplier")))
    except Exception as error:
        raise ValueError(
            "option_chain strike_increment and contract_multiplier must be decimals"
        ) from error
    if (
        not strike_increment.is_finite()
        or strike_increment <= 0
        or not contract_multiplier.is_finite()
        or contract_multiplier <= 0
    ):
        raise ValueError(
            "option_chain strike_increment and contract_multiplier must be positive"
        )
    if strike_increment.quantize(
        Decimal("0.00000001"), rounding=ROUND_HALF_EVEN
    ) != strike_increment:
        raise ValueError(
            "option_chain.strike_increment supports at most 8 decimal places"
        )
    if raw.get("exercise_style") != "european":
        raise ValueError("option_chain.exercise_style must be european")
    settlement_type = raw.get("settlement_type")
    if not isinstance(settlement_type, str) or not settlement_type:
        raise ValueError("option_chain.settlement_type must be non-empty")
    return OptionChainConfig(
        chain_id=chain_id,
        expiry_days=expiry_days,
        moneyness_grid=moneyness_grid,
        strike_grid=strike_grid,
        call_put=call_put,
        listing_rule="snapshot_start",
        roll_rule="static",
        strike_increment=strike_increment,
        strike_rounding="ROUND_HALF_EVEN",
        exercise_style="european",
        settlement_type=settlement_type,
        contract_multiplier=contract_multiplier,
    )


def _validate_chain_strikes(
    underlyings: tuple[UnderlyingConfig, ...], builder: OptionChainBuilder
) -> None:
    """Reject a grid whose exchange rounding collapses distinct contracts.

    Validation is per underlying because listing-moneyness grids use each
    underlying's own listing spot.  A collision would otherwise create two
    stable IDs for the same economic expiry/call-put/strike contract.
    """

    for underlying in underlyings:
        grid = builder.chain.moneyness_grid or builder.chain.strike_grid or ()
        strikes = [
            builder.absolute_strike(
                underlying.initial_spot,
                strike_input if builder.chain.moneyness_grid is not None else None,
                strike_input if builder.chain.strike_grid is not None else None,
            )
            for strike_input in grid
        ]
        if any(strike <= 0 for strike in strikes) or len(strikes) != len(set(strikes)):
            raise ValueError(
                "option_chain grid collapses after strike rounding for "
                f"{underlying.underlying_id}"
            )


def _parse_underlying_simulation(
    raw: Any,
    underlyings: tuple[UnderlyingConfig, ...],
) -> UnderlyingSimulationConfig:
    """Validate Lambda and deterministically derive D and R in float64 order.

    Row norms are checked against the decimal input and again after conversion
    to the declared float64 representation.  This makes positive-semidefiniteness
    a property of the factor construction instead of a tolerance-based eigenvalue
    repair performed after the fact.
    """

    if not isinstance(raw, dict):
        raise ValueError(
            "schema_version 1.2.0 or 1.3.0 requires underlying_simulation"
        )

    dependence_spec_id = raw.get("dependence_spec_id")
    if not isinstance(dependence_spec_id, str) or not dependence_spec_id:
        raise ValueError("underlying_simulation.dependence_spec_id must be non-empty")
    if raw.get("measure") != "P":
        raise ValueError("underlying_simulation.measure must be P")
    if raw.get("formulation") != "factor_loading":
        raise ValueError(
            "underlying_simulation.formulation must be factor_loading"
        )
    if raw.get("idiosyncratic_diagonal") != "derive_from_row_norms":
        raise ValueError(
            "underlying_simulation.idiosyncratic_diagonal must be "
            "derive_from_row_norms"
        )
    if "correlation_matrix" in raw:
        raise ValueError(
            "underlying_simulation.correlation_matrix is derived from factor loadings"
        )
    if raw.get("matrix_dtype") != "float64":
        raise ValueError("underlying_simulation.matrix_dtype must be float64")
    if raw.get("factorization_method") != "factor_loading_direct":
        raise ValueError(
            "underlying_simulation.factorization_method must be "
            "factor_loading_direct"
        )
    if raw.get("factorization_order") != "declared_driver_order":
        raise ValueError(
            "underlying_simulation.factorization_order must be "
            "declared_driver_order"
        )
    if raw.get("time_grid") != "business_daily":
        raise ValueError(
            "underlying_simulation.time_grid must be business_daily"
        )
    regime_id = raw.get("regime_id")
    if not isinstance(regime_id, str) or not regime_id:
        raise ValueError("underlying_simulation.regime_id must be non-empty")

    raw_driver_order = raw.get("driver_order")
    if not isinstance(raw_driver_order, list) or not raw_driver_order or any(
        not isinstance(driver, str) or not driver for driver in raw_driver_order
    ):
        raise ValueError(
            "underlying_simulation.driver_order must be a non-empty string array"
        )
    driver_order = tuple(raw_driver_order)
    if len(driver_order) != len(set(driver_order)):
        raise ValueError("underlying_simulation.driver_order must be unique")
    underlying_ids = {underlying.underlying_id for underlying in underlyings}
    if set(driver_order) != underlying_ids:
        raise ValueError(
            "underlying_simulation.driver_order must contain every underlying_id "
            "exactly once"
        )

    raw_matrix = raw.get("factor_loading_matrix")
    if not isinstance(raw_matrix, list) or len(raw_matrix) != len(driver_order):
        raise ValueError(
            "underlying_simulation.factor_loading_matrix row count must match "
            "driver_order"
        )
    if not raw_matrix or any(not isinstance(row, list) for row in raw_matrix):
        raise ValueError(
            "underlying_simulation.factor_loading_matrix must be a matrix"
        )
    factor_count = len(raw_matrix[0])
    if factor_count < 1 or any(len(row) != factor_count for row in raw_matrix):
        raise ValueError(
            "underlying_simulation.factor_loading_matrix must be non-ragged "
            "with at least one factor"
        )

    factor_loading_rows: list[tuple[float, ...]] = []
    idiosyncratic_diagonal: list[float] = []
    for row_index, raw_row in enumerate(raw_matrix):
        row = tuple(
            _finite_float(
                value,
                field_name=(
                    "underlying_simulation.factor_loading_matrix"
                    f"[{row_index}][{column_index}]"
                ),
            )
            for column_index, value in enumerate(raw_row)
        )
        exact_row_norm_squared = sum(
            (Decimal(str(value)) ** 2 for value in raw_row), Decimal("0")
        )
        if exact_row_norm_squared > Decimal("1"):
            raise ValueError(
                "underlying_simulation factor-loading row norm must not exceed 1: "
                f"{driver_order[row_index]}"
            )
        row_norm_squared = math.fsum(value * value for value in row)
        if row_norm_squared > 1.0:
            raise ValueError(
                "underlying_simulation factor-loading row norm exceeds 1 in "
                f"float64: {driver_order[row_index]}"
            )
        factor_loading_rows.append(row)
        idiosyncratic_diagonal.append(max(0.0, 1.0 - row_norm_squared))

    factor_loading_matrix = tuple(factor_loading_rows)
    diagonal = tuple(idiosyncratic_diagonal)
    correlation_matrix = tuple(
        tuple(
            1.0
            if row_index == column_index
            else math.fsum(
                factor_loading_matrix[row_index][factor_index]
                * factor_loading_matrix[column_index][factor_index]
                for factor_index in range(factor_count)
            )
            for column_index in range(len(driver_order))
        )
        for row_index in range(len(driver_order))
    )
    return UnderlyingSimulationConfig(
        dependence_spec_id=dependence_spec_id,
        measure="P",
        driver_order=driver_order,
        formulation="factor_loading",
        factor_loading_matrix=factor_loading_matrix,
        idiosyncratic_diagonal=diagonal,
        correlation_matrix=correlation_matrix,
        matrix_dtype="float64",
        factorization_method="factor_loading_direct",
        factorization_order="declared_driver_order",
        time_grid="business_daily",
        regime_id=regime_id,
    )


def _parse_deterministic_function(
    raw: Any, *, field_name: str
) -> DeterministicFunction:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a number or deterministic function")
    if isinstance(raw, (int, float)):
        value = _finite_float(raw, field_name=field_name)
        return DeterministicFunction(
            function_type="constant",
            nodes=(DeterministicFunctionNode(day_offset=0, value=value),),
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a number or deterministic function")

    function_type = raw.get("type")
    if function_type == "constant":
        value = _finite_float(raw.get("value"), field_name=f"{field_name}.value")
        return DeterministicFunction(
            function_type="constant",
            nodes=(DeterministicFunctionNode(day_offset=0, value=value),),
        )
    if function_type != "piecewise_linear":
        raise ValueError(f"unsupported {field_name} function type: {function_type}")
    if raw.get("extrapolation", "flat") != "flat":
        raise ValueError(f"{field_name} supports flat extrapolation only")

    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) < 2:
        raise ValueError(f"{field_name} piecewise_linear requires at least two nodes")
    nodes: list[DeterministicFunctionNode] = []
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            raise ValueError(f"{field_name}.nodes[{index}] must be an object")
        day_offset = raw_node.get("day_offset")
        if isinstance(day_offset, bool) or not isinstance(day_offset, int):
            raise ValueError(
                f"{field_name}.nodes[{index}].day_offset must be an integer"
            )
        nodes.append(
            DeterministicFunctionNode(
                day_offset=day_offset,
                value=_finite_float(
                    raw_node.get("value"),
                    field_name=f"{field_name}.nodes[{index}].value",
                ),
            )
        )
    offsets = [node.day_offset for node in nodes]
    if offsets[0] != 0:
        raise ValueError(f"{field_name} first day_offset must be 0")
    if any(right <= left for left, right in zip(offsets, offsets[1:])):
        raise ValueError(f"{field_name} day_offset values must be strictly increasing")
    return DeterministicFunction(
        function_type="piecewise_linear", nodes=tuple(nodes), extrapolation="flat"
    )


def _finite_float(raw: Any, *, field_name: str) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite number") from error
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    return value


def _validate_entities(
    underlyings: tuple[UnderlyingConfig, ...],
    templates: tuple[OptionTemplate, ...],
) -> None:
    if not underlyings or not templates:
        raise ValueError("config requires at least one underlying and one option template")
    underlying_ids = [item.underlying_id for item in underlyings]
    template_ids = [item.template_id for item in templates]
    if len(underlying_ids) != len(set(underlying_ids)):
        raise ValueError("underlying_id values must be unique")
    if len(template_ids) != len(set(template_ids)):
        raise ValueError("option template_id values must be unique")
    for item in underlyings:
        if item.initial_spot <= 0:
            raise ValueError(f"initial_spot must be positive: {item.underlying_id}")
        if (
            any(node.value <= 0 for node in item.physical_volatility_function.nodes)
            or item.base_implied_volatility <= 0
        ):
            raise ValueError(f"volatility must be positive: {item.underlying_id}")
    for item in templates:
        if item.call_put not in {"call", "put"}:
            raise ValueError(f"unsupported call_put: {item.call_put}")
        if item.exercise_style != "european":
            raise ValueError("the v1 pipeline supports European exercise only")
        if (
            (item.strike_moneyness is None) == (item.strike_absolute is None)
            or (
                item.strike_moneyness is not None
                and (
                    not item.strike_moneyness.is_finite()
                    or item.strike_moneyness <= 0
                )
            )
            or (
                item.strike_absolute is not None
                and (
                    not item.strike_absolute.is_finite()
                    or item.strike_absolute <= 0
                )
            )
            or item.expiry_days <= 0
            or not item.contract_multiplier.is_finite()
            or item.contract_multiplier <= 0
        ):
            raise ValueError(f"invalid option template: {item.template_id}")


def option_id(underlying_id: str, template_id: str) -> str:
    return f"{underlying_id}-{template_id}"
