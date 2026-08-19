"""Declarative identities for implemented stochastic model families.

The records in this module contain no numerical implementation and no dynamic
import path.  They identify the probability laws and the contracts that make a
model family reproducible; executable backends are registered explicitly at
their owning permission boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_IDENTITY_FIELDS = (
    "physical_measure_id",
    "pricing_measure_id",
    "numeraire_id",
    "conditioning_information_id",
    "time_axis_id",
    "day_count_id",
    "units_id",
    "transition_id",
    "parameter_semantics_id",
    "dtype_id",
    "random_ordering_id",
    "canonicalization_id",
)


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class StochasticIdentity:
    """Evidence IDs needed to identify the family's probability model."""

    physical_measure_id: str
    pricing_measure_id: str
    numeraire_id: str
    conditioning_information_id: str
    time_axis_id: str
    day_count_id: str
    units_id: str
    transition_id: str
    parameter_semantics_id: str
    dtype_id: str
    random_ordering_id: str
    canonicalization_id: str

    def __post_init__(self) -> None:
        for field_name in _IDENTITY_FIELDS:
            _nonempty_string(getattr(self, field_name), field_name)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StochasticIdentity":
        """Parse an exact closed stochastic-identity mapping."""

        keys = set(value)
        expected = set(_IDENTITY_FIELDS)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "stochastic_identity requires exactly its declared fields; "
                f"missing={missing}, extra={extra}"
            )
        return cls(**{field: value[field] for field in _IDENTITY_FIELDS})

    def to_dict(self) -> dict[str, str]:
        """Return the stable JSON representation."""

        return {field: getattr(self, field) for field in _IDENTITY_FIELDS}


@dataclass(frozen=True, slots=True)
class ModelFamilySpec:
    """Stable model-family identity, independent of task and interface IDs."""

    model_family_id: str
    underlying_dynamics_id: str
    pricing_model_id: str
    measure_mapping_id: str
    state_contract_id: str
    model_coordinate: int
    stochastic_identity: StochasticIdentity

    def __post_init__(self) -> None:
        for field_name in (
            "model_family_id",
            "underlying_dynamics_id",
            "pricing_model_id",
            "measure_mapping_id",
            "state_contract_id",
        ):
            _nonempty_string(getattr(self, field_name), field_name)
        if (
            isinstance(self.model_coordinate, bool)
            or not isinstance(self.model_coordinate, int)
            or self.model_coordinate < 0
        ):
            raise ValueError("model_coordinate must be a non-negative integer")
        if not isinstance(self.stochastic_identity, StochasticIdentity):
            raise TypeError("stochastic_identity must be a StochasticIdentity")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelFamilySpec":
        """Parse one closed model-family-v1 document.

        Rejecting unknown fields also rejects JSON-provided Python import paths;
        code backends must be registered explicitly in Python.
        """

        expected = {
            "schema_version",
            "model_family_id",
            "underlying_dynamics_id",
            "pricing_model_id",
            "measure_mapping_id",
            "state_contract_id",
            "model_coordinate",
            "stochastic_identity",
        }
        keys = set(value)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "model-family document has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        if value["schema_version"] != "model-family-v1.0.0":
            raise ValueError("unsupported model-family schema_version")
        stochastic_identity = value["stochastic_identity"]
        if not isinstance(stochastic_identity, Mapping):
            raise ValueError("stochastic_identity must be an object")
        return cls(
            model_family_id=value["model_family_id"],
            underlying_dynamics_id=value["underlying_dynamics_id"],
            pricing_model_id=value["pricing_model_id"],
            measure_mapping_id=value["measure_mapping_id"],
            state_contract_id=value["state_contract_id"],
            model_coordinate=value["model_coordinate"],
            stochastic_identity=StochasticIdentity.from_mapping(
                stochastic_identity
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical field mapping used by the sidecar config."""

        return {
            "schema_version": "model-family-v1.0.0",
            "model_family_id": self.model_family_id,
            "underlying_dynamics_id": self.underlying_dynamics_id,
            "pricing_model_id": self.pricing_model_id,
            "measure_mapping_id": self.measure_mapping_id,
            "state_contract_id": self.state_contract_id,
            "model_coordinate": self.model_coordinate,
            "stochastic_identity": self.stochastic_identity.to_dict(),
        }


__all__ = ["ModelFamilySpec", "StochasticIdentity"]
