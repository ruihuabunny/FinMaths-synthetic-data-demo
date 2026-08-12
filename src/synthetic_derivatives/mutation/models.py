"""Immutable records used by the task-mutation workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synthetic_derivatives.task_space import TaskSpec
from synthetic_derivatives.task_space.models import CoordinateValue


@dataclass(frozen=True)
class Operator:
    """Axes, cardinality, and direction allowed by one mutation operation."""

    operator_id: str
    allowed_axes: frozenset[str]
    max_changed_axes: int
    direction: str


@dataclass(frozen=True)
class Lineage:
    """Replayable record connecting an immutable parent and child task."""

    parent_task_id: str
    child_task_id: str
    operator: str
    before: dict[str, CoordinateValue]
    after: dict[str, CoordinateValue]
    seed: int
    engine_id: str
    compatibility_rule_id: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready lineage record."""

        return {
            "parent_task_id": self.parent_task_id,
            "child_task_id": self.child_task_id,
            "operator": self.operator,
            "before": self.before,
            "after": self.after,
            "seed": self.seed,
            "engine_id": self.engine_id,
            "compatibility_rule_id": self.compatibility_rule_id,
        }


@dataclass(frozen=True)
class MutatedTask:
    """A generated child task bundled with its required lineage."""

    task: TaskSpec
    lineage: Lineage

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready child and lineage payload."""

        return {"task": self.task.to_dict(), "lineage": self.lineage.to_dict()}


__all__ = ["Lineage", "MutatedTask", "Operator"]
