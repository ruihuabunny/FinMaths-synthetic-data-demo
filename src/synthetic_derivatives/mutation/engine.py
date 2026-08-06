"""Deterministic, compatibility-constrained task mutation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.task_space import AXES, TaskSpaceRegistry, TaskSpec


@dataclass(frozen=True)
class MutationOperator:
    """Axes, cardinality, and direction allowed by one mutation operation."""

    operator_id: str
    allowed_axes: frozenset[str]
    max_changed_axes: int
    direction: str


@dataclass(frozen=True)
class MutationLineage:
    """Replayable record connecting an immutable parent and child task."""

    parent_task_id: str
    child_task_id: str
    operator: str
    before: dict[str, int]
    after: dict[str, int]
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
    lineage: MutationLineage

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready child and lineage payload."""

        return {"task": self.task.to_dict(), "lineage": self.lineage.to_dict()}


class MutationEngine:
    """Change declared task coordinates without changing scheduler state."""

    def __init__(self, raw: Mapping[str, Any], registry: TaskSpaceRegistry):
        """Validate operator definitions and bind their compatibility registry."""

        if raw.get("schema_version") != "1.0.0":
            raise ValueError("unsupported mutation schema_version")
        self.engine_id = str(raw["engine_id"])
        self.registry = registry
        operator_items = list(raw["operators"])
        operator_ids = [item["operator_id"] for item in operator_items]
        if len(operator_ids) != len(set(operator_ids)):
            raise ValueError("mutation operator_id values must be unique")
        self.operators = {
            item["operator_id"]: MutationOperator(
                operator_id=item["operator_id"],
                allowed_axes=frozenset(item["allowed_axes"]),
                max_changed_axes=int(item["max_changed_axes"]),
                direction=item.get("direction", "any"),
            )
            for item in operator_items
        }
        if not self.operators:
            raise ValueError("mutation config requires at least one operator")
        for operator in self.operators.values():
            unknown = operator.allowed_axes - set(AXES)
            if unknown:
                raise ValueError(f"unknown mutation axes: {sorted(unknown)}")
            if operator.max_changed_axes < 1:
                raise ValueError("max_changed_axes must be positive")
            if operator.direction not in {"any", "increase", "decrease"}:
                raise ValueError(f"unsupported mutation direction: {operator.direction}")

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        registry: TaskSpaceRegistry,
    ) -> "MutationEngine":
        """Load a UTF-8 JSON mutation config and bind ``registry``."""

        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("mutation config must be a JSON object")
        return cls(raw, registry)

    def mutate(
        self,
        parent: TaskSpec,
        *,
        operator_id: str,
        changes: Mapping[str, int],
        seed: int,
        task_family_id: str | None = None,
        method_id: str | None = None,
        output_contract_id: str | None = None,
        snapshot_id: str | None = None,
        snapshot_revision: int | None = None,
    ) -> MutatedTask:
        """Create one compatible child and its reproducible lineage record.

        ``changes`` supplies the new coordinate values; this version does not
        sample them.  ``seed`` is part of child identity and lineage, but does
        not influence the supplied values in the current implementation.
        """

        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("mutation seed must be a non-negative integer")
        try:
            operator = self.operators[operator_id]
        except KeyError as error:
            raise ValueError(f"unknown mutation operator: {operator_id}") from error
        requested_axes = set(changes)
        if not requested_axes:
            raise ValueError("mutation must request at least one coordinate change")
        if not requested_axes <= operator.allowed_axes:
            disallowed = sorted(requested_axes - operator.allowed_axes)
            raise ValueError(f"operator {operator_id} cannot change axes {disallowed}")

        coordinates = parent.coordinates.with_changes(changes)
        changed_axes = parent.coordinates.changed_axes(coordinates)
        if not changed_axes:
            raise ValueError("mutation must change at least one coordinate value")
        if len(changed_axes) > operator.max_changed_axes:
            raise ValueError(
                f"operator {operator_id} changes {len(changed_axes)} axes; "
                f"maximum is {operator.max_changed_axes}"
            )
        self._validate_direction(parent, coordinates, changed_axes, operator)
        if "A" in changed_axes and method_id is None:
            raise ValueError("changing method axis A requires an explicit method_id")
        child_family = (
            parent.task_family_id if task_family_id is None else task_family_id
        )
        child_method = parent.method_id if method_id is None else method_id
        child_output_contract = (
            parent.output_contract_id
            if output_contract_id is None
            else output_contract_id
        )
        child_snapshot_id = parent.snapshot_id if snapshot_id is None else snapshot_id
        child_snapshot_revision = (
            parent.snapshot_revision
            if snapshot_revision is None
            else snapshot_revision
        )
        decision = self.registry.require_compatible(coordinates, child_family)
        coordinate_id = "-".join(
            f"{axis}{value}" for axis, value in coordinates.to_dict().items()
        )
        # Encode every permitted identity override, not just coordinates.  Two
        # children that point at different snapshots or methods must never
        # collapse to the same task ID.
        mutation_id = "-".join(
            (
                self.engine_id,
                operator_id,
                str(seed),
                coordinate_id,
                child_snapshot_id,
                f"r{child_snapshot_revision}",
                child_method,
                child_output_contract,
            )
        )
        child = TaskSpec(
            task_id=f"{parent.task_id}_m-{mutation_id}",
            task_family_id=child_family,
            coordinates=coordinates,
            snapshot_id=child_snapshot_id,
            snapshot_revision=child_snapshot_revision,
            method_id=child_method,
            output_contract_id=child_output_contract,
        )
        lineage = MutationLineage(
            parent_task_id=parent.task_id,
            child_task_id=child.task_id,
            operator=operator_id,
            before=parent.coordinates.to_dict(),
            after=coordinates.to_dict(),
            seed=seed,
            engine_id=self.engine_id,
            compatibility_rule_id=decision.rule_id,
        )
        return MutatedTask(child, lineage)

    @staticmethod
    def _validate_direction(parent, child, changed_axes, operator) -> None:
        """Enforce monotone operators on every axis that actually changed."""

        if operator.direction == "any":
            return
        for axis in changed_axes:
            before = getattr(parent.coordinates, axis)
            after = getattr(child, axis)
            if operator.direction == "increase" and after <= before:
                raise ValueError(f"operator {operator.operator_id} requires {axis} to increase")
            if operator.direction == "decrease" and after >= before:
                raise ValueError(f"operator {operator.operator_id} requires {axis} to decrease")
