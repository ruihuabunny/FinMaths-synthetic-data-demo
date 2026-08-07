from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.option_daily_generator import OptionDailyGenerator
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def _small_q_config(repository_root: Path):
    config = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json"
    )
    return replace(
        config,
        snapshot_id="DERIVATIVES-Q-UNIT-v2",
        generator_config_id="quantlib-q-unit-v2",
        business_days=2,
        option_templates=config.option_templates[:2],
    )


def test_physical_node_sampling_script_is_replayable(
    tmp_path: Path, repository_root: Path
) -> None:
    source = (
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json"
    )
    script = repository_root / "scripts/sample_physical_dynamics.py"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    different_seed = tmp_path / "different-seed.json"
    for output in (first, second):
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--input",
                str(source),
                "--output",
                str(output),
            ],
            check=True,
        )
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(source),
            "--output",
            str(different_seed),
            "--parameter-generator-seed",
            "20260807",
        ],
        check=True,
    )

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() != different_seed.read_bytes()
    raw = json.loads(first.read_text(encoding="utf-8"))
    sampling = raw["physical_function_sampling"]
    assert sampling["materialization"] == (
        "sample-once-and-freeze-nodes-in-config"
    )
    assert sampling["parameter_generator_seed"] == raw["seed"]
    assert sampling["parameter_generator_seed_hard_bounds"] == [1, 2**32 - 1]
    seed_contract = sampling["sampling_seed_distribution"]
    assert seed_contract["hard_bounds"] == [1, 2**32 - 1]
    assert seed_contract["realized_value"] == sampling["sampling_seed"]
    assert seed_contract["hard_bounds"][0] <= sampling["sampling_seed"] <= (
        seed_contract["hard_bounds"][1]
    )

    sampled_underlyings = [
        underlying["physical_sampling_parameters"]
        for underlying in raw["underlyings"]
    ]
    assert len({item["sampling_seed"] for item in sampled_underlyings}) == len(
        sampled_underlyings
    )
    assert len(
        {tuple(item["node_offsets_calendar_days"]) for item in sampled_underlyings}
    ) == len(sampled_underlyings)
    for item in sampled_underlyings:
        assert item["sampling_seed_hard_bounds"] == [1, 2**32 - 1]
        offsets = item["node_offsets_calendar_days"]
        offset_contract = item["node_offset_distribution"]
        assert len(offsets) == offset_contract["node_count"] == 7
        assert offsets[0] == offset_contract["initial_offset"] == 0
        assert offsets == sorted(set(offsets))
        assert all(
            offset_contract["interior"]["hard_bounds"][0]
            <= value
            <= offset_contract["interior"]["hard_bounds"][1]
            for value in offsets[1:-1]
        )
        assert offset_contract["terminal"]["hard_bounds"][0] <= offsets[-1] <= (
            offset_contract["terminal"]["hard_bounds"][1]
        )
        assert item["drift_distribution"]["bounds"] == [-0.05, 0.15]
        assert item["volatility_distribution"]["bounds"] == [0.05, 0.8]

    for name, contract in sampling["hyperparameter_distributions"].items():
        realized_values = [
            item["realized_hyperparameters"][name]
            for item in sampled_underlyings
        ]
        assert len(set(realized_values)) == len(sampled_underlyings)
        assert all(
            contract["hard_bounds"][0] <= value <= contract["hard_bounds"][1]
            for value in realized_values
        )
    assert sampling["drift_value_hard_bounds"] == [-0.05, 0.15]
    assert sampling["volatility_value_hard_bounds"] == [0.05, 0.8]
    assert all(
        len({node["value"] for node in underlying["physical_drift"]["nodes"]}) > 1
        and len(
            {node["value"] for node in underlying["physical_volatility"]["nodes"]}
        )
        > 1
        for underlying in raw["underlyings"]
    )


