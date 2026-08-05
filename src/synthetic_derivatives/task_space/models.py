"""Immutable task-space records shared by mutation and curriculum code."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping


AXES = ("L", "P", "M", "A", "D", "R")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(value: Mapping[str, Any]) -> str:
    """Hash a JSON-compatible mapping using the repository's canonical form."""

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
        keys = set(value)
        if keys != set(AXES):
            missing = sorted(set(AXES) - keys)
            extra = sorted(keys - set(AXES))
            raise ValueError(f"coordinates require exactly {AXES}; missing={missing}, extra={extra}")
        return cls(**{axis: value[axis] for axis in AXES})

    def to_dict(self) -> dict[str, int]:
        return {axis: getattr(self, axis) for axis in AXES}

    def changed_axes(self, other: "TaskCoordinates") -> tuple[str, ...]:
        return tuple(axis for axis in AXES if getattr(self, axis) != getattr(other, axis))

    def with_changes(self, changes: Mapping[str, int]) -> "TaskCoordinates":
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
    snapshot_hash: str
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
        if not _SHA256.fullmatch(self.snapshot_hash):
            raise ValueError("snapshot_hash must be a lowercase SHA-256 hex digest")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskSpec":
        return cls(
            task_id=value["task_id"],
            task_family_id=value["task_family_id"],
            coordinates=TaskCoordinates.from_mapping(value["coordinates"]),
            snapshot_id=value["snapshot_id"],
            snapshot_hash=value["snapshot_hash"],
            method_id=value["method_id"],
            output_contract_id=value["output_contract_id"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_family_id": self.task_family_id,
            "coordinates": self.coordinates.to_dict(),
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "method_id": self.method_id,
            "output_contract_id": self.output_contract_id,
        }

    @property
    def logical_hash(self) -> str:
        return canonical_sha256(self.to_dict())
