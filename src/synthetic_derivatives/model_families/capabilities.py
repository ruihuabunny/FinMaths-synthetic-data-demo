"""Executable capability registry, separate from design-space compatibility."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Sequence

from synthetic_derivatives.model_families.registry import ModelFamilyRegistry
from synthetic_derivatives.task_space.models import TaskSpecV3
from synthetic_derivatives.task_space.registry import TaskSpaceRegistry


CapabilityStatus = Literal[
    "catalog_only",
    "library_implemented",
    "portable_verified",
]
CAPABILITY_STATUSES: frozenset[str] = frozenset(
    {"catalog_only", "library_implemented", "portable_verified"}
)
PORTABLE_CAPABILITY_STATUSES: frozenset[str] = frozenset(
    {"portable_verified"}
)
EXECUTABLE_CAPABILITY_STATUSES: frozenset[str] = frozenset(
    {"library_implemented", "portable_verified"}
)


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field_name)


@dataclass(frozen=True, slots=True, order=True)
class CapabilityKey:
    """The complete five-field key used for runtime dispatch."""

    model_family_id: str
    task_family_id: str
    task_kind_id: str
    method_id: str
    solver_interface_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "model_family_id",
            "task_family_id",
            "task_kind_id",
            "method_id",
            "solver_interface_id",
        ):
            _nonempty_string(getattr(self, field_name), field_name)

    @classmethod
    def from_task(cls, task: TaskSpecV3) -> "CapabilityKey":
        if not isinstance(task, TaskSpecV3):
            raise TypeError("capability lookup requires TaskSpecV3")
        return cls(*task.capability_identity)

    def to_dict(self) -> dict[str, str]:
        return {
            "model_family_id": self.model_family_id,
            "task_family_id": self.task_family_id,
            "task_kind_id": self.task_kind_id,
            "method_id": self.method_id,
            "solver_interface_id": self.solver_interface_id,
        }


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """A capability key paired with the exact output contract it produces."""

    key: CapabilityKey
    output_contract_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, CapabilityKey):
            raise TypeError("key must be CapabilityKey")
        _nonempty_string(self.output_contract_id, "output_contract_id")


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """Declared implementation evidence; never a dynamic import specification."""

    authoring_backend_id: str | None
    solver_id: str | None
    verifier_id: str | None
    package_materializer_id: str | None
    runtime_contract_id: str | None
    test_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CapabilityEvidence":
        fields = {
            "authoring_backend_id",
            "solver_id",
            "verifier_id",
            "package_materializer_id",
            "runtime_contract_id",
            "test_ids",
        }
        keys = set(value)
        if keys != fields:
            missing = sorted(fields - keys)
            extra = sorted(keys - fields)
            raise ValueError(
                "capability evidence has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        raw_test_ids = value["test_ids"]
        if not isinstance(raw_test_ids, list) or any(
            not isinstance(item, str) or not item for item in raw_test_ids
        ):
            raise ValueError("evidence.test_ids must be an array of non-empty strings")
        if len(raw_test_ids) != len(set(raw_test_ids)):
            raise ValueError("evidence.test_ids must not contain duplicates")
        return cls(
            authoring_backend_id=_optional_string(
                value["authoring_backend_id"], "authoring_backend_id"
            ),
            solver_id=_optional_string(value["solver_id"], "solver_id"),
            verifier_id=_optional_string(value["verifier_id"], "verifier_id"),
            package_materializer_id=_optional_string(
                value["package_materializer_id"], "package_materializer_id"
            ),
            runtime_contract_id=_optional_string(
                value["runtime_contract_id"], "runtime_contract_id"
            ),
            test_ids=tuple(raw_test_ids),
        )

    def require_for_status(self, status: CapabilityStatus) -> None:
        """Enforce status-specific evidence without inferring implementation."""

        if status == "catalog_only":
            return
        required = {
            "authoring_backend_id": self.authoring_backend_id,
            "solver_id": self.solver_id,
            "verifier_id": self.verifier_id,
        }
        if status == "portable_verified":
            required.update(
                {
                    "package_materializer_id": self.package_materializer_id,
                    "runtime_contract_id": self.runtime_contract_id,
                }
            )
        missing = sorted(name for name, value in required.items() if value is None)
        if not self.test_ids:
            missing.append("test_ids")
        if missing:
            raise ValueError(
                f"capability status {status!r} lacks required evidence: {missing}"
            )
        if status == "library_implemented" and (
            self.package_materializer_id is not None
            or self.runtime_contract_id is not None
        ):
            raise ValueError(
                "library_implemented capability must not claim portable package/runtime evidence"
            )


@dataclass(frozen=True, slots=True)
class ExecutableCapability:
    """One exact capability declaration and its legacy compatibility aliases."""

    key: CapabilityKey
    catalog_task_family_id: str
    status: CapabilityStatus
    output_contract_id: str
    legacy_identity_ids: tuple[str, ...]
    evidence: CapabilityEvidence

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutableCapability":
        fields = {
            "model_family_id",
            "task_family_id",
            "task_kind_id",
            "method_id",
            "solver_interface_id",
            "catalog_task_family_id",
            "status",
            "output_contract_id",
            "legacy_identity_ids",
            "evidence",
        }
        keys = set(value)
        if keys != fields:
            missing = sorted(fields - keys)
            extra = sorted(keys - fields)
            raise ValueError(
                "capability has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        status = value["status"]
        if not isinstance(status, str) or status not in CAPABILITY_STATUSES:
            raise ValueError(f"unsupported capability status: {status!r}")
        raw_aliases = value["legacy_identity_ids"]
        if not isinstance(raw_aliases, list) or any(
            not isinstance(item, str) or not item for item in raw_aliases
        ):
            raise ValueError("legacy_identity_ids must contain non-empty strings")
        if len(raw_aliases) != len(set(raw_aliases)):
            raise ValueError("legacy_identity_ids must not contain duplicates")
        raw_evidence = value["evidence"]
        if not isinstance(raw_evidence, Mapping):
            raise ValueError("capability evidence must be an object")
        evidence = CapabilityEvidence.from_mapping(raw_evidence)
        evidence.require_for_status(status)
        return cls(
            key=CapabilityKey(
                model_family_id=value["model_family_id"],
                task_family_id=value["task_family_id"],
                task_kind_id=value["task_kind_id"],
                method_id=value["method_id"],
                solver_interface_id=value["solver_interface_id"],
            ),
            catalog_task_family_id=_nonempty_string(
                value["catalog_task_family_id"], "catalog_task_family_id"
            ),
            status=status,
            output_contract_id=_nonempty_string(
                value["output_contract_id"], "output_contract_id"
            ),
            legacy_identity_ids=tuple(raw_aliases),
            evidence=evidence,
        )

    @property
    def binding(self) -> CapabilityBinding:
        return CapabilityBinding(self.key, self.output_contract_id)


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    """Deterministic admission result suitable for diagnostics and tests."""

    admitted: bool
    code: str
    reason: str
    capability: ExecutableCapability | None


@dataclass(frozen=True, slots=True)
class TaskCapabilityDecision:
    """One task ID paired with its capability decision."""

    task_id: str
    decision: CapabilityDecision


@dataclass(frozen=True, slots=True)
class GatedTaskPool:
    """Stable admitted task sequence plus a decision for every candidate."""

    tasks: tuple[TaskSpecV3, ...]
    decisions: tuple[TaskCapabilityDecision, ...]


class ExecutableCapabilityRegistry:
    """Fail-closed dispatch registry layered over the broad design catalog."""

    def __init__(
        self,
        raw: Mapping[str, Any],
        *,
        model_families: ModelFamilyRegistry,
        design_catalog: TaskSpaceRegistry | None = None,
    ):
        expected = {"schema_version", "registry_id", "capabilities"}
        keys = set(raw)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(
                "capability registry has an invalid field set; "
                f"missing={missing}, extra={extra}"
            )
        if raw["schema_version"] != "executable-capability-registry-v1.0.0":
            raise ValueError("unsupported executable capability schema_version")
        self.registry_id = _nonempty_string(raw["registry_id"], "registry_id")
        raw_capabilities = raw["capabilities"]
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise ValueError("capability registry requires a non-empty array")
        capabilities = tuple(
            ExecutableCapability.from_mapping(item)
            if isinstance(item, Mapping)
            else self._raise_non_object_capability()
            for item in raw_capabilities
        )
        keys_in_order = [item.key for item in capabilities]
        if len(keys_in_order) != len(set(keys_in_order)):
            raise ValueError("executable capability keys must be unique")
        for capability in capabilities:
            model_families.require(capability.key.model_family_id)
        aliases = [
            alias
            for capability in capabilities
            for alias in capability.legacy_identity_ids
        ]
        if len(aliases) != len(set(aliases)):
            raise ValueError("legacy capability identity aliases must be unique")
        self.model_families = model_families
        self.design_catalog = design_catalog
        self.capabilities = capabilities
        self._by_key = MappingProxyType({item.key: item for item in capabilities})
        self._by_legacy_identity = MappingProxyType(
            {
                alias: capability
                for capability in capabilities
                for alias in capability.legacy_identity_ids
            }
        )

    @staticmethod
    def _raise_non_object_capability() -> ExecutableCapability:
        raise ValueError("each capability must be a JSON object")

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        model_families: ModelFamilyRegistry,
        design_catalog: TaskSpaceRegistry | None = None,
    ) -> "ExecutableCapabilityRegistry":
        with Path(path).open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, Mapping):
            raise ValueError("capability registry config must be a JSON object")
        return cls(
            raw,
            model_families=model_families,
            design_catalog=design_catalog,
        )

    def get(self, key: CapabilityKey) -> ExecutableCapability | None:
        if not isinstance(key, CapabilityKey):
            raise TypeError("capability key must be CapabilityKey")
        return self._by_key.get(key)

    def require_legacy_identity(self, identity_id: str) -> ExecutableCapability:
        """Resolve one explicit legacy variant/delivery alias without guessing."""

        _nonempty_string(identity_id, "identity_id")
        try:
            return self._by_legacy_identity[identity_id]
        except KeyError as error:
            raise ValueError(
                f"legacy identity has no executable capability adapter: {identity_id!r}"
            ) from error

    @staticmethod
    def _normalize_statuses(statuses: Iterable[str]) -> frozenset[str]:
        normalized = frozenset(statuses)
        if not normalized or not normalized <= CAPABILITY_STATUSES:
            raise ValueError("required capability statuses are empty or invalid")
        return normalized

    def evaluate_binding(
        self,
        binding: CapabilityBinding,
        *,
        required_statuses: Iterable[str] = PORTABLE_CAPABILITY_STATUSES,
    ) -> CapabilityDecision:
        """Evaluate an exact binding without coordinates or default inference."""

        if not isinstance(binding, CapabilityBinding):
            raise TypeError("binding must be CapabilityBinding")
        statuses = self._normalize_statuses(required_statuses)
        try:
            self.model_families.require(binding.key.model_family_id)
        except ValueError as error:
            return CapabilityDecision(False, "MODEL_FAMILY_UNAVAILABLE", str(error), None)
        capability = self._by_key.get(binding.key)
        if capability is None:
            return CapabilityDecision(
                False,
                "CAPABILITY_NOT_REGISTERED",
                f"no executable capability is registered for {binding.key.to_dict()}",
                None,
            )
        if binding.output_contract_id != capability.output_contract_id:
            return CapabilityDecision(
                False,
                "OUTPUT_CONTRACT_MISMATCH",
                "declared output contract does not match the registered capability: "
                f"expected {capability.output_contract_id!r}, received "
                f"{binding.output_contract_id!r}",
                capability,
            )
        if capability.status not in statuses:
            return CapabilityDecision(
                False,
                "CAPABILITY_STATUS_DENIED",
                f"capability status {capability.status!r} is not one of "
                f"{sorted(statuses)}",
                capability,
            )
        return CapabilityDecision(
            True,
            "CAPABILITY_ADMITTED",
            f"capability admitted with status {capability.status!r}",
            capability,
        )

    def evaluate_task(
        self,
        task: TaskSpecV3,
        *,
        required_statuses: Iterable[str] = PORTABLE_CAPABILITY_STATUSES,
    ) -> CapabilityDecision:
        """Evaluate family/M, exact capability, output, and catalog constraints."""

        if not isinstance(task, TaskSpecV3):
            raise TypeError("task capability evaluation requires TaskSpecV3")
        family = self.model_families.get(task.model_family_id)
        if family is None:
            return CapabilityDecision(
                False,
                "MODEL_FAMILY_UNAVAILABLE",
                "unknown or unimplemented model family: "
                f"{task.model_family_id!r}",
                None,
            )
        if task.coordinates.M != family.model_coordinate:
            return CapabilityDecision(
                False,
                "MODEL_FAMILY_MISMATCH",
                "model_family_id and M coordinate disagree: "
                f"{task.model_family_id!r} requires M={family.model_coordinate}, "
                f"received M={task.coordinates.M}",
                None,
            )
        binding_decision = self.evaluate_binding(
            CapabilityBinding(CapabilityKey.from_task(task), task.output_contract_id),
            required_statuses=required_statuses,
        )
        if not binding_decision.admitted:
            return binding_decision
        capability = binding_decision.capability
        assert capability is not None
        if self.design_catalog is not None:
            decision = self.design_catalog.evaluate(
                task.coordinates, capability.catalog_task_family_id
            )
            if not decision.compatible:
                return CapabilityDecision(
                    False,
                    "DESIGN_CATALOG_INCOMPATIBLE",
                    decision.reason,
                    capability,
                )
        return binding_decision

    def require_binding(
        self,
        binding: CapabilityBinding,
        *,
        required_statuses: Iterable[str] = PORTABLE_CAPABILITY_STATUSES,
    ) -> ExecutableCapability:
        decision = self.evaluate_binding(
            binding, required_statuses=required_statuses
        )
        if not decision.admitted:
            raise ValueError(
                f"executable capability denied [{decision.code}]: {decision.reason}"
            )
        assert decision.capability is not None
        return decision.capability

    def require_task(
        self,
        task: TaskSpecV3,
        *,
        required_statuses: Iterable[str] = PORTABLE_CAPABILITY_STATUSES,
    ) -> ExecutableCapability:
        decision = self.evaluate_task(task, required_statuses=required_statuses)
        if not decision.admitted:
            raise ValueError(
                f"executable capability denied [{decision.code}]: {decision.reason}"
            )
        assert decision.capability is not None
        return decision.capability

    def gate_task_pool(
        self,
        tasks: Sequence[TaskSpecV3],
        *,
        required_statuses: Iterable[str] = PORTABLE_CAPABILITY_STATUSES,
    ) -> GatedTaskPool:
        """Admit only exact capabilities while preserving candidate order."""

        task_ids = [task.task_id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique before capability gating")
        admitted: list[TaskSpecV3] = []
        decisions: list[TaskCapabilityDecision] = []
        for task in tasks:
            decision = self.evaluate_task(task, required_statuses=required_statuses)
            decisions.append(TaskCapabilityDecision(task.task_id, decision))
            if decision.admitted:
                admitted.append(task)
        return GatedTaskPool(tuple(admitted), tuple(decisions))


__all__ = [
    "CAPABILITY_STATUSES",
    "EXECUTABLE_CAPABILITY_STATUSES",
    "PORTABLE_CAPABILITY_STATUSES",
    "CapabilityBinding",
    "CapabilityDecision",
    "CapabilityEvidence",
    "CapabilityKey",
    "CapabilityStatus",
    "ExecutableCapability",
    "ExecutableCapabilityRegistry",
    "GatedTaskPool",
    "TaskCapabilityDecision",
]
