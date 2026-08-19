#!/usr/bin/env python3
"""Sample replayable piecewise-linear P-measure drift/volatility functions.

The script is an authoring step, not part of market-path generation. It samples
node values once, writes the realized nodes and their distribution contract into
the generator config, and thereby keeps subsequent DuckDB builds deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import QuantLib as ql


# Structural choices are fixed; every numerical sampling parameter below is
# drawn within a recorded hard bound before node values are sampled.
NODE_COUNT = 7
SAMPLING_SEED_BOUNDS = (1, 2**32 - 1)
INTERIOR_NODE_OFFSET_BOUNDS = (14, 181)
TERMINAL_NODE_OFFSET_BOUNDS = (182, 365)
DRIFT_PHI_BOUNDS = (0.35, 0.85)
DRIFT_INITIAL_STDDEV_BOUNDS = (0.002, 0.015)
DRIFT_INNOVATION_STDDEV_BOUNDS = (0.005, 0.030)
DRIFT_BOUNDS = (-0.05, 0.15)
LOG_VOLATILITY_PHI_BOUNDS = (0.35, 0.90)
LOG_VOLATILITY_INITIAL_STDDEV_BOUNDS = (0.02, 0.12)
LOG_VOLATILITY_INNOVATION_STDDEV_BOUNDS = (0.04, 0.20)
VOLATILITY_BOUNDS = (0.05, 0.80)


def _derived_seed(sampling_seed: int, *parts: Any) -> int:
    material = "|".join([str(sampling_seed), *(str(part) for part in parts)])
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:4], "big")


def _standard_normal(sampling_seed: int, *parts: Any) -> float:
    uniform = ql.MersenneTwisterUniformRng(_derived_seed(sampling_seed, *parts))
    gaussian = ql.BoxMullerMersenneTwisterGaussianRng(uniform)
    return float(gaussian.next().value())


def _uniform(sampling_seed: int, *parts: Any) -> float:
    generator = ql.MersenneTwisterUniformRng(
        _derived_seed(sampling_seed, *parts)
    )
    return float(generator.next().value())


def _uniform_real(
    sampling_seed: int,
    bounds: tuple[float, float],
    *parts: Any,
) -> float:
    lower, upper = bounds
    return lower + (upper - lower) * _uniform(sampling_seed, *parts)


def _uniform_integer(
    sampling_seed: int,
    bounds: tuple[int, int],
    *parts: Any,
) -> int:
    lower, upper = bounds
    width = upper - lower + 1
    return min(upper, lower + int(_uniform(sampling_seed, *parts) * width))


def _sample_node_offsets(sampling_seed: int) -> tuple[int, ...]:
    """Sample a strictly increasing grid that spans the liquid option horizon."""

    lower, upper = INTERIOR_NODE_OFFSET_BOUNDS
    priorities = sorted(
        (
            _uniform(sampling_seed, "node-offset", "interior", candidate),
            candidate,
        )
        for candidate in range(lower, upper + 1)
    )
    interior = sorted(candidate for _, candidate in priorities[: NODE_COUNT - 2])
    terminal = _uniform_integer(
        sampling_seed,
        TERMINAL_NODE_OFFSET_BOUNDS,
        "node-offset",
        "terminal",
    )
    return (0, *interior, terminal)


def _sample_hyperparameters(sampling_seed: int) -> dict[str, float]:
    distributions = {
        "drift_phi": DRIFT_PHI_BOUNDS,
        "drift_initial_standard_deviation": DRIFT_INITIAL_STDDEV_BOUNDS,
        "drift_innovation_standard_deviation": DRIFT_INNOVATION_STDDEV_BOUNDS,
        "log_volatility_phi": LOG_VOLATILITY_PHI_BOUNDS,
        "log_volatility_initial_standard_deviation": (
            LOG_VOLATILITY_INITIAL_STDDEV_BOUNDS
        ),
        "log_volatility_innovation_standard_deviation": (
            LOG_VOLATILITY_INNOVATION_STDDEV_BOUNDS
        ),
    }
    return {
        name: round(
            _uniform_real(
                sampling_seed,
                bounds,
                "sampling-hyperparameter",
                name,
            ),
            12,
        )
        for name, bounds in distributions.items()
    }


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return min(bounds[1], max(bounds[0], value))


def _anchor(underlying: dict[str, Any], field: str) -> float:
    saved = underlying.get("physical_sampling_anchor")
    if isinstance(saved, dict) and field in saved:
        return float(saved[field])
    raw = underlying[field]
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, dict):
        nodes = raw.get("nodes")
        if isinstance(nodes, list) and nodes:
            return float(nodes[0]["value"])
    raise ValueError(f"cannot resolve {field} sampling anchor for {underlying.get('underlying_id')}")


def _sample_drift_nodes(
    underlying_id: str,
    anchor: float,
    sampling_seed: int,
    offsets: tuple[int, ...],
    hyperparameters: dict[str, float],
) -> list[dict[str, float | int]]:
    state = _clamp(
        anchor
        + hyperparameters["drift_initial_standard_deviation"]
        * _standard_normal(sampling_seed, underlying_id, "physical-drift", 0),
        DRIFT_BOUNDS,
    )
    values = [state]
    for node_index in range(1, len(offsets)):
        innovation = hyperparameters[
            "drift_innovation_standard_deviation"
        ] * _standard_normal(sampling_seed, underlying_id, "physical-drift", node_index)
        state = _clamp(
            anchor
            + hyperparameters["drift_phi"] * (state - anchor)
            + innovation,
            DRIFT_BOUNDS,
        )
        values.append(state)
    return [
        {"day_offset": day_offset, "value": round(value, 10)}
        for day_offset, value in zip(offsets, values)
    ]


def _sample_volatility_nodes(
    underlying_id: str,
    anchor: float,
    sampling_seed: int,
    offsets: tuple[int, ...],
    hyperparameters: dict[str, float],
) -> list[dict[str, float | int]]:
    if anchor <= 0:
        raise ValueError(f"volatility sampling anchor must be positive: {underlying_id}")
    log_anchor = math.log(anchor)
    log_state = log_anchor + hyperparameters[
        "log_volatility_initial_standard_deviation"
    ] * _standard_normal(sampling_seed, underlying_id, "log-physical-volatility", 0)
    state = _clamp(math.exp(log_state), VOLATILITY_BOUNDS)
    log_state = math.log(state)
    values = [state]
    for node_index in range(1, len(offsets)):
        innovation = hyperparameters[
            "log_volatility_innovation_standard_deviation"
        ] * _standard_normal(
            sampling_seed, underlying_id, "log-physical-volatility", node_index
        )
        log_state = (
            log_anchor
            + hyperparameters["log_volatility_phi"] * (log_state - log_anchor)
            + innovation
        )
        state = _clamp(math.exp(log_state), VOLATILITY_BOUNDS)
        log_state = math.log(state)
        values.append(state)
    return [
        {"day_offset": day_offset, "value": round(value, 10)}
        for day_offset, value in zip(offsets, values)
    ]


def sample_config(
    raw: dict[str, Any],
    *,
    parameter_generator_seed: int,
) -> dict[str, Any]:
    """Return config 1.6 with distinct per-underlying sampled functions."""

    if not SAMPLING_SEED_BOUNDS[0] <= parameter_generator_seed <= SAMPLING_SEED_BOUNDS[1]:
        raise ValueError(
            "parameter_generator_seed must be within the recorded 32-bit hard bound"
        )
    sampling_seed = _uniform_integer(
        parameter_generator_seed,
        SAMPLING_SEED_BOUNDS,
        "sampling-seed-generation",
    )
    result = json.loads(json.dumps(raw))
    result.update(
        {
            "schema_version": "1.6.0",
            "generator_config_id": (
                "quantlib-randomized-tdgbm-metals-liquid-option-chain-v4"
            ),
            "generator_version": "0.8.0",
            "snapshot_id": "DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v4",
        }
    )
    used_underlying_seeds: set[int] = set()
    used_offset_grids: set[tuple[int, ...]] = set()
    used_hyperparameter_values = {
        name: set()
        for name in (
            "drift_phi",
            "drift_initial_standard_deviation",
            "drift_innovation_standard_deviation",
            "log_volatility_phi",
            "log_volatility_initial_standard_deviation",
            "log_volatility_innovation_standard_deviation",
        )
    }
    for underlying in result["underlyings"]:
        underlying_id = underlying["underlying_id"]
        for resample_attempt in range(1_000):
            underlying_sampling_seed = _uniform_integer(
                sampling_seed,
                SAMPLING_SEED_BOUNDS,
                "underlying-sampling-seed",
                underlying_id,
                resample_attempt,
            )
            offsets = _sample_node_offsets(underlying_sampling_seed)
            hyperparameters = _sample_hyperparameters(underlying_sampling_seed)
            if underlying_sampling_seed in used_underlying_seeds:
                continue
            if offsets in used_offset_grids:
                continue
            if any(
                value in used_hyperparameter_values[name]
                for name, value in hyperparameters.items()
            ):
                continue
            break
        else:
            raise RuntimeError(
                f"could not sample distinct parameters for {underlying_id}"
            )

        used_underlying_seeds.add(underlying_sampling_seed)
        used_offset_grids.add(offsets)
        for name, value in hyperparameters.items():
            used_hyperparameter_values[name].add(value)

        drift_anchor = _anchor(underlying, "physical_drift")
        volatility_anchor = _anchor(underlying, "physical_volatility")
        underlying["physical_sampling_anchor"] = {
            "drift": drift_anchor,
            "volatility": volatility_anchor,
        }
        underlying["physical_drift"] = {
            "type": "piecewise_linear",
            "nodes": _sample_drift_nodes(
                underlying_id,
                drift_anchor,
                underlying_sampling_seed,
                offsets,
                hyperparameters,
            ),
            "extrapolation": "flat",
        }
        underlying["physical_volatility"] = {
            "type": "piecewise_linear",
            "nodes": _sample_volatility_nodes(
                underlying_id,
                volatility_anchor,
                underlying_sampling_seed,
                offsets,
                hyperparameters,
            ),
            "extrapolation": "flat",
        }
        underlying["physical_sampling_parameters"] = {
            "sampling_seed": underlying_sampling_seed,
            "sampling_seed_hard_bounds": list(SAMPLING_SEED_BOUNDS),
            "resample_attempt": resample_attempt,
            "stream_namespace": (
                "sha256(underlying_sampling_seed,underlying_id,parameter,node_index)"
            ),
            "node_offsets_calendar_days": list(offsets),
            "node_offset_distribution": {
                "node_count": NODE_COUNT,
                "initial_offset": 0,
                "interior": {
                    "distribution": "discrete_uniform_without_replacement",
                    "hard_bounds": list(INTERIOR_NODE_OFFSET_BOUNDS),
                    "realized_values": list(offsets[1:-1]),
                },
                "terminal": {
                    "distribution": "discrete_uniform",
                    "hard_bounds": list(TERMINAL_NODE_OFFSET_BOUNDS),
                    "realized_value": offsets[-1],
                },
            },
            "realized_hyperparameters": hyperparameters,
            "drift_distribution": {
                "process": "bounded_mean_reverting_gaussian_nodes",
                "long_run_mean": "physical_sampling_anchor.drift",
                "autoregressive_coefficient": hyperparameters["drift_phi"],
                "initial_standard_deviation": hyperparameters[
                    "drift_initial_standard_deviation"
                ],
                "innovation_standard_deviation": hyperparameters[
                    "drift_innovation_standard_deviation"
                ],
                "bounds": list(DRIFT_BOUNDS),
            },
            "volatility_distribution": {
                "process": "bounded_mean_reverting_lognormal_nodes",
                "long_run_log_mean": "log(physical_sampling_anchor.volatility)",
                "autoregressive_coefficient": hyperparameters[
                    "log_volatility_phi"
                ],
                "initial_log_standard_deviation": hyperparameters[
                    "log_volatility_initial_standard_deviation"
                ],
                "log_innovation_standard_deviation": hyperparameters[
                    "log_volatility_innovation_standard_deviation"
                ],
                "bounds": list(VOLATILITY_BOUNDS),
            },
        }
        underlying.pop("base_implied_volatility", None)

    result["physical_function_sampling"] = {
        "parameter_generator_seed": parameter_generator_seed,
        "parameter_generator_seed_hard_bounds": list(SAMPLING_SEED_BOUNDS),
        "sampling_seed": sampling_seed,
        "rng": {
            "uniform": "QuantLib.MersenneTwisterUniformRng/sha256-partitioned-v1",
            "gaussian": (
                "QuantLib.BoxMullerMersenneTwisterGaussianRng/"
                "sha256-partitioned-v1"
            ),
        },
        "sampling_seed_distribution": {
            "distribution": "discrete_uniform",
            "hard_bounds": list(SAMPLING_SEED_BOUNDS),
            "generator_seed": parameter_generator_seed,
            "realized_value": sampling_seed,
        },
        "underlying_sampling_seed_distribution": {
            "distribution": "discrete_uniform",
            "hard_bounds": list(SAMPLING_SEED_BOUNDS),
            "generator_seed": sampling_seed,
            "partition": "underlying_id",
            "uniqueness": "enforced_across_underlyings",
        },
        "per_underlying_node_offset_distribution": {
            "node_count": NODE_COUNT,
            "initial_offset": 0,
            "interior_distribution": "discrete_uniform_without_replacement",
            "interior_hard_bounds": list(INTERIOR_NODE_OFFSET_BOUNDS),
            "terminal_distribution": "discrete_uniform",
            "terminal_hard_bounds": list(TERMINAL_NODE_OFFSET_BOUNDS),
            "grid_uniqueness": "enforced_across_underlyings",
        },
        "hyperparameter_distributions": {
            "drift_phi": {
                "distribution": "continuous_uniform",
                "hard_bounds": list(DRIFT_PHI_BOUNDS),
            },
            "drift_initial_standard_deviation": {
                "distribution": "continuous_uniform",
                "hard_bounds": list(DRIFT_INITIAL_STDDEV_BOUNDS),
            },
            "drift_innovation_standard_deviation": {
                "distribution": "continuous_uniform",
                "hard_bounds": list(DRIFT_INNOVATION_STDDEV_BOUNDS),
            },
            "log_volatility_phi": {
                "distribution": "continuous_uniform",
                "hard_bounds": list(LOG_VOLATILITY_PHI_BOUNDS),
            },
            "log_volatility_initial_standard_deviation": {
                "distribution": "continuous_uniform",
                "hard_bounds": list(LOG_VOLATILITY_INITIAL_STDDEV_BOUNDS),
            },
            "log_volatility_innovation_standard_deviation": {
                "distribution": "continuous_uniform",
                "hard_bounds": list(LOG_VOLATILITY_INNOVATION_STDDEV_BOUNDS),
            },
        },
        "hyperparameter_uniqueness": "each realized scalar differs by underlying",
        "drift_value_hard_bounds": list(DRIFT_BOUNDS),
        "volatility_value_hard_bounds": list(VOLATILITY_BOUNDS),
        "stream_partition": (
            "global_sampling_seed -> per-underlying sampling seed; then "
            "sha256(underlying_sampling_seed,underlying_id,purpose,candidate_or_node)"
        ),
        "materialization": "sample-once-and-freeze-nodes-in-config",
    }
    result["q_pricing"] = {
        "risk_neutral_measure_id": "USD-MONEY-MARKET-Q-v1",
        "numeraire_id": "USD-MONEY-MARKET-ACCOUNT-v1",
        "rate_path_id": "USD-FLAT-CONTINUOUS-RATE-v1",
        "measure_change": "girsanov_drift_only",
        "volatility_mapping": "same_deterministic_diffusion",
    }
    result.pop("smile", None)
    return result


def main() -> int:
    """Sample once, freeze the realized functions, and write generator JSON."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--parameter-generator-seed",
        type=int,
        help=(
            "root seed for sampling the sampling seed, offsets, hyperparameters "
            "and nodes; defaults to the input config market-path seed"
        ),
    )
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    parameter_generator_seed = (
        args.parameter_generator_seed
        if args.parameter_generator_seed is not None
        else int(raw["seed"])
    )
    sampled = sample_config(
        raw,
        parameter_generator_seed=parameter_generator_seed,
    )
    rendered = json.dumps(sampled, ensure_ascii=False, indent=2) + "\n"
    if args.output == args.input:
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(args.output)
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
