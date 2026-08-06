"""Immutable task-space records shared by mutation and curriculum code."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


# This order is part of the serialized task identity and deterministic child IDs.
AXES = ("L", "P", "M", "A", "D", "R")


@dataclass(frozen=True)
class TaskCoordinates:
    """The six independent difficulty coordinates ``(L, P, M, A, D, R)``."""

    L: int
    P: int
    M: int
    A: int
    D: int
    R: int

    def __post_init__(self) -> None:
        for axis, value in self.to_dict().items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{axis} must be a non-negative integer")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskCoordinates":
        """Parse a mapping that contains every axis exactly once."""

        keys = set(value)
        if keys != set(AXES):
            missing = sorted(set(AXES) - keys)
            extra = sorted(keys - set(AXES))
            raise ValueError(f"coordinates require exactly {AXES}; missing={missing}, extra={extra}")
        return cls(**{axis: value[axis] for axis in AXES})

    def to_dict(self) -> dict[str, int]:
        """Serialize coordinates in canonical ``AXES`` order."""

        return {axis: getattr(self, axis) for axis in AXES}

    def changed_axes(self, other: "TaskCoordinates") -> tuple[str, ...]:
        """Return changed axis names in canonical order."""

        return tuple(axis for axis in AXES if getattr(self, axis) != getattr(other, axis))

    def with_changes(self, changes: Mapping[str, int]) -> "TaskCoordinates":
        """Return a validated copy with the requested coordinate replacements."""

        unknown = set(changes) - set(AXES)
        if unknown:
            raise ValueError(f"unknown coordinate axes: {sorted(unknown)}")
        return replace(self, **dict(changes))


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
