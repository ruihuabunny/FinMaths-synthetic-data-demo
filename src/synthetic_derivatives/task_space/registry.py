"""Config-driven compatibility registry for the six-dimensional task space."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.task_space.models import (
    AXES,
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
    coordinates: dict[str, frozenset[int]]
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

        if raw.get("schema_version") != "1.0.0":
            raise ValueError("unsupported task-space schema_version")
        self.registry_id = str(raw["registry_id"])
        axes = raw["axes"]
        if set(axes) != set(AXES):
            raise ValueError(f"task-space axes must be exactly {AXES}")
        self.bounds = {
            axis: (int(axes[axis]["min"]), int(axes[axis]["max"]))
            for axis in AXES
        }
        self.rules = tuple(self._parse_rule(item) for item in raw["compatibility_rules"])
        if not self.rules:
            raise ValueError("task-space registry requires compatibility rules")
        for rule in self.rules:
            for axis, values in rule.coordinates.items():
                lower, upper = self.bounds[axis]
                if any(value < lower or value > upper for value in values):
                    raise ValueError(
                        f"compatibility rule {rule.rule_id} has {axis} outside "
                        f"registry bounds [{lower}, {upper}]"
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
        coordinates = {
            axis: frozenset(int(value) for value in selectors.get(axis, []))
            for axis in AXES
        }
        return CompatibilityRule(
            rule_id=str(raw["rule_id"]),
            task_family_id=str(raw["task_family_id"]),
            coordinates=coordinates,
            reason=str(raw["reason"]),
        )

    def _bounds_error(self, coordinates: TaskCoordinates) -> str | None:
        """Describe the first out-of-range axis, if any."""

        for axis, value in coordinates.to_dict().items():
            lower, upper = self.bounds[axis]
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
