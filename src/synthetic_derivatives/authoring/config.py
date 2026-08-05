"""Generator configuration parsing."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
    template_id: str
    call_put: str
    strike_moneyness: Decimal
    expiry_days: int
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal


@dataclass(frozen=True)
class GeneratorConfig:
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


def load_generator_config(path: str | Path) -> GeneratorConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("generator config must be a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version not in {"1.0.0", "1.1.0"}:
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
    return GeneratorConfig(
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
        if item.strike_moneyness <= 0 or item.expiry_days <= 0:
            raise ValueError(f"invalid option template: {item.template_id}")


def option_id(underlying_id: str, template_id: str) -> str:
    return f"{underlying_id}-{template_id}"
