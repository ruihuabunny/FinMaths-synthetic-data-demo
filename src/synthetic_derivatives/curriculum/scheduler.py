"""Adaptive sampling over immutable tasks; hard-verifier rewards stay binary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from synthetic_derivatives.task_space import AXES, TaskCoordinates, TaskSpec


@dataclass(frozen=True)
class CurriculumStage:
    index: int
    stage_id: str
    coordinates: dict[str, frozenset[int]]

    def matches(self, coordinates: TaskCoordinates) -> bool:
        return all(
            not allowed or getattr(coordinates, axis) in allowed
            for axis, allowed in self.coordinates.items()
        )


@dataclass(frozen=True)
class MasteryBand:
    minimum: float
    maximum: float
    action: str
    weight_multiplier: float


class AdaptiveCurriculumScheduler:
    """Compute replay/current/exploration weights without editing task specs."""

    def __init__(self, raw: Mapping[str, Any]):
        if raw.get("schema_version") != "1.0.0":
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
        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("curriculum config must be a JSON object")
        return cls(raw)

    @staticmethod
    def _parse_stage(raw: Mapping[str, Any]) -> CurriculumStage:
        selectors = raw["coordinates"]
        unknown = set(selectors) - set(AXES)
        if unknown:
            raise ValueError(f"unknown curriculum axes: {sorted(unknown)}")
        return CurriculumStage(
            index=int(raw["index"]),
            stage_id=str(raw["stage_id"]),
            coordinates={
                axis: frozenset(int(value) for value in selectors.get(axis, []))
                for axis in AXES
            },
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

    def stage_for(self, coordinates: TaskCoordinates) -> CurriculumStage:
        for stage in self.stages:
            if stage.matches(coordinates):
                return stage
        raise ValueError(f"coordinates are outside configured curriculum: {coordinates}")

    def mastery_band(self, pass_at_1: float) -> MasteryBand:
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
        """Return normalized adaptive weights for replay/current/next-stage tasks."""

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
