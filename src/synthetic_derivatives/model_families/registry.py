"""Fail-closed registry for implemented model-family identities."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from synthetic_derivatives.model_families.contracts import ModelFamilySpec

if TYPE_CHECKING:
    from synthetic_derivatives.task_space.models import TaskCoordinates


class ModelFamilyRegistry:
    """Resolve declared families and validate their derived ``M`` coordinate."""

    def __init__(self, specifications: Iterable[ModelFamilySpec]):
        items = tuple(specifications)
        if not items:
            raise ValueError("model-family registry requires at least one family")
        family_ids = [item.model_family_id for item in items]
        if len(family_ids) != len(set(family_ids)):
            raise ValueError("model_family_id values must be unique")
        coordinates = [item.model_coordinate for item in items]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("model_coordinate values must be unique")
        self._specifications = MappingProxyType(
            {item.model_family_id: item for item in items}
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "ModelFamilyRegistry":
        """Load a registry containing one model-family document."""

        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, Mapping):
            raise ValueError("model-family config must be a JSON object")
        return cls((ModelFamilySpec.from_mapping(raw),))

    @classmethod
    def from_directory(cls, path: str | Path) -> "ModelFamilyRegistry":
        """Load every versioned JSON family document in lexical path order."""

        directory = Path(path)
        paths = tuple(sorted(directory.glob("*.json")))
        if not paths:
            raise ValueError("model-family config directory contains no JSON files")
        specifications: list[ModelFamilySpec] = []
        for config_path in paths:
            with config_path.open(encoding="utf-8") as handle:
                raw: Any = json.load(handle)
            if not isinstance(raw, Mapping):
                raise ValueError(
                    f"model-family config must be a JSON object: {config_path}"
                )
            specifications.append(ModelFamilySpec.from_mapping(raw))
        return cls(specifications)

    @property
    def model_family_ids(self) -> tuple[str, ...]:
        """Return registered IDs in deterministic declaration order."""

        return tuple(self._specifications)

    def get(self, model_family_id: str) -> ModelFamilySpec | None:
        """Return a family or ``None`` without inferring from coordinates."""

        return self._specifications.get(model_family_id)

    def require(self, model_family_id: str) -> ModelFamilySpec:
        """Return a registered family, failing closed for catalog-only IDs."""

        try:
            return self._specifications[model_family_id]
        except KeyError as error:
            raise ValueError(
                f"unknown or unimplemented model family: {model_family_id!r}"
            ) from error

    def require_matching_coordinates(
        self, model_family_id: str, coordinates: "TaskCoordinates"
    ) -> ModelFamilySpec:
        """Require ``coordinates.M`` to equal the family's declared coordinate."""

        specification = self.require(model_family_id)
        if coordinates.M != specification.model_coordinate:
            raise ValueError(
                "model_family_id and M coordinate disagree: "
                f"{model_family_id!r} requires M={specification.model_coordinate}, "
                f"received M={coordinates.M}"
            )
        return specification


__all__ = ["ModelFamilyRegistry"]
