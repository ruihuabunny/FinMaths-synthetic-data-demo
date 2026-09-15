"""Deterministic, compatibility-constrained task mutation."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.model_families import (
    EXECUTABLE_CAPABILITY_STATUSES,
    ExecutableCapabilityRegistry,
)
from synthetic_derivatives.mutation.models import (
    FamilyLineage,
    FamilyMutatedTask,
    FamilyOperator,
    Lineage,
    MutatedTask,
    Operator,
)
from synthetic_derivatives.task_space import (
    AXES,
    TaskSpaceRegistry,
    TaskSpec,
    TaskSpecV3,
    coordinate_rank,
)
from synthetic_derivatives.task_space.models import CoordinateValue

# These names were historically defined in this module.  Keep them as aliases
# so direct imports and persisted pickle references continue to resolve while
# the model definitions live in ``mutation.models``.
MutationLineage = Lineage
MutationOperator = Operator

_FAMILY_IDENTITY_FIELDS = frozenset(
    {
        "task_family_id",
        "task_kind_id",
        "solver_interface_id",
        "method_id",
        "output_contract_id",
        "snapshot_id",
        "snapshot_revision",
    }
)


class MutationEngine:
    """Change declared task coordinates without changing scheduler state."""

    def __init__(self, raw: Mapping[str, Any], registry: TaskSpaceRegistry):
        """Validate operator definitions and bind their compatibility registry."""

        if raw.get("schema_version") != "2.0.0":
            raise ValueError("unsupported mutation schema_version")
        self.engine_id = str(raw["engine_id"])
        self.registry = registry
        operator_items = list(raw["operators"])
        operator_ids = [item["operator_id"] for item in operator_items]
        if len(operator_ids) != len(set(operator_ids)):
            raise ValueError("mutation operator_id values must be unique")
        self.operators = {
            item["operator_id"]: Operator(
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
        changes: Mapping[str, CoordinateValue],
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
        coordinate_id = coordinates.canonical_id
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
        lineage = Lineage(
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
            before = coordinate_rank(axis, getattr(parent.coordinates, axis))
            after = coordinate_rank(axis, getattr(child, axis))
            if operator.direction == "increase" and after <= before:
                raise ValueError(f"operator {operator.operator_id} requires {axis} to increase")
            if operator.direction == "decrease" and after >= before:
                raise ValueError(f"operator {operator.operator_id} requires {axis} to decrease")


class FamilyAwareMutationEngine:
    """Mutate semantic tasks while keeping model family and ``M`` immutable."""

    def __init__(
        self,
        raw: Mapping[str, Any],
        registry: TaskSpaceRegistry,
        capabilities: ExecutableCapabilityRegistry,
    ):
        expected = {
            "schema_version",
            "engine_id",
            "model_family_id",
            "immutable_fields",
            "operators",
        }
        keys = set(raw)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "family mutation config has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        if raw["schema_version"] != "family-mutation-v1.0.0":
            raise ValueError("unsupported family mutation schema_version")
        self.engine_id = str(raw["engine_id"])
        if not self.engine_id:
            raise ValueError("family mutation engine_id must not be empty")
        self.model_family_id = str(raw["model_family_id"])
        capabilities.model_families.require(self.model_family_id)
        immutable_fields = raw["immutable_fields"]
        if immutable_fields != ["model_family_id", "coordinates.M"]:
            raise ValueError(
                "family mutation immutable_fields must exactly freeze "
                "model_family_id and coordinates.M"
            )
        if (
            capabilities.design_catalog is None
            or capabilities.design_catalog.registry_id != registry.registry_id
        ):
            raise ValueError(
                "family mutation requires the capability registry's bound design catalog"
            )
        self.registry = registry
        self.capabilities = capabilities
        raw_operators = raw["operators"]
        if not isinstance(raw_operators, list) or not raw_operators:
            raise ValueError("family mutation config requires operators")
        parsed = tuple(self._parse_operator(item) for item in raw_operators)
        operator_ids = [item.operator_id for item in parsed]
        if len(operator_ids) != len(set(operator_ids)):
            raise ValueError("family mutation operator_id values must be unique")
        self.operators = {item.operator_id: item for item in parsed}

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        registry: TaskSpaceRegistry,
        capabilities: ExecutableCapabilityRegistry,
    ) -> "FamilyAwareMutationEngine":
        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, Mapping):
            raise ValueError("family mutation config must be a JSON object")
        return cls(raw, registry, capabilities)

    @staticmethod
    def _parse_operator(raw: Mapping[str, Any]) -> FamilyOperator:
        expected = {
            "operator_id",
            "allowed_axes",
            "max_changed_axes",
            "direction",
            "allowed_identity_fields",
            "max_changed_identity_fields",
        }
        keys = set(raw)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "family mutation operator has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        operator_id = raw["operator_id"]
        if not isinstance(operator_id, str) or not operator_id:
            raise ValueError("family mutation operator_id must be non-empty")
        allowed_axes_raw = raw["allowed_axes"]
        if not isinstance(allowed_axes_raw, list) or any(
            not isinstance(item, str) for item in allowed_axes_raw
        ):
            raise ValueError("family mutation allowed_axes must be an array of strings")
        if len(allowed_axes_raw) != len(set(allowed_axes_raw)):
            raise ValueError("family mutation allowed_axes must not contain duplicates")
        allowed_axes = frozenset(allowed_axes_raw)
        unknown_axes = allowed_axes - set(AXES)
        if unknown_axes:
            raise ValueError(f"unknown family mutation axes: {sorted(unknown_axes)}")
        if "M" in allowed_axes:
            raise ValueError("ordinary family mutation operators cannot allow M")
        identity_raw = raw["allowed_identity_fields"]
        if not isinstance(identity_raw, list) or any(
            not isinstance(item, str) for item in identity_raw
        ):
            raise ValueError(
                "family mutation allowed_identity_fields must be an array of strings"
            )
        if len(identity_raw) != len(set(identity_raw)):
            raise ValueError(
                "family mutation allowed_identity_fields must not contain duplicates"
            )
        allowed_identity_fields = frozenset(identity_raw)
        unknown_identity = allowed_identity_fields - _FAMILY_IDENTITY_FIELDS
        if unknown_identity:
            raise ValueError(
                "unknown family mutation identity fields: "
                f"{sorted(unknown_identity)}"
            )
        max_changed_axes = raw["max_changed_axes"]
        max_changed_identity_fields = raw["max_changed_identity_fields"]
        for value, name in (
            (max_changed_axes, "max_changed_axes"),
            (max_changed_identity_fields, "max_changed_identity_fields"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"family mutation {name} must be non-negative")
        if max_changed_axes == 0 and max_changed_identity_fields == 0:
            raise ValueError("family mutation operator must permit some change")
        direction = raw["direction"]
        if direction not in {"any", "increase", "decrease"}:
            raise ValueError(f"unsupported family mutation direction: {direction}")
        return FamilyOperator(
            operator_id=operator_id,
            allowed_axes=allowed_axes,
            max_changed_axes=max_changed_axes,
            direction=direction,
            allowed_identity_fields=allowed_identity_fields,
            max_changed_identity_fields=max_changed_identity_fields,
        )

    def mutate(
        self,
        parent: TaskSpecV3,
        *,
        operator_id: str,
        changes: Mapping[str, CoordinateValue],
        seed: int,
        model_family_id: str | None = None,
        task_family_id: str | None = None,
        task_kind_id: str | None = None,
        solver_interface_id: str | None = None,
        method_id: str | None = None,
        output_contract_id: str | None = None,
        snapshot_id: str | None = None,
        snapshot_revision: int | None = None,
    ) -> FamilyMutatedTask:
        """Create one capability-backed child with complete semantic lineage."""

        if not isinstance(parent, TaskSpecV3):
            raise TypeError("family mutation requires TaskSpecV3")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("mutation seed must be a non-negative integer")
        if parent.model_family_id != self.model_family_id:
            raise ValueError(
                "parent model family does not match the family mutation engine"
            )
        requested_family = (
            parent.model_family_id
            if model_family_id is None
            else model_family_id
        )
        if requested_family != parent.model_family_id:
            raise ValueError("ordinary mutation cannot change model_family_id")
        self.capabilities.model_families.require_matching_coordinates(
            parent.model_family_id, parent.coordinates
        )
        self.capabilities.require_task(
            parent, required_statuses=EXECUTABLE_CAPABILITY_STATUSES
        )
        try:
            operator = self.operators[operator_id]
        except KeyError as error:
            raise ValueError(f"unknown family mutation operator: {operator_id}") from error
        requested_axes = set(changes)
        unknown_axes = requested_axes - set(AXES)
        if unknown_axes:
            raise ValueError(f"unknown coordinate axes: {sorted(unknown_axes)}")
        if "M" in requested_axes:
            raise ValueError("ordinary mutation cannot change model coordinate M")
        if not requested_axes <= operator.allowed_axes:
            disallowed = sorted(requested_axes - operator.allowed_axes)
            raise ValueError(f"operator {operator_id} cannot change axes {disallowed}")
        coordinates = parent.coordinates.with_changes(changes)
        changed_axes = parent.coordinates.changed_axes(coordinates)
        if len(changed_axes) > operator.max_changed_axes:
            raise ValueError(
                f"operator {operator_id} changes {len(changed_axes)} axes; maximum "
                f"is {operator.max_changed_axes}"
            )
        MutationEngine._validate_direction(
            parent, coordinates, changed_axes, operator
        )
        if "A" in changed_axes and (
            method_id is None or method_id == parent.method_id
        ):
            raise ValueError("changing method axis A requires a new method_id")
        if "D" in changed_axes and (
            solver_interface_id is None
            or solver_interface_id == parent.solver_interface_id
        ):
            raise ValueError(
                "changing data/tool axis D requires a new solver_interface_id"
            )
        if "R" in changed_axes and (
            output_contract_id is None
            or output_contract_id == parent.output_contract_id
        ):
            raise ValueError("changing output axis R requires a new output_contract_id")

        overrides: dict[str, Any] = {
            "task_family_id": task_family_id,
            "task_kind_id": task_kind_id,
            "solver_interface_id": solver_interface_id,
            "method_id": method_id,
            "output_contract_id": output_contract_id,
            "snapshot_id": snapshot_id,
            "snapshot_revision": snapshot_revision,
        }
        changed_identity_fields = tuple(
            field
            for field, value in overrides.items()
            if value is not None and value != getattr(parent, field)
        )
        disallowed_identity = (
            set(changed_identity_fields) - operator.allowed_identity_fields
        )
        if disallowed_identity:
            raise ValueError(
                f"operator {operator_id} cannot change identity fields "
                f"{sorted(disallowed_identity)}"
            )
        if len(changed_identity_fields) > operator.max_changed_identity_fields:
            raise ValueError(
                f"operator {operator_id} changes {len(changed_identity_fields)} "
                "identity fields; maximum is "
                f"{operator.max_changed_identity_fields}"
            )
        if not changed_axes and not changed_identity_fields:
            raise ValueError("family mutation must change coordinates or semantic identity")

        values = {
            field: getattr(parent, field) if value is None else value
            for field, value in overrides.items()
        }
        candidate = TaskSpecV3(
            task_id=parent.task_id,
            model_family_id=parent.model_family_id,
            task_family_id=values["task_family_id"],
            task_kind_id=values["task_kind_id"],
            solver_interface_id=values["solver_interface_id"],
            coordinates=coordinates,
            snapshot_id=values["snapshot_id"],
            snapshot_revision=values["snapshot_revision"],
            method_id=values["method_id"],
            output_contract_id=values["output_contract_id"],
        )
        capability = self.capabilities.require_task(
            candidate, required_statuses=EXECUTABLE_CAPABILITY_STATUSES
        )
        compatibility = self.registry.require_compatible(
            candidate.coordinates, capability.catalog_task_family_id
        )
        assert compatibility.rule_id is not None
        identity_material = {
            "parent_task_id": parent.task_id,
            "engine_id": self.engine_id,
            "operator_id": operator_id,
            "seed": seed,
            "child_identity": candidate.identity_dict(),
        }
        encoded = json.dumps(
            identity_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        child = replace(
            candidate,
            task_id=f"{parent.task_id}_fm-{sha256(encoded).hexdigest()[:24]}",
        )
        lineage = FamilyLineage(
            parent_task_id=parent.task_id,
            child_task_id=child.task_id,
            operator=operator_id,
            before_identity=parent.identity_dict(),
            after_identity=child.identity_dict(),
            seed=seed,
            engine_id=self.engine_id,
            capability_registry_id=self.capabilities.registry_id,
            compatibility_rule_id=compatibility.rule_id,
        )
        return FamilyMutatedTask(child, lineage)


# Concise compatibility name for callers that do not use the scheduler naming.
FamilyMutationEngine = FamilyAwareMutationEngine
