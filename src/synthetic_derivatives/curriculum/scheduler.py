"""Adaptive sampling over immutable tasks; hard-verifier rewards stay binary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from synthetic_derivatives.model_families import (
    ExecutableCapabilityRegistry,
    GatedTaskPool,
)
from synthetic_derivatives.task_space import (
    AXES,
    F_LEVELS,
    TaskCoordinates,
    TaskSpec,
    TaskSpecV3,
)
from synthetic_derivatives.task_space.models import CoordinateValue


@dataclass(frozen=True)
class CurriculumStage:
    """One ordered curriculum stage described by axis selectors."""

    index: int
    stage_id: str
    coordinates: dict[str, frozenset[CoordinateValue]]

    def matches(self, coordinates: TaskCoordinates) -> bool:
        """Return whether all non-empty selectors admit ``coordinates``."""

        return all(
            not allowed or getattr(coordinates, axis) in allowed
            for axis, allowed in self.coordinates.items()
        )


@dataclass(frozen=True)
class MasteryBand:
    """Pass-rate interval and sampling multiplier for tasks in that interval."""

    minimum: float
    maximum: float
    action: str
    weight_multiplier: float


@dataclass(frozen=True)
class FamilyCurriculumStage:
    """One family-local stage over already capability-gated semantic tasks."""

    index: int
    stage_id: str
    task_family_ids: frozenset[str]
    task_kind_ids: frozenset[str]
    solver_interface_ids: frozenset[str]

    def matches(self, task: TaskSpecV3) -> bool:
        return (
            task.task_family_id in self.task_family_ids
            and task.task_kind_id in self.task_kind_ids
            and task.solver_interface_id in self.solver_interface_ids
        )


class AdaptiveCurriculumScheduler:
    """Compute replay/current/exploration weights without editing task specs."""

    def __init__(self, raw: Mapping[str, Any]):
        """Validate stages, bucket mixture, and mastery intervals."""

        if raw.get("schema_version") != "2.0.0":
            raise ValueError("unsupported curriculum schema_version")
        self.curriculum_id = str(raw["curriculum_id"])
        mixture = raw["mixture"]
        self.mixture = {
            "replay": float(mixture["replay"]),
            "current": float(mixture["current"]),
            "explore": float(mixture["explore"]),
        }
        if any(value < 0 for value in self.mixture.values()):
            raise ValueError("curriculum mixture weights must be non-negative")
        if abs(sum(self.mixture.values()) - 1.0) > 1e-12:
            raise ValueError("curriculum mixture weights must sum to one")
        self.stages = tuple(self._parse_stage(item) for item in raw["stages"])
        indices = [stage.index for stage in self.stages]
        if indices != list(range(len(indices))):
            raise ValueError("curriculum stage indices must be contiguous and ordered from zero")
        self.mastery_bands = tuple(
            MasteryBand(
                minimum=float(item["minimum"]),
                maximum=float(item["maximum"]),
                action=str(item["action"]),
                weight_multiplier=float(item["weight_multiplier"]),
            )
            for item in raw["mastery_bands"]
        )
        self._validate_bands()

    @classmethod
    def from_path(cls, path: str | Path) -> "AdaptiveCurriculumScheduler":
        """Load a UTF-8 JSON curriculum config from ``path``."""

        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("curriculum config must be a JSON object")
        return cls(raw)

    @staticmethod
    def _parse_stage(raw: Mapping[str, Any]) -> CurriculumStage:
        """Normalize omitted selectors to wildcard sets for one stage."""

        selectors = raw["coordinates"]
        unknown = set(selectors) - set(AXES)
        if unknown:
            raise ValueError(f"unknown curriculum axes: {sorted(unknown)}")
        coordinates: dict[str, frozenset[CoordinateValue]] = {}
        for axis in AXES:
            values = selectors.get(axis, [])
            if not isinstance(values, list):
                raise ValueError(f"curriculum selector {axis} must be a list")
            if axis == "F":
                if any(
                    not isinstance(value, str) or value not in F_LEVELS
                    for value in values
                ):
                    raise ValueError("curriculum F selector has an invalid level")
            elif any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in values
            ):
                raise ValueError(f"curriculum selector {axis} requires integers")
            coordinates[axis] = frozenset(values)
        return CurriculumStage(
            index=int(raw["index"]),
            stage_id=str(raw["stage_id"]),
            coordinates=coordinates,
        )

    def _validate_bands(self) -> None:
        """Require contiguous mastery coverage of the full probability range."""

        if not self.mastery_bands:
            raise ValueError("curriculum requires mastery bands")
        expected_minimum = 0.0
        for band in self.mastery_bands:
            if abs(band.minimum - expected_minimum) > 1e-12:
                raise ValueError("mastery bands must be contiguous from zero")
            if band.maximum <= band.minimum or band.weight_multiplier < 0:
                raise ValueError("invalid mastery band")
            expected_minimum = band.maximum
        if abs(expected_minimum - 1.0) > 1e-12:
            raise ValueError("mastery bands must cover [0, 1]")

    def stage_for(self, coordinates: TaskCoordinates) -> CurriculumStage:
        """Return the first configured stage matching ``coordinates``."""

        for stage in self.stages:
            if stage.matches(coordinates):
                return stage
        raise ValueError(f"coordinates are outside configured curriculum: {coordinates}")

    def mastery_band(self, pass_at_1: float) -> MasteryBand:
        """Map a pass rate to a left-closed band; the final band includes 1.0."""

        if not 0.0 <= pass_at_1 <= 1.0:
            raise ValueError("pass@1 must be in [0, 1]")
        for band in self.mastery_bands[:-1]:
            if band.minimum <= pass_at_1 < band.maximum:
                return band
        return self.mastery_bands[-1]

    def sampling_weights(
        self,
        tasks: Sequence[TaskSpec],
        *,
        current_stage: int,
        pass_at_1: Mapping[str, float],
    ) -> dict[str, float]:
        """Return normalized weights for replay/current/next-stage tasks.

        Empty buckets surrender their configured mass proportionally to the
        remaining buckets.  Within each bucket, mastery multipliers redistribute
        that mass across tasks; missing pass rates are treated as zero.  Tasks
        beyond the next stage are intentionally ineligible.
        """

        if current_stage < 0 or current_stage >= len(self.stages):
            raise ValueError("current_stage is outside configured stages")
        task_ids = [task.task_id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique")
        buckets: dict[str, list[TaskSpec]] = {
            "replay": [],
            "current": [],
            "explore": [],
        }
        for task in tasks:
            stage_index = self.stage_for(task.coordinates).index
            if stage_index < current_stage:
                buckets["replay"].append(task)
            elif stage_index == current_stage:
                buckets["current"].append(task)
            elif stage_index == current_stage + 1:
                buckets["explore"].append(task)

        active_mass = sum(
            self.mixture[name] for name, bucket in buckets.items() if bucket
        )
        if active_mass == 0:
            raise ValueError("no candidate tasks belong to replay/current/next-stage buckets")
        weights: dict[str, float] = {}
        for name, bucket in buckets.items():
            if not bucket:
                continue
            bucket_mass = self.mixture[name] / active_mass
            if len(bucket) == 1:
                weights[bucket[0].task_id] = bucket_mass
                continue
            scores = []
            for task in bucket:
                rate = float(pass_at_1.get(task.task_id, 0.0))
                scores.append(self.mastery_band(rate).weight_multiplier)
            score_total = sum(scores)
            if score_total == 0:
                scores = [1.0] * len(bucket)
                score_total = float(len(bucket))
            for task, score in zip(bucket, scores, strict=True):
                weights[task.task_id] = bucket_mass * score / score_total
        return weights


class FamilyAwareCurriculumScheduler:
    """Gate semantic tasks before deterministic family-local stage sampling."""

    def __init__(
        self,
        raw: Mapping[str, Any],
        capabilities: ExecutableCapabilityRegistry,
    ):
        expected = {
            "schema_version",
            "curriculum_id",
            "model_family_id",
            "mixture",
            "mastery_bands",
            "stages",
        }
        keys = set(raw)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "family curriculum has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        if raw["schema_version"] != "family-curriculum-v1.0.0":
            raise ValueError("unsupported family curriculum schema_version")
        self.curriculum_id = str(raw["curriculum_id"])
        if not self.curriculum_id:
            raise ValueError("family curriculum_id must not be empty")
        self.model_family_id = str(raw["model_family_id"])
        capabilities.model_families.require(self.model_family_id)
        self.capabilities = capabilities
        mixture = raw["mixture"]
        if not isinstance(mixture, Mapping) or set(mixture) != {
            "replay",
            "current",
            "explore",
        }:
            raise ValueError("family curriculum mixture has an invalid field set")
        self.mixture = {
            "replay": float(mixture["replay"]),
            "current": float(mixture["current"]),
            "explore": float(mixture["explore"]),
        }
        if any(value < 0 for value in self.mixture.values()):
            raise ValueError("curriculum mixture weights must be non-negative")
        if abs(sum(self.mixture.values()) - 1.0) > 1e-12:
            raise ValueError("curriculum mixture weights must sum to one")
        raw_stages = raw["stages"]
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ValueError("family curriculum requires stages")
        self.stages = tuple(self._parse_stage(item) for item in raw_stages)
        indices = [stage.index for stage in self.stages]
        if indices != list(range(len(indices))):
            raise ValueError(
                "family curriculum stage indices must be contiguous and ordered from zero"
            )
        raw_bands = raw["mastery_bands"]
        if not isinstance(raw_bands, list):
            raise ValueError("family curriculum mastery_bands must be an array")
        self.mastery_bands = tuple(
            MasteryBand(
                minimum=float(item["minimum"]),
                maximum=float(item["maximum"]),
                action=str(item["action"]),
                weight_multiplier=float(item["weight_multiplier"]),
            )
            for item in raw_bands
        )
        self._validate_bands()

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        capabilities: ExecutableCapabilityRegistry,
    ) -> "FamilyAwareCurriculumScheduler":
        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, Mapping):
            raise ValueError("family curriculum config must be a JSON object")
        return cls(raw, capabilities)

    @staticmethod
    def _parse_stage(raw: Mapping[str, Any]) -> FamilyCurriculumStage:
        expected = {
            "index",
            "stage_id",
            "task_family_ids",
            "task_kind_ids",
            "solver_interface_ids",
        }
        keys = set(raw)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "family curriculum stage has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )

        def identifiers(field_name: str) -> frozenset[str]:
            values = raw[field_name]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(item, str) or not item for item in values)
                or len(values) != len(set(values))
            ):
                raise ValueError(
                    f"family curriculum {field_name} must contain unique IDs"
                )
            return frozenset(values)

        stage_id = raw["stage_id"]
        if not isinstance(stage_id, str) or not stage_id:
            raise ValueError("family curriculum stage_id must be non-empty")
        index = raw["index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("family curriculum stage index must be non-negative")
        return FamilyCurriculumStage(
            index=index,
            stage_id=stage_id,
            task_family_ids=identifiers("task_family_ids"),
            task_kind_ids=identifiers("task_kind_ids"),
            solver_interface_ids=identifiers("solver_interface_ids"),
        )

    def _validate_bands(self) -> None:
        if not self.mastery_bands:
            raise ValueError("curriculum requires mastery bands")
        expected_minimum = 0.0
        for band in self.mastery_bands:
            if abs(band.minimum - expected_minimum) > 1e-12:
                raise ValueError("mastery bands must be contiguous from zero")
            if band.maximum <= band.minimum or band.weight_multiplier < 0:
                raise ValueError("invalid mastery band")
            expected_minimum = band.maximum
        if abs(expected_minimum - 1.0) > 1e-12:
            raise ValueError("mastery bands must cover [0, 1]")

    def mastery_band(self, pass_at_1: float) -> MasteryBand:
        if not 0.0 <= pass_at_1 <= 1.0:
            raise ValueError("pass@1 must be in [0, 1]")
        for band in self.mastery_bands[:-1]:
            if band.minimum <= pass_at_1 < band.maximum:
                return band
        return self.mastery_bands[-1]

    def stage_for(self, task: TaskSpecV3) -> FamilyCurriculumStage:
        """Map only this family's admitted semantic identity to a local stage."""

        if not isinstance(task, TaskSpecV3):
            raise TypeError("family stage mapping requires TaskSpecV3")
        self.capabilities.require_task(task)
        if task.model_family_id != self.model_family_id:
            raise ValueError(
                f"task model family {task.model_family_id!r} is outside curriculum "
                f"{self.model_family_id!r}"
            )
        for stage in self.stages:
            if stage.matches(task):
                return stage
        raise ValueError(
            f"semantic task is outside configured family-local stages: {task.task_id}"
        )

    def construct_task_pool(self, tasks: Sequence[TaskSpecV3]) -> GatedTaskPool:
        """Apply the portable capability gate before any stage mapping."""

        pool = self.capabilities.gate_task_pool(tasks)
        for task in pool.tasks:
            self.stage_for(task)
        return pool

    def sampling_weights(
        self,
        tasks: Sequence[TaskSpecV3],
        *,
        current_stage: int,
        pass_at_1: Mapping[str, float],
    ) -> dict[str, float]:
        """Preserve 20/60/20 semantics over the capability-gated task pool."""

        if current_stage < 0 or current_stage >= len(self.stages):
            raise ValueError("current_stage is outside configured stages")
        pool = self.construct_task_pool(tasks)
        buckets: dict[str, list[TaskSpecV3]] = {
            "replay": [],
            "current": [],
            "explore": [],
        }
        for task in pool.tasks:
            stage_index = self.stage_for(task).index
            if stage_index < current_stage:
                buckets["replay"].append(task)
            elif stage_index == current_stage:
                buckets["current"].append(task)
            elif stage_index == current_stage + 1:
                buckets["explore"].append(task)
        active_mass = sum(
            self.mixture[name] for name, bucket in buckets.items() if bucket
        )
        if active_mass == 0:
            diagnostics = [
                f"{item.task_id}:{item.decision.code}" for item in pool.decisions
            ]
            raise ValueError(
                "no capability-gated tasks belong to replay/current/next-stage "
                f"buckets; diagnostics={diagnostics}"
            )
        weights: dict[str, float] = {}
        for name, bucket in buckets.items():
            if not bucket:
                continue
            bucket_mass = self.mixture[name] / active_mass
            if len(bucket) == 1:
                weights[bucket[0].task_id] = bucket_mass
                continue
            scores = [
                self.mastery_band(
                    float(pass_at_1.get(task.task_id, 0.0))
                ).weight_multiplier
                for task in bucket
            ]
            score_total = sum(scores)
            if score_total == 0:
                scores = [1.0] * len(bucket)
                score_total = float(len(bucket))
            for task, score in zip(bucket, scores, strict=True):
                weights[task.task_id] = bucket_mass * score / score_total
        return weights