def test_q_snapshot_materializes_initial_state_and_quote_iv_audit(
    tmp_path: Path, repository_root: Path
) -> None:
    config = _small_q_config(repository_root)
    database = tmp_path / "q-pricing.duckdb"
    with AuthoringPipeline(database, config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        initial_rows = pipeline.connection.execute(
            """
            SELECT count(*)
            FROM market.underlying_daily daily
            JOIN market.underlyings master USING (snapshot_id, underlying_id)
            WHERE daily.date = ?
              AND daily.spot_open = master.initial_spot
              AND daily.spot_high = master.initial_spot
              AND daily.spot_low = master.initial_spot
              AND daily.spot_close = master.initial_spot
            """,
            [config.start_date],
        ).fetchone()[0]
        physical_rows = [
            json.loads(row[0])
            for row in pipeline.connection.execute(
                """
                SELECT physical_dynamics
                FROM market.pricing_metadata
                WHERE underlying_id = ?
                ORDER BY valuation_date
                """,
                [config.underlyings[0].underlying_id],
            ).fetchall()
        ]
        audit = pipeline.connection.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE iv_status = 'CONVERGED'),
                   count(*) FILTER (WHERE implied_volatility IS NULL),
                   max(abs(implied_volatility - q_effective_volatility))
            FROM market.option_pricing_audit
            """
        ).fetchone()
        solver_audit_view_count = pipeline.connection.execute(
            """
            SELECT count(*) FROM information_schema.views
            WHERE table_schema = 'solver_visible'
              AND table_name = 'option_pricing_audit'
            """
        ).fetchone()[0]

    assert result["status"] == "COMPLETED"
    assert initial_rows == len(config.underlyings)
    assert physical_rows[0]["state_role"] == "initial_condition"
    assert "interval_start" not in physical_rows[0]
    assert physical_rows[1]["state_role"] == "interval_transition"
    assert physical_rows[1]["interval_start"] == config.start_date.isoformat()
    assert audit[0] == result["summary"]["option_daily_count"]
    assert audit[1] == audit[0]
    assert audit[2] == 0
    assert audit[3] < 1e-6
    assert solver_audit_view_count == 0


def test_q_effective_volatility_is_integrated_piecewise_variance(
    repository_root: Path,
) -> None:
    config = _small_q_config(repository_root)
    underlying = config.underlyings[0]
    option_template = config.option_templates[0]
    option_generator = OptionDailyGenerator(config)
    contract = option_generator.option_contract_row(
        underlying, option_template, "unit-run"
    )
    expiry = contract[6]
    expected = underlying.physical_volatility_function.interval_root_mean_square(
        0.0, float((expiry - config.start_date).days)
    )
    actual = option_generator._pricing_volatility(
        underlying,
        config.start_date,
        expiry,
        underlying.initial_spot,
        contract[5],
        (expiry - config.start_date).days / 365.0,
    )

    assert actual == pytest.approx(expected)


def test_option_quotes_use_rounded_underlying_and_option_increments(
    repository_root: Path,
) -> None:
    config = replace(
        _small_q_config(repository_root),
        underlying_minimum_price_increment=Decimal("0.01"),
        option_minimum_price_increment=Decimal("0.01"),
    )
    underlying = config.underlyings[0]
    generator = OptionDailyGenerator(config)
    contract = generator.option_contract_row(
        underlying, config.option_templates[0], "increment-test"
    )

    first = generator.option_daily_result(
        underlying,
        contract,
        config.start_date,
        Decimal("100.001"),
        "increment-test",
    )
    second = generator.option_daily_result(
        underlying,
        contract,
        config.start_date,
        Decimal("100.004"),
        "increment-test",
    )

    assert first is not None and second is not None
    assert first.quote_row == second.quote_row
    assert first.pricing_audit_row == second.pricing_audit_row
    assert all(
        value % Decimal("0.01") == 0
        for value in first.quote_row[10:14]
    )
