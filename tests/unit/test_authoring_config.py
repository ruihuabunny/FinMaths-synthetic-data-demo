from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

from synthetic_derivatives.authoring.config import (
    DeterministicFunction,
    DeterministicFunctionNode,
    load_generator_config,
    quantize_to_increment,
)
from synthetic_derivatives.authoring.underlying_daily_generator import (
    UnderlyingDailyGenerator,
)


def _bridge_template(repository_root: Path) -> Path:
    return repository_root / (
        "authoring/templates/quantlib_bsm_brownian_bridge_volume.template.json"
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


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("calendar", "TARGET"),
        ("day_count", "Actual360"),
        ("pricing_model", "Heston"),
        ("pricing_engine", "QuantLib.BinomialVanillaEngine"),
        ("physical_process", "QuantLib.HestonProcess"),
    ],
)
def test_runtime_labels_must_match_the_implemented_generator(
    tmp_path: Path,
    smoke_config_path: Path,
    field_name: str,
    invalid_value: str,
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw[field_name] = invalid_value
    config_path = tmp_path / f"invalid-{field_name}.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=field_name):
        load_generator_config(config_path)


def test_rng_label_must_match_the_implemented_stream_partition(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw["rng"] = (
        "QuantLib.BoxMullerMersenneTwisterGaussianRng/"
        "underlying-factor-idiosyncratic-sha256-v1"
    )
    config_path = tmp_path / "invalid-rng.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="rng must match"):
        load_generator_config(config_path)


def test_quote_precision_controls_generated_values_and_metadata(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw["quote_decimal_places"] = 4
    config_path = tmp_path / "four-decimal-quotes.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_generator_config(config_path)
    generator = UnderlyingDailyGenerator(config)

    initial_row = generator.initial_underlying_daily_row(
        config.underlyings[0], "precision-test"
    )
    metadata_row = generator.pricing_metadata_row(
        config.underlyings[0], config.start_date, None, "precision-test"
    )

    assert initial_row[3].as_tuple().exponent == -4
    assert json.loads(metadata_row[19]) == {
        "dtype": "float64",
        "market_quote_decimal_places": 4,
        "minimum_price_increments": {
            "option": "0.01",
            "underlying": "0.01",
        },
    }


def test_market_prices_round_to_configured_minimum_increments(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw["underlying_minimum_price_increment"] = "0.05"
    raw["option_minimum_price_increment"] = "0.01"
    raw["underlyings"][0]["initial_spot"] = 80.03
    config_path = tmp_path / "minimum-price-increments.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_generator_config(config_path)
    generator = UnderlyingDailyGenerator(config)

    initial_row = generator.initial_underlying_daily_row(
        config.underlyings[0], "increment-test"
    )

    assert initial_row[3:8] == (Decimal("80.05000000"),) * 5
    assert quantize_to_increment(
        Decimal("1.025"), Decimal("0.05"), decimal_places=8
    ) == Decimal("1.00000000")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("underlying_minimum_price_increment", "0"),
        ("underlying_minimum_price_increment", "0.000000001"),
        ("option_minimum_price_increment", "-0.01"),
        ("option_minimum_price_increment", "not-a-decimal"),
    ],
)
def test_minimum_price_increment_must_be_positive_and_storable(
    tmp_path: Path,
    smoke_config_path: Path,
    field_name: str,
    invalid_value: str,
) -> None:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw[field_name] = invalid_value
    config_path = tmp_path / f"invalid-{field_name}.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=field_name):
        load_generator_config(config_path)


def test_config_18_parses_closed_bridge_and_volume_contracts(
    repository_root: Path,
) -> None:
    config = load_generator_config(_bridge_template(repository_root))

    assert config.schema_version == "1.8.0"
    assert config.rng.endswith("semantic-keyed-authoring-streams-sha256-v2")
    assert config.intraday_bridge is not None
    assert config.intraday_bridge.steps == 64
    assert config.intraday_bridge.endpoint_policy == (
        "published_quantized_open_close"
    )
    assert config.volume_model is not None
    assert config.volume_model.measure == "P"
    assert config.underlyings[0].base_volume == 1_000_000
    assert config.underlyings[0].volume_log_stddev == pytest.approx(
        0.1435941754
    )
    assert [item.measure for item in config.underlying_dependence_specs] == [
        "P",
        "Q",
    ]


@pytest.mark.parametrize(
    ("section", "field", "invalid_value", "message"),
    [
        ("intraday_bridge", "steps", True, "steps"),
        ("intraday_bridge", "steps", 1, "steps"),
        ("intraday_bridge", "grid", "business_fraction", "grid"),
        (
            "intraday_bridge",
            "endpoint_policy",
            "raw_close",
            "endpoint_policy",
        ),
        (
            "intraday_bridge",
            "cross_asset_policy",
            "joint",
            "cross_asset_policy",
        ),
        ("volume_model", "measure", "Q", "measure"),
        ("volume_model", "rounding", "floor", "rounding"),
        ("volume_model", "overflow_policy", "raise", "overflow_policy"),
    ],
)
def test_config_18_rejects_noncanonical_observation_contracts(
    tmp_path: Path,
    repository_root: Path,
    section: str,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    raw = json.loads(_bridge_template(repository_root).read_text(encoding="utf-8"))
    raw[section][field] = invalid_value
    path = tmp_path / f"invalid-{section}-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_generator_config(path)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("base_volume", True, "base_volume"),
        ("base_volume", 0, "base_volume"),
        ("base_volume", 2**63, "base_volume"),
        ("volume_log_stddev", -0.1, "volume_log_stddev"),
        ("volume_log_stddev", 2.1, "volume_log_stddev"),
        ("volume_log_stddev", float("nan"), "volume_log_stddev"),
    ],
)
def test_config_18_rejects_invalid_underlying_volume_parameters(
    tmp_path: Path,
    repository_root: Path,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    raw = json.loads(_bridge_template(repository_root).read_text(encoding="utf-8"))
    raw["underlyings"][0][field] = invalid_value
    path = tmp_path / f"invalid-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_generator_config(path)


@pytest.mark.parametrize("missing", ["intraday_bridge", "volume_model"])
def test_config_18_requires_both_observation_contracts(
    tmp_path: Path, repository_root: Path, missing: str
) -> None:
    raw = json.loads(_bridge_template(repository_root).read_text(encoding="utf-8"))
    raw.pop(missing)
    path = tmp_path / f"missing-{missing}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=missing):
        load_generator_config(path)


def test_config_18_requires_new_rng_and_distinct_observation_namespaces(
    tmp_path: Path, repository_root: Path
) -> None:
    raw = json.loads(_bridge_template(repository_root).read_text(encoding="utf-8"))
    raw["rng"] = (
        "QuantLib.BoxMullerMersenneTwisterGaussianRng/"
        "underlying-factor-idiosyncratic-sha256-v1"
    )
    wrong_rng = tmp_path / "wrong-rng.json"
    wrong_rng.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="rng must match"):
        load_generator_config(wrong_rng)

    raw = json.loads(_bridge_template(repository_root).read_text(encoding="utf-8"))
    raw["volume_model"]["stream_namespace"] = raw["intraday_bridge"][
        "stream_namespace"
    ]
    collision = tmp_path / "namespace-collision.json"
    collision.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="namespaces must be distinct"):
        load_generator_config(collision)
