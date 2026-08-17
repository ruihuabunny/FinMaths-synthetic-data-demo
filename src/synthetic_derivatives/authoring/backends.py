"""Explicit authoring backend dispatch for implemented model families."""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Protocol

from synthetic_derivatives.authoring.config import GeneratorConfig
from synthetic_derivatives.authoring.option_daily_generator import OptionDailyGenerator
from synthetic_derivatives.authoring.underlying_daily_generator import (
    UnderlyingDailyGenerator,
)
from synthetic_derivatives.model_families import TDGBM_BSM_MODEL_FAMILY_ID


TDGBM_BSM_AUTHORING_BACKEND_ID = "tdgbm_bsm_authoring_v1"


class AuthoringBackend(Protocol):
    """Construction surface owned by the trusted authoring boundary."""

    model_family_id: str
    backend_id: str

    def validate_config(self, config: GeneratorConfig) -> None: ...

    def create_underlying_generator(
        self, config: GeneratorConfig
    ) -> UnderlyingDailyGenerator: ...

    def create_option_generator(
        self, config: GeneratorConfig
    ) -> OptionDailyGenerator: ...


class TDGBMBSMAuthoringBackend:
    """Thin adapter over the repository's existing P-history/Q-pricing code."""

    model_family_id = TDGBM_BSM_MODEL_FAMILY_ID
    backend_id = TDGBM_BSM_AUTHORING_BACKEND_ID

    def validate_config(self, config: GeneratorConfig) -> None:
        """Reject dispatch if a bypassed config no longer names this backend."""

        if not isinstance(config, GeneratorConfig):
            raise TypeError("authoring backend requires GeneratorConfig")
        expected = {
            "pricing_model": "Black-Scholes-Merton",
            "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
            "physical_process": "QuantLib.BlackScholesMertonProcess",
        }
        changed = [
            field
            for field, value in expected.items()
            if getattr(config, field) != value
        ]
        if changed:
            raise ValueError(
                "tdgbm_bsm authoring backend received incompatible config fields: "
                f"{changed}"
            )

    def create_underlying_generator(
        self, config: GeneratorConfig
    ) -> UnderlyingDailyGenerator:
        return UnderlyingDailyGenerator(config)

    def create_option_generator(
        self, config: GeneratorConfig
    ) -> OptionDailyGenerator:
        return OptionDailyGenerator(config)


class AuthoringBackendRegistry:
    """Immutable, explicit map from model-family IDs to trusted backends."""

    def __init__(self, backends: Iterable[AuthoringBackend]):
        items = tuple(backends)
        if not items:
            raise ValueError("authoring backend registry requires at least one backend")
        family_ids: list[str] = []
        backend_ids: list[str] = []
        for backend in items:
            for field_name in ("model_family_id", "backend_id"):
                value = getattr(backend, field_name, None)
                if not isinstance(value, str) or not value:
                    raise ValueError(
                        f"authoring backend {field_name} must be a non-empty string"
                    )
            family_ids.append(backend.model_family_id)
            backend_ids.append(backend.backend_id)
            for method_name in (
                "validate_config",
                "create_underlying_generator",
                "create_option_generator",
            ):
                if not callable(getattr(backend, method_name, None)):
                    raise ValueError(
                        f"authoring backend must implement {method_name}"
                    )
        if len(family_ids) != len(set(family_ids)):
            raise ValueError("authoring model_family_id values must be unique")
        if len(backend_ids) != len(set(backend_ids)):
            raise ValueError("authoring backend_id values must be unique")
        self._by_family = MappingProxyType(
            {backend.model_family_id: backend for backend in items}
        )

    @property
    def model_family_ids(self) -> tuple[str, ...]:
        return tuple(self._by_family)

    def resolve(self, model_family_id: str) -> AuthoringBackend:
        """Resolve exactly one implemented backend; never consult the catalog."""

        try:
            return self._by_family[model_family_id]
        except KeyError as error:
            raise ValueError(
                "unknown or unimplemented authoring model family: "
                f"{model_family_id!r}"
            ) from error


DEFAULT_AUTHORING_BACKEND_REGISTRY = AuthoringBackendRegistry(
    (TDGBMBSMAuthoringBackend(),)
)


__all__ = [
    "AuthoringBackend",
    "AuthoringBackendRegistry",
    "DEFAULT_AUTHORING_BACKEND_REGISTRY",
    "TDGBMBSMAuthoringBackend",
    "TDGBM_BSM_AUTHORING_BACKEND_ID",
]
