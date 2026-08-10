"""Config-driven compatibility registry for the seven-dimensional task space."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.task_space.models import (
    AXES,
    F_LEVELS,
    LEGACY_AXES,
    CoordinateValue,
    TaskCoordinates,
    TaskSpec,
)


@dataclass(frozen=True)
class CompatibilityDecision:
    """Result of matching coordinates against the ordered compatibility rules."""

    compatible: bool
    rule_id: str | None
    task_family_id: str | None
    reason: str


@dataclass(frozen=True)
class CompatibilityRule:
    """One product-family rule with optional selectors on each task axis.

    An empty selector is a wildcard.  Non-empty selectors enumerate all values
    accepted on that axis.
    """

    rule_id: str
    task_family_id: str
    coordinates: dict[str, frozenset[CoordinateValue]]
    reason: str

    def matches(self, coordinates: TaskCoordinates) -> bool:
        """Return whether all non-wildcard selectors admit ``coordinates``."""

        return all(
            not allowed or getattr(coordinates, axis) in allowed
            for axis, allowed in self.coordinates.items()
        )


class TaskSpaceRegistry:
    """Validate coordinate bounds and product/model/method compatibility."""

    def __init__(self, raw: Mapping[str, Any]):
        """Validate and compile one task-space registry mapping.

        Rule order is preserved because :meth:`evaluate` returns the first
        matching rule.  Config authors should therefore place a more specific
        overlapping rule before a broader one.
        """

        if raw.get("schema_version") != "2.0.0":
            raise ValueError("unsupported task-space schema_version")
        self.registry_id = str(raw["registry_id"])
        axes = raw["axes"]
        if set(axes) != set(AXES):
            raise ValueError(f"task-space axes must be exactly {AXES}")
        self.bounds: dict[str, tuple[int, int]] = {}
        for axis in LEGACY_AXES:
            minimum = axes[axis]["min"]
            maximum = axes[axis]["max"]
            if (
                isinstance(minimum, bool)
                or not isinstance(minimum, int)
                or isinstance(maximum, bool)
                or not isinstance(maximum, int)
                or minimum < 0
                or maximum < minimum
            ):
                raise ValueError(f"invalid numeric bounds for axis {axis}")
            self.bounds[axis] = (minimum, maximum)
        f_values = axes["F"].get("values")
        if not isinstance(f_values, list) or tuple(f_values) != F_LEVELS:
            raise ValueError("task-space F values must exactly match the runtime enum")
        self.f_levels = F_LEVELS
        self.rules = tuple(self._parse_rule(item) for item in raw["compatibility_rules"])
        if not self.rules:
            raise ValueError("task-space registry requires compatibility rules")
        for rule in self.rules:
            for axis, values in rule.coordinates.items():
                if axis == "F":
                    invalid = [value for value in values if value not in self.f_levels]
                    expected = str(self.f_levels)
                else:
                    lower, upper = self.bounds[axis]
                    invalid = [
                        value
                        for value in values
                        if not isinstance(value, int)
                        or isinstance(value, bool)
                        or value < lower
                        or value > upper
                    ]
                    expected = f"[{lower}, {upper}]"
                if invalid:
                    raise ValueError(
                        f"compatibility rule {rule.rule_id} has {axis} outside "
                        f"registry values {expected}"
                    )
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("compatibility rule_id values must be unique")

    @classmethod
    def from_path(cls, path: str | Path) -> "TaskSpaceRegistry":
        """Load a UTF-8 JSON registry from ``path``."""

        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("task-space config must be a JSON object")
        return cls(raw)

    def _parse_rule(self, raw: Mapping[str, Any]) -> CompatibilityRule:
        """Normalize omitted axis selectors to wildcard sets."""

        selectors = raw["coordinates"]
        unknown = set(selectors) - set(AXES)
        if unknown:
            raise ValueError(f"unknown compatibility axes: {sorted(unknown)}")
        coordinates: dict[str, frozenset[CoordinateValue]] = {}
        for axis in AXES:
            values = selectors.get(axis, [])
            if not isinstance(values, list):
                raise ValueError(f"compatibility selector {axis} must be a list")
            if axis == "F":
                if any(not isinstance(value, str) for value in values):
                    raise ValueError("compatibility selector F requires strings")
                coordinates[axis] = frozenset(values)
            else:
                if any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in values
                ):
                    raise ValueError(
                        f"compatibility selector {axis} requires integers"
                    )
                coordinates[axis] = frozenset(values)
        return CompatibilityRule(
            rule_id=str(raw["rule_id"]),
            task_family_id=str(raw["task_family_id"]),
            coordinates=coordinates,
            reason=str(raw["reason"]),
        )

    def _bounds_error(self, coordinates: TaskCoordinates) -> str | None:
        """Describe the first out-of-range axis, if any."""

        for axis, value in coordinates.to_dict().items():
            if axis == "F":
                if value not in self.f_levels:
                    return f"F={value!r} is outside registry values {self.f_levels}"
                continue
            lower, upper = self.bounds[axis]
            assert isinstance(value, int)
            if not lower <= value <= upper:
                return f"{axis}={value} is outside registry bounds [{lower}, {upper}]"
        return None

    def evaluate(
        self, coordinates: TaskCoordinates, task_family_id: str | None = None
    ) -> CompatibilityDecision:
        """Return the first compatibility decision without raising.

        ``task_family_id`` narrows matching to one family when supplied.  A
        coordinate may be within global bounds and still be incompatible when
        no product/model/method rule admits the combination.
        """

        bounds_error = self._bounds_error(coordinates)
        if bounds_error:
            return CompatibilityDecision(False, None, task_family_id, bounds_error)
        for rule in self.rules:
            if task_family_id is not None and rule.task_family_id != task_family_id:
                continue
            if rule.matches(coordinates):
                return CompatibilityDecision(
                    True, rule.rule_id, rule.task_family_id, rule.reason
                )
        family = f" for task family {task_family_id!r}" if task_family_id else ""
        return CompatibilityDecision(
            False,
            None,
            task_family_id,
            f"no compatible product/model/method rule{family}",
        )

    def require_compatible(
        self, coordinates: TaskCoordinates, task_family_id: str | None = None
    ) -> CompatibilityDecision:
        """Return a compatible decision or raise ``ValueError`` with its reason."""

        decision = self.evaluate(coordinates, task_family_id)
        if not decision.compatible:
            raise ValueError(f"incompatible task coordinates: {decision.reason}")
        return decision

    def validate_task(self, task: TaskSpec) -> CompatibilityDecision:
        """Validate a task against the rule for its declared family."""

        return self.require_compatible(task.coordinates, task.task_family_id)
