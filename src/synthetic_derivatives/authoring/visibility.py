"""Solver-visible metadata projection and private-value leakage guards."""

from __future__ import annotations

from typing import Any

PRIVATE_METADATA_KEYS = frozenset(
    {
        "seed",
        "sampling_seed",
        "mutation_seed",
        "parameter_generator_seed",
        "value",
        "drift",
        "volatility",
        "clean_quotes",
        "quote_noise_realization",
        "requested_signature",
        "private_truth_signature",
        "mutation_lineage",
        "canonical_answer",
        "bridge_spec_id",
        "volume_spec_id",
        "stream_namespace",
        "base_volume",
        "volume_log_stddev",
        "bridge_increment",
    }
)


def public_dynamics_projection(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose node locations and model semantics without private node heights."""

    def function_projection(function: Any) -> dict[str, Any]:
        if not isinstance(function, dict):
            raise ValueError("node function metadata must be an object")
        nodes = function.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("node function metadata requires public locations")
        return {
            "type": function.get("type"),
            "node_offsets_calendar_days": [int(node["day_offset"]) for node in nodes],
            "extrapolation": function.get("extrapolation"),
        }

    return {
        key: raw[key]
        for key in (
            "measure",
            "process",
            "time_origin",
            "time_axis",
            "state_precision_contract",
        )
        if key in raw
    } | {
        "drift_function": function_projection(raw["drift_function"]),
        "volatility_function": function_projection(raw["volatility_function"]),
    }


def assert_no_private_metadata_leakage(value: Any, path: str = "public") -> None:
    """Recursively reject private values even when nested or renamed by a wrapper."""

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in PRIVATE_METADATA_KEYS:
                raise ValueError(f"private metadata field leaked at {path}.{key}")
            assert_no_private_metadata_leakage(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_private_metadata_leakage(item, f"{path}[{index}]")

