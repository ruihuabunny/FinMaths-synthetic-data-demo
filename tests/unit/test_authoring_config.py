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


def test_f2a_generator_variant_and_mutation_contract_share_tick_semantics(
    repository_root: Path,
) -> None:
    authoring = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_f2a_parent_v2.json"
    )
    variant = json.loads(
        (
            repository_root
            / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
        ).read_text(encoding="utf-8")
    )
    mutation = json.loads(
        (
            repository_root / "configs/mutations/f2a_point_v1.json"
        ).read_text(encoding="utf-8")
    )

    market_contract = variant["market_contract"]
    assert authoring.underlying_minimum_price_increment == Decimal(
        market_contract["underlying_minimum_price_increment"]
    )
    assert authoring.option_minimum_price_increment == Decimal(
        market_contract["option_minimum_price_increment"]
    )
    assert mutation["price_increment_source"] == "generator_config"
    assert all(
        isinstance(ticks, int) and not isinstance(ticks, bool) and ticks > 0
        for ticks in mutation["absolute_tick_grid"]
    )
    assert variant["execution_contract"]["option_execution"] == (
        "directional_bid_ask"
    )
    assert Decimal(
        variant["execution_contract"]["option_fee_per_contract_per_side"]
    ) > 0
    assert Decimal(
        variant["execution_contract"][
            "underlying_trading_cost_rate_per_side"
        ]
    ) == Decimal("0.0005")


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
