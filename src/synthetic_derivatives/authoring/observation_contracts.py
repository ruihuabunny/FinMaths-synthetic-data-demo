"""Config-1.8 bridge and volume observation contracts and parsers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntradayBridgeConfig:
    bridge_spec_id: str
    method: str
    steps: int
    grid: str
    variance_clock: str
    endpoint_policy: str
    extrema_policy: str
    cross_asset_policy: str
    stream_namespace: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bridge_spec_id": self.bridge_spec_id,
            "method": self.method,
            "steps": self.steps,
            "grid": self.grid,
            "variance_clock": self.variance_clock,
            "endpoint_policy": self.endpoint_policy,
            "extrema_policy": self.extrema_policy,
            "cross_asset_policy": self.cross_asset_policy,
            "stream_namespace": self.stream_namespace,
        }


@dataclass(frozen=True)
class VolumeModelConfig:
    volume_spec_id: str
    measure: str
    method: str
    rounding: str
    overflow_policy: str
    dependence_policy: str
    stream_namespace: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume_spec_id": self.volume_spec_id,
            "measure": self.measure,
            "method": self.method,
            "rounding": self.rounding,
            "overflow_policy": self.overflow_policy,
            "dependence_policy": self.dependence_policy,
            "stream_namespace": self.stream_namespace,
        }


def parse_intraday_bridge(raw: Any) -> IntradayBridgeConfig:
    """Validate the closed config-1.8 Brownian-bridge contract."""

    if not isinstance(raw, dict):
        raise ValueError("schema_version 1.8.0 requires intraday_bridge")
    expected_keys = {
        "bridge_spec_id",
        "method",
        "steps",
        "grid",
        "variance_clock",
        "endpoint_policy",
        "extrema_policy",
        "cross_asset_policy",
        "stream_namespace",
    }
    if set(raw) != expected_keys:
        raise ValueError(
            "intraday_bridge must contain exactly the versioned contract fields"
        )
    for field_name in ("bridge_spec_id", "stream_namespace"):
        value = raw[field_name]
        if not isinstance(value, str) or not value:
            raise ValueError(f"intraday_bridge.{field_name} must be non-empty")
    fixed_values = {
        "method": "log_price_brownian_bridge",
        "grid": "uniform_calendar_fraction",
        "variance_clock": "integrated_variance",
        "endpoint_policy": "published_quantized_open_close",
        "extrema_policy": "quantized_discrete_grid_only",
        "cross_asset_policy": "marginal_independent_given_close_endpoints",
    }
    for field_name, expected in fixed_values.items():
        if raw[field_name] != expected:
            raise ValueError(f"intraday_bridge.{field_name} must be {expected}")
    steps = raw["steps"]
    if (
        isinstance(steps, bool)
        or not isinstance(steps, int)
        or not 2 <= steps <= 4096
    ):
        raise ValueError(
            "intraday_bridge.steps must be an integer between 2 and 4096"
        )
    return IntradayBridgeConfig(
        bridge_spec_id=raw["bridge_spec_id"],
        method=raw["method"],
        steps=steps,
        grid=raw["grid"],
        variance_clock=raw["variance_clock"],
        endpoint_policy=raw["endpoint_policy"],
        extrema_policy=raw["extrema_policy"],
        cross_asset_policy=raw["cross_asset_policy"],
        stream_namespace=raw["stream_namespace"],
    )


def parse_volume_model(raw: Any) -> VolumeModelConfig:
    """Validate the closed config-1.8 keyed-lognormal volume contract."""

    if not isinstance(raw, dict):
        raise ValueError("schema_version 1.8.0 requires volume_model")
    expected_keys = {
        "volume_spec_id",
        "measure",
        "method",
        "rounding",
        "overflow_policy",
        "dependence_policy",
        "stream_namespace",
    }
    if set(raw) != expected_keys:
        raise ValueError(
            "volume_model must contain exactly the versioned contract fields"
        )
    for field_name in ("volume_spec_id", "stream_namespace"):
        value = raw[field_name]
        if not isinstance(value, str) or not value:
            raise ValueError(f"volume_model.{field_name} must be non-empty")
    fixed_values = {
        "measure": "P",
        "method": "keyed_mean_preserving_lognormal",
        "rounding": "ROUND_HALF_EVEN_INTEGER",
        "overflow_policy": "clip_signed_int64",
        "dependence_policy": (
            "independent_by_underlying_date_and_from_price_streams"
        ),
    }
    for field_name, expected in fixed_values.items():
        if raw[field_name] != expected:
            raise ValueError(f"volume_model.{field_name} must be {expected}")
    return VolumeModelConfig(
        volume_spec_id=raw["volume_spec_id"],
        measure=raw["measure"],
        method=raw["method"],
        rounding=raw["rounding"],
        overflow_policy=raw["overflow_policy"],
        dependence_policy=raw["dependence_policy"],
        stream_namespace=raw["stream_namespace"],
    )
