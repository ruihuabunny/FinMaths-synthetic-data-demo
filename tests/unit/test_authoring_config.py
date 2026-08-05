from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from synthetic_derivatives.authoring.config import (
    DeterministicFunction,
    DeterministicFunctionNode,
    load_generator_config,
)


def test_piecewise_linear_function_reduces_exactly_over_an_interval() -> None:
    function = DeterministicFunction(
        function_type="piecewise_linear",
        nodes=(
            DeterministicFunctionNode(day_offset=0, value=0.2),
            DeterministicFunctionNode(day_offset=2, value=0.4),
        ),
    )

    assert function.value_at(-3) == 0.2
    assert function.value_at(1) == pytest.approx(0.3)
    assert function.value_at(7) == 0.4
    assert function.interval_average(0, 1) == pytest.approx(0.25)
    assert function.interval_root_mean_square(0, 1) == pytest.approx(
        math.sqrt(0.04 + 0.02 + 0.01 / 3.0)
    )


def test_piecewise_linear_function_integrates_across_nodes_and_flat_extrapolation() -> None:
    function = DeterministicFunction(
        function_type="piecewise_linear",
        nodes=(
            DeterministicFunctionNode(day_offset=0, value=0.1),
            DeterministicFunctionNode(day_offset=2, value=0.3),
            DeterministicFunctionNode(day_offset=4, value=0.2),
        ),
    )

    assert function.interval_average(-2, 6) == pytest.approx(0.1875)


def test_time_function_requires_v1_1_schema(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw["underlyings"][0]["physical_drift"] = {
        "type": "piecewise_linear",
        "nodes": [
            {"day_offset": 0, "value": 0.06},
            {"day_offset": 30, "value": 0.08},
        ],
    }
    config_path = tmp_path / "invalid-v1.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="require schema_version 1.1.0"):
        load_generator_config(config_path)


def test_time_function_rejects_nonpositive_volatility_node(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw["schema_version"] = "1.1.0"
    raw["underlyings"][0]["physical_volatility"] = {
        "type": "piecewise_linear",
        "nodes": [
            {"day_offset": 0, "value": 0.18},
            {"day_offset": 30, "value": 0.0},
        ],
    }
    config_path = tmp_path / "invalid-vol.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="volatility must be positive"):
        load_generator_config(config_path)
