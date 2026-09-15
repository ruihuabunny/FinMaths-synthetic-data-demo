"""Deterministic physical-parameter functions and their parser."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeterministicFunctionNode:
    """One value on a calendar-day-offset deterministic parameter curve."""

    day_offset: int
    value: float


@dataclass(frozen=True)
class DeterministicFunction:
    """Constant or piecewise-linear parameter function anchored at start date."""

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
        """Return the exact interval mean of ``f`` or ``f**2``."""

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


def finite_float(raw: Any, *, field_name: str) -> float:
    """Convert one numeric input while rejecting bool, NaN and infinities."""

    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite number") from error
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    return value


def parse_deterministic_function(
    raw: Any, *, field_name: str
) -> DeterministicFunction:
    """Normalize a scalar or JSON function definition to one internal type."""

    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a number or deterministic function")
    if isinstance(raw, (int, float)):
        value = finite_float(raw, field_name=field_name)
        return DeterministicFunction(
            function_type="constant",
            nodes=(DeterministicFunctionNode(day_offset=0, value=value),),
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a number or deterministic function")

    function_type = raw.get("type")
    if function_type == "constant":
        value = finite_float(raw.get("value"), field_name=f"{field_name}.value")
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
                value=finite_float(
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
