"""Explicit compatibility adapters from accepted BSM identities to task v3."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from synthetic_derivatives.model_families.capabilities import (
    CapabilityBinding,
    CapabilityKey,
)
from synthetic_derivatives.task_space.models import (
    TaskCoordinates,
    TaskSpec,
    TaskSpecV3,
)


TDGBM_BSM_MODEL_FAMILY_ID = "tdgbm_bsm"
ANALYTIC_GREEKS_LIBRARY_INTERFACE_ID = (
    "python-library-bsm-analytic-greeks-v1"
)
SCALAR_IV_LIBRARY_INTERFACE_ID = "python-library-bsm-scalar-iv-v1"
STATIC_JSON_SOLVER_INTERFACE_ID = "static-json-query-schema-submit-v2"
DUCKDB_QUERY_V3_SOLVER_INTERFACE_ID = (
    "read-only-duckdb-query-schema-submit-v3"
)


@dataclass(frozen=True, slots=True)
class LegacyVariantIdentity:
    """Frozen mapping from one accepted variant to separated semantic IDs."""

    variant_id: str
    legacy_task_family_id: str
    model_family_id: str
    task_family_id: str
    task_kind_id: str
    solver_interface_id: str
    coordinates: TaskCoordinates
    method_id: str
    output_contract_id: str

    @property
    def binding(self) -> CapabilityBinding:
        return CapabilityBinding(
            CapabilityKey(
                self.model_family_id,
                self.task_family_id,
                self.task_kind_id,
                self.method_id,
                self.solver_interface_id,
            ),
            self.output_contract_id,
        )

    def adapt_task(self, task: TaskSpec) -> TaskSpecV3:
        """Adapt a matching legacy task while preserving its immutable fields."""

        if not isinstance(task, TaskSpec):
            raise TypeError("legacy variant adapter requires TaskSpec")
        mismatches: list[str] = []
        if task.task_family_id != self.legacy_task_family_id:
            mismatches.append("task_family_id")
        if task.coordinates != self.coordinates:
            mismatches.append("coordinates")
        if task.method_id != self.method_id:
            mismatches.append("method_id")
        if task.output_contract_id != self.output_contract_id:
            mismatches.append("output_contract_id")
        if mismatches:
            raise ValueError(
                f"legacy task does not match variant {self.variant_id!r}: {mismatches}"
            )
        return TaskSpecV3.from_legacy(
            task,
            model_family_id=self.model_family_id,
            task_family_id=self.task_family_id,
            task_kind_id=self.task_kind_id,
            solver_interface_id=self.solver_interface_id,
        )


_LEGACY_VARIANTS = MappingProxyType(
    {
        "bsm_analytic_greeks_v1": LegacyVariantIdentity(
            variant_id="bsm_analytic_greeks_v1",
            legacy_task_family_id="bsm_greeks",
            model_family_id=TDGBM_BSM_MODEL_FAMILY_ID,
            task_family_id="analytic_greeks",
            task_kind_id="core_greeks",
            solver_interface_id=ANALYTIC_GREEKS_LIBRARY_INTERFACE_ID,
            coordinates=TaskCoordinates(1, 0, 0, 0, 1, 1, "F0"),
            method_id="bsm-analytic-float64-greeks-v1",
            output_contract_id="bsm-analytic-greeks-output-v1",
        ),
        "bsm_iv_scalar_v1": LegacyVariantIdentity(
            variant_id="bsm_iv_scalar_v1",
            legacy_task_family_id="bsm_vanilla",
            model_family_id=TDGBM_BSM_MODEL_FAMILY_ID,
            task_family_id="implied_volatility",
            task_kind_id="scalar_iv",
            solver_interface_id=SCALAR_IV_LIBRARY_INTERFACE_ID,
            coordinates=TaskCoordinates(1, 0, 0, 1, 1, 0, "F0"),
            method_id="bsm-bisection-float64-80-v1",
            output_contract_id="bsm-iv-scalar-output-v1",
        ),
        "bsm_market_implied_greeks_v1": LegacyVariantIdentity(
            variant_id="bsm_market_implied_greeks_v1",
            legacy_task_family_id="bsm_greeks",
            model_family_id=TDGBM_BSM_MODEL_FAMILY_ID,
            task_family_id="market_implied_metric_bundle",
            task_kind_id="core_greeks_bundle",
            solver_interface_id=STATIC_JSON_SOLVER_INTERFACE_ID,
            coordinates=TaskCoordinates(5, 0, 0, 1, 4, 1, "F0"),
            method_id="bsm-mid-iv-bisection80-analytic-greeks-v1",
            output_contract_id="bsm-market-implied-greeks-output-v1",
        ),
    }
)


def get_legacy_variant_identity(variant_id: str) -> LegacyVariantIdentity:
    """Return one explicit accepted mapping or fail for an unknown variant."""

    try:
        return _LEGACY_VARIANTS[variant_id]
    except KeyError as error:
        raise ValueError(f"unsupported legacy variant identity: {variant_id!r}") from error


def adapt_legacy_variant_config(
    value: Mapping[str, Any],
) -> LegacyVariantIdentity:
    """Validate an accepted variant document before returning its mapping."""

    variant_id = value.get("variant_id")
    if not isinstance(variant_id, str):
        raise ValueError("legacy variant_id must be a string")
    identity = get_legacy_variant_identity(variant_id)
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, Mapping):
        raise ValueError("legacy variant coordinates must be an object")
    observed_coordinates = TaskCoordinates.from_mapping(coordinates)
    observed_method = value.get("method_id")
    if variant_id == "bsm_iv_scalar_v1":
        inversion = value.get("inversion_contract")
        if not isinstance(inversion, Mapping):
            raise ValueError("scalar IV variant requires inversion_contract")
        observed_method = inversion.get("method_id")
        if value.get("output_schema") != (
            "schemas/bsm-implied-volatility-output.schema.json"
        ):
            raise ValueError("scalar IV output schema identity changed")
    observed = {
        "task_family": value.get("task_family"),
        "coordinates": observed_coordinates,
        "method_id": observed_method,
    }
    expected = {
        "task_family": identity.legacy_task_family_id,
        "coordinates": identity.coordinates,
        "method_id": identity.method_id,
    }
    if observed != expected:
        changed = sorted(key for key in expected if observed[key] != expected[key])
        raise ValueError(
            f"legacy variant {variant_id!r} conflicts with its adapter: {changed}"
        )
    if variant_id != "bsm_iv_scalar_v1" and value.get(
        "output_contract_id"
    ) != identity.output_contract_id:
        raise ValueError(
            f"legacy variant {variant_id!r} output contract identity changed"
        )
    if variant_id == "bsm_market_implied_greeks_v1" and value.get(
        "input_interface"
    ) != "two_data_only_trusted_queries_over_hidden_task_duckdb":
        raise ValueError("market Greeks legacy input interface identity changed")
    return identity


__all__ = [
    "ANALYTIC_GREEKS_LIBRARY_INTERFACE_ID",
    "DUCKDB_QUERY_V3_SOLVER_INTERFACE_ID",
    "LegacyVariantIdentity",
    "SCALAR_IV_LIBRARY_INTERFACE_ID",
    "STATIC_JSON_SOLVER_INTERFACE_ID",
    "TDGBM_BSM_MODEL_FAMILY_ID",
    "adapt_legacy_variant_config",
    "get_legacy_variant_identity",
]
