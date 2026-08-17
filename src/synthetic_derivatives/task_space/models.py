"""Immutable task-space records shared by mutation and curriculum code."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


# This order is part of serialized task identity and deterministic child IDs.
LEGACY_AXES = ("L", "P", "M", "A", "D", "R")
AXES = (*LEGACY_AXES, "F")
F_LEVELS = (
    "F0",
    "F1",
    "F2A",
    "F2B",
    "F3A",
    "F3B",
    "F4A",
    "F4B",
    "F5A",
    "F5B",
    "F6A",
    "F6B",
)
CoordinateValue = int | str


@dataclass(frozen=True)
class TaskCoordinates:
    """The seven difficulty coordinates ``(L, P, M, A, D, R, F)``."""

    L: int
    P: int
    M: int
    A: int
    D: int
    R: int
    F: str

    def __post_init__(self) -> None:
        for axis in LEGACY_AXES:
            value = getattr(self, axis)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{axis} must be a non-negative integer")
        if not isinstance(self.F, str) or self.F not in F_LEVELS:
            raise ValueError(f"F must be one of {F_LEVELS}")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskCoordinates":
        """Parse a mapping that contains every axis exactly once."""

        keys = set(value)
        if keys != set(AXES):
            missing = sorted(set(AXES) - keys)
            extra = sorted(keys - set(AXES))
            raise ValueError(f"coordinates require exactly {AXES}; missing={missing}, extra={extra}")
        return cls(**{axis: value[axis] for axis in AXES})

    def to_dict(self) -> dict[str, CoordinateValue]:
        """Serialize coordinates in canonical ``AXES`` order."""

        return {axis: getattr(self, axis) for axis in AXES}

    def changed_axes(self, other: "TaskCoordinates") -> tuple[str, ...]:
        """Return changed axis names in canonical order."""

        return tuple(axis for axis in AXES if getattr(self, axis) != getattr(other, axis))

    def with_changes(
        self, changes: Mapping[str, CoordinateValue]
    ) -> "TaskCoordinates":
        """Return a validated copy with the requested coordinate replacements."""

        unknown = set(changes) - set(AXES)
        if unknown:
            raise ValueError(f"unknown coordinate axes: {sorted(unknown)}")
        return replace(self, **dict(changes))

    @property
    def canonical_id(self) -> str:
        """Return the coordinate component used by deterministic identities."""

        return "-".join(
            str(getattr(self, axis))
            if axis == "F"
            else f"{axis}{getattr(self, axis)}"
            for axis in AXES
        )


def coordinate_rank(axis: str, value: CoordinateValue) -> int:
    """Map an axis value to its declared monotone structural order."""

    if axis == "F":
        if not isinstance(value, str) or value not in F_LEVELS:
            raise ValueError(f"F must be one of {F_LEVELS}")
        return F_LEVELS.index(value)
    if axis not in LEGACY_AXES:
        raise ValueError(f"unknown coordinate axis: {axis}")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{axis} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class TaskSpec:
    """Public identity fields for one immutable task variant."""

    task_id: str
    task_family_id: str
    coordinates: TaskCoordinates
    snapshot_id: str
    snapshot_revision: int
    method_id: str
    output_contract_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "task_id",
            "task_family_id",
            "snapshot_id",
            "method_id",
            "output_contract_id",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must not be empty")
        if self.snapshot_revision < 1:
            raise ValueError("snapshot_revision must be positive")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskSpec":
        """Construct one task from its public JSON representation."""

        return cls(
            task_id=value["task_id"],
            task_family_id=value["task_family_id"],
            coordinates=TaskCoordinates.from_mapping(value["coordinates"]),
            snapshot_id=value["snapshot_id"],
            snapshot_revision=value["snapshot_revision"],
            method_id=value["method_id"],
            output_contract_id=value["output_contract_id"],
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable public representation used by configs and lineage."""

        return {
            "task_id": self.task_id,
            "task_family_id": self.task_family_id,
            "coordinates": self.coordinates.to_dict(),
            "snapshot_id": self.snapshot_id,
            "snapshot_revision": self.snapshot_revision,
            "method_id": self.method_id,
            "output_contract_id": self.output_contract_id,
        }


@dataclass(frozen=True, slots=True)
class TaskSpecV3:
    """Semantic task identity with model, work, target, and ABI separated.

    This semantic version is unrelated to package/runtime/toolset/query
    protocols that also happen to use a ``v3`` label.  It does not replace the
    legacy :class:`TaskSpec`; callers must opt into an explicit adapter.
    """

    task_id: str
    model_family_id: str
    task_family_id: str
    task_kind_id: str
    solver_interface_id: str
    coordinates: TaskCoordinates
    snapshot_id: str
    snapshot_revision: int
    method_id: str
    output_contract_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "task_id",
            "model_family_id",
            "task_family_id",
            "task_kind_id",
            "solver_interface_id",
            "snapshot_id",
            "method_id",
            "output_contract_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.coordinates, TaskCoordinates):
            raise TypeError("coordinates must be TaskCoordinates")
        if (
            isinstance(self.snapshot_revision, bool)
            or not isinstance(self.snapshot_revision, int)
            or self.snapshot_revision < 1
        ):
            raise ValueError("snapshot_revision must be a positive integer")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskSpecV3":
        """Parse the exact closed public representation of a semantic task."""

        fields = (
            "task_id",
            "model_family_id",
            "task_family_id",
            "task_kind_id",
            "solver_interface_id",
            "coordinates",
            "snapshot_id",
            "snapshot_revision",
            "method_id",
            "output_contract_id",
        )
        keys = set(value)
        expected = set(fields)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "semantic task requires exactly its v3 identity fields; "
                f"missing={missing}, extra={extra}"
            )
        coordinates = value["coordinates"]
        if not isinstance(coordinates, Mapping):
            raise ValueError("coordinates must be an object")
        return cls(
            task_id=value["task_id"],
            model_family_id=value["model_family_id"],
            task_family_id=value["task_family_id"],
            task_kind_id=value["task_kind_id"],
            solver_interface_id=value["solver_interface_id"],
            coordinates=TaskCoordinates.from_mapping(coordinates),
            snapshot_id=value["snapshot_id"],
            snapshot_revision=value["snapshot_revision"],
            method_id=value["method_id"],
            output_contract_id=value["output_contract_id"],
        )

    @classmethod
    def from_legacy(
        cls,
        task: TaskSpec,
        *,
        model_family_id: str,
        task_family_id: str,
        task_kind_id: str,
        solver_interface_id: str,
    ) -> "TaskSpecV3":
        """Explicitly adapt one legacy task without guessing new identities."""

        if not isinstance(task, TaskSpec):
            raise TypeError("task must be a legacy TaskSpec")
        return cls(
            task_id=task.task_id,
            model_family_id=model_family_id,
            task_family_id=task_family_id,
            task_kind_id=task_kind_id,
            solver_interface_id=solver_interface_id,
            coordinates=task.coordinates,
            snapshot_id=task.snapshot_id,
            snapshot_revision=task.snapshot_revision,
            method_id=task.method_id,
            output_contract_id=task.output_contract_id,
        )

    @property
    def capability_identity(self) -> tuple[str, str, str, str, str]:
        """Return the five-field executable capability key in stable order."""

        return (
            self.model_family_id,
            self.task_family_id,
            self.task_kind_id,
            self.method_id,
            self.solver_interface_id,
        )

    def identity_dict(self) -> dict[str, Any]:
        """Return all deterministic child-identity material except task ID."""

        return {
            "model_family_id": self.model_family_id,
            "task_family_id": self.task_family_id,
            "task_kind_id": self.task_kind_id,
            "solver_interface_id": self.solver_interface_id,
            "coordinates": self.coordinates.to_dict(),
            "snapshot_id": self.snapshot_id,
            "snapshot_revision": self.snapshot_revision,
            "method_id": self.method_id,
            "output_contract_id": self.output_contract_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the stable semantic task-v3 representation."""

        return {"task_id": self.task_id, **self.identity_dict()}


def migrate_legacy_six_axis_coordinates(
    value: Mapping[str, Any],
) -> TaskCoordinates:
    """Explicitly migrate one exact legacy coordinate mapping to ``F0``."""

    keys = set(value)
    if keys != set(LEGACY_AXES):
        missing = sorted(set(LEGACY_AXES) - keys)
        extra = sorted(keys - set(LEGACY_AXES))
        raise ValueError(
            "legacy coordinates require exactly "
            f"{LEGACY_AXES}; missing={missing}, extra={extra}"
        )
    migrated = {axis: value[axis] for axis in LEGACY_AXES}
    migrated["F"] = "F0"
    return TaskCoordinates.from_mapping(migrated)


def migrate_legacy_six_axis_task(value: Mapping[str, Any]) -> TaskSpec:
    """Explicitly migrate a legacy public task while preserving its task ID."""

    coordinates = value.get("coordinates")
    if not isinstance(coordinates, Mapping):
        raise ValueError("legacy task coordinates must be a mapping")
    migrated = dict(value)
    migrated["coordinates"] = migrate_legacy_six_axis_coordinates(
        coordinates
    ).to_dict()
    return TaskSpec.from_mapping(migrated)
