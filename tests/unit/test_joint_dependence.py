from __future__ import annotations

import json
import math
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import QuantLib as ql
import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.option_daily_generator import OptionDailyGenerator
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.authoring.underlying_daily_generator import (
    UnderlyingDailyGenerator,
)


def _joint_raw(
    repository_root: Path,
    *,
    factor_loading_matrix: list[list[float]] | None = None,
) -> dict[str, Any]:
    source = (
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json"
    )
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw.update(
        {
            "schema_version": "1.7.0",
            "generator_config_id": "quantlib-bsm-joint-dependence-unit-v1",
            "generator_version": "0.9.0",
            "snapshot_id": "DERIVATIVES-JOINT-DEPENDENCE-UNIT-v1",
            "business_days": 2,
        }
    )
    raw["underlyings"] = raw["underlyings"][:2]
    physical = raw.pop("underlying_simulation")
    physical.update(
        {
            "dependence_spec_id": "SYNTH-UNIT-P-SPOT-FACTOR-v1",
            "driver_order": [
                underlying["underlying_id"] for underlying in raw["underlyings"]
            ],
            "factor_loading_matrix": (
                factor_loading_matrix
                if factor_loading_matrix is not None
                else [[0.8], [0.5]]
            ),
            "regime_id": "constant-unit-joint-dependence",
        }
    )
    pricing = deepcopy(physical)
    pricing.update(
        {
            "dependence_spec_id": "SYNTH-UNIT-Q-SPOT-FACTOR-v1",
            "measure": "Q",
            "source_dependence_spec_id": physical["dependence_spec_id"],
            "mapping_id": "SYNTH-UNIT-GIRSANOV-COVARIANCE-v1",
            "mapping_type": "girsanov_drift_only_same_brownian_covariance",
            "risk_neutral_measure_id": raw["q_pricing"][
                "risk_neutral_measure_id"
            ],
            "numeraire_id": raw["q_pricing"]["numeraire_id"],
            "rate_path_id": raw["q_pricing"]["rate_path_id"],
        }
    )
    raw["underlying_dependence_specs"] = [physical, pricing]
    return raw


def _write_config(tmp_path: Path, raw: dict[str, Any], name: str) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_config_17_derives_measure_qualified_psd_dependence(
    tmp_path: Path, repository_root: Path
) -> None:
    config = load_generator_config(
        _write_config(tmp_path, _joint_raw(repository_root), "joint.json")
    )
    physical, pricing = config.underlying_dependence_specs

    assert config.schema_version == "1.7.0"
    assert config.underlying_simulation is physical
    assert config.q_underlying_dependence is pricing
    assert (physical.measure, pricing.measure) == ("P", "Q")
    assert physical.dependence_spec_id != pricing.dependence_spec_id
    assert pricing.source_dependence_spec_id == physical.dependence_spec_id
    assert pricing.mapping_id == "SYNTH-UNIT-GIRSANOV-COVARIANCE-v1"
    assert pricing.mapping_type == (
        "girsanov_drift_only_same_brownian_covariance"
    )
    assert physical.factor_loading_matrix == pricing.factor_loading_matrix == (
        (0.8,),
        (0.5,),
    )
    assert physical.idiosyncratic_diagonal == pytest.approx((0.36, 0.75))
    assert physical.correlation_matrix == pricing.correlation_matrix == (
        (1.0, 0.4),
        (0.4, 1.0),
    )
    for specification in (physical, pricing):
        assert specification.correlation_matrix == tuple(
            tuple(
                specification.correlation_matrix[column][row]
                for column in range(len(specification.driver_order))
            )
            for row in range(len(specification.driver_order))
        )
        assert all(
            specification.correlation_matrix[index][index] == 1.0
            for index in range(len(specification.driver_order))
        )
        assert all(
            math.isfinite(value) and -1.0 <= value <= 1.0
            for row in specification.correlation_matrix
            for value in row
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("ragged", "must be non-ragged"),
        ("duplicate_driver", "driver_order must be unique"),
        ("missing_underlying", "must contain every underlying_id"),
        ("derivative_driver", "option/derivative drivers are forbidden"),
        ("nan", "must be a finite number"),
        ("infinity", "must be a finite number"),
        ("row_norm", "row norm must not exceed 1"),
        ("direct_correlation", "is derived from factor loadings"),
        ("different_q_loading", "requires identical P/Q driver order"),
        ("wrong_source", "must identify the declared P spec"),
        ("wrong_q_context", "must match q_pricing"),
        ("wrong_measure_order", "exact declared order P then Q"),
    ],
)
def test_invalid_measure_qualified_dependence_is_rejected(
    tmp_path: Path,
    repository_root: Path,
    case: str,
    message: str,
) -> None:
    raw = _joint_raw(repository_root)
    physical, pricing = raw["underlying_dependence_specs"]
    first_id, second_id = physical["driver_order"]
    if case == "ragged":
        pricing["factor_loading_matrix"] = [[0.8], [0.5, 0.1]]
    elif case == "duplicate_driver":
        pricing["driver_order"] = [first_id, first_id]
    elif case == "missing_underlying":
        physical["driver_order"] = [first_id]
        physical["factor_loading_matrix"] = [[0.8]]
    elif case == "derivative_driver":
        physical["driver_order"] = [first_id, "OPTION-CALL-UNIT-v1"]
    elif case == "nan":
        physical["factor_loading_matrix"][0][0] = float("nan")
    elif case == "infinity":
        pricing["factor_loading_matrix"][0][0] = float("inf")
    elif case == "row_norm":
        physical["factor_loading_matrix"] = [[0.8, 0.8], [0.5, 0.1]]
    elif case == "direct_correlation":
        pricing["correlation_matrix"] = [[1.0, 0.4], [0.4, 1.0]]
    elif case == "different_q_loading":
        pricing["factor_loading_matrix"][0][0] = 0.7
    elif case == "wrong_source":
        pricing["source_dependence_spec_id"] = "UNKNOWN-P-SPEC"
    elif case == "wrong_q_context":
        pricing["rate_path_id"] = "OTHER-RATE-PATH"
    elif case == "wrong_measure_order":
        raw["underlying_dependence_specs"] = [pricing, physical]
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(case)

    path = _write_config(tmp_path, raw, f"invalid-{case}.json")
    with pytest.raises(ValueError, match=message):
        load_generator_config(path)


def test_pipeline_persists_and_safely_projects_p_q_dependence(
    tmp_path: Path, repository_root: Path
) -> None:
    config = load_generator_config(
        _write_config(tmp_path, _joint_raw(repository_root), "persist.json")
    )
    with AuthoringPipeline(tmp_path / "joint.duckdb", config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        private_rows = pipeline.connection.execute(
            """
            SELECT measure, dependence_spec_id, source_dependence_spec_id,
                   mapping_id, risk_neutral_measure_id, numeraire_id,
                   rate_path_id, driver_order, factor_loading_matrix,
                   idiosyncratic_diagonal, correlation_matrix
            FROM market.underlying_dependence
            ORDER BY CASE measure WHEN 'P' THEN 0 ELSE 1 END
            """
        ).fetchall()
        public_rows = pipeline.connection.execute(
            """
            SELECT measure, dependence_spec_id, source_dependence_spec_id,
                   mapping_id, driver_order, correlation_matrix
            FROM solver_visible.underlying_dependence
            ORDER BY CASE measure WHEN 'P' THEN 0 ELSE 1 END
            """
        ).fetchall()
        public_columns = {
            row[0]
            for row in pipeline.connection.execute(
                "DESCRIBE solver_visible.underlying_dependence"
            ).fetchall()
        }
        dynamics = [
            (json.loads(row[0]), json.loads(row[1]))
            for row in pipeline.connection.execute(
                """
                SELECT physical_dynamics, pricing_dynamics
                FROM market.pricing_metadata
                ORDER BY valuation_date, underlying_id
                """
            ).fetchall()
        ]

    assert result["summary"]["underlying_dependence_count"] == 2
    assert result["table_stats"]["underlying_dependence"]["inserted"] == 2
    assert [row[0] for row in private_rows] == ["P", "Q"]
    assert private_rows[1][2] == private_rows[0][1]
    assert private_rows[1][3] == "SYNTH-UNIT-GIRSANOV-COVARIANCE-v1"
    assert private_rows[0][7:] == private_rows[1][7:]
    assert len(public_rows) == 2
    assert {"seed", "generator_config_id", "created_run_id"}.isdisjoint(
        public_columns
    )
    assert all(
        physical["dependence_spec_id"] == "SYNTH-UNIT-P-SPOT-FACTOR-v1"
        and pricing["underlying_dependence"]["dependence_spec_id"]
        == "SYNTH-UNIT-Q-SPOT-FACTOR-v1"
        for physical, pricing in dynamics
    )


def test_historical_shock_consumes_only_p_dependence(
    tmp_path: Path,
    repository_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_generator_config(
        _write_config(tmp_path, _joint_raw(repository_root), "p-shock.json")
    )
    generator = UnderlyingDailyGenerator(config)
    calls: list[tuple[Any, ...]] = []

    def fixed_gaussian(*parts: Any) -> float:
        calls.append(parts)
        return 0.0

    monkeypatch.setattr(generator, "_gaussian", fixed_gaussian)
    generator.underlying_close_shock(
        config.underlyings[0].underlying_id, config.start_date
    )

    assert calls
    assert all(parts[0] == "underlying-simulation" for parts in calls)
    assert all(parts[1] == "P" for parts in calls)
    assert all(
        parts[2] == config.underlying_simulation.dependence_spec_id
        for parts in calls
    )


def _quantlib_marginal_metrics(config: Any) -> tuple[float, ...]:
    generator = OptionDailyGenerator(config)
    underlying = config.underlyings[0]
    template = next(
        item
        for item in config.option_templates
        if item.call_put == "call" and item.strike_moneyness == Decimal("1.0")
    )
    contract = generator.option_contract_row(underlying, template, "marginal-run")
    strike = contract[5]
    expiry = contract[6]
    evaluation_date = ql.Date(
        config.start_date.day, config.start_date.month, config.start_date.year
    )
    expiry_date = ql.Date(expiry.day, expiry.month, expiry.year)
    ql.Settings.instance().evaluationDate = evaluation_date
    volatility = generator._pricing_volatility(
        underlying,
        config.start_date,
        expiry,
        underlying.initial_spot,
        strike,
        (expiry - config.start_date).days / 365.0,
    )
    process = ql.BlackScholesMertonProcess(
        ql.QuoteHandle(ql.SimpleQuote(float(underlying.initial_spot))),
        ql.YieldTermStructureHandle(
            ql.FlatForward(evaluation_date, underlying.dividend_yield, generator.day_count)
        ),
        ql.YieldTermStructureHandle(
            ql.FlatForward(evaluation_date, underlying.risk_free_rate, generator.day_count)
        ),
        ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(
                evaluation_date, generator.calendar, volatility, generator.day_count
            )
        ),
    )
    option = ql.VanillaOption(
        ql.PlainVanillaPayoff(ql.Option.Call, float(strike)),
        ql.EuropeanExercise(expiry_date),
    )
    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
    return (
        option.NPV(),
        option.delta(),
        option.gamma(),
        option.vega(),
        option.theta(),
        option.rho(),
    )


def test_identity_and_non_diagonal_dependence_leave_bsm_margins_unchanged(
    tmp_path: Path, repository_root: Path
) -> None:
    identity = load_generator_config(
        _write_config(
            tmp_path,
            _joint_raw(repository_root, factor_loading_matrix=[[0.0], [0.0]]),
            "identity.json",
        )
    )
    correlated = load_generator_config(
        _write_config(
            tmp_path,
            _joint_raw(repository_root, factor_loading_matrix=[[0.8], [0.5]]),
            "correlated.json",
        )
    )
    identity_generator = OptionDailyGenerator(identity)
    correlated_generator = OptionDailyGenerator(correlated)

    assert identity.q_underlying_dependence.correlation_matrix == (
        (1.0, 0.0),
        (0.0, 1.0),
    )
    assert correlated.q_underlying_dependence.correlation_matrix == (
        (1.0, 0.4),
        (0.4, 1.0),
    )
    identity_quotes = []
    correlated_quotes = []
    for underlying_index, template in (
        (underlying_index, template)
        for underlying_index in range(len(identity.underlyings))
        for template in identity.option_templates
    ):
        identity_underlying = identity.underlyings[underlying_index]
        correlated_underlying = correlated.underlyings[underlying_index]
        identity_contract = identity_generator.option_contract_row(
            identity_underlying, template, "same-run"
        )
        correlated_contract = correlated_generator.option_contract_row(
            correlated_underlying, template, "same-run"
        )
        identity_quotes.append(
            identity_generator.option_daily_row(
                identity_underlying,
                identity_contract,
                identity.start_date,
                identity_underlying.initial_spot,
                "same-run",
            )
        )
        correlated_quotes.append(
            correlated_generator.option_daily_row(
                correlated_underlying,
                correlated_contract,
                correlated.start_date,
                correlated_underlying.initial_spot,
                "same-run",
            )
        )

    assert identity_quotes == correlated_quotes
    assert _quantlib_marginal_metrics(identity) == _quantlib_marginal_metrics(
        correlated
    )


@pytest.mark.parametrize("change", ["lambda", "driver_order", "mapping_id"])
def test_dependence_change_requires_new_snapshot_identity(
    tmp_path: Path, repository_root: Path, change: str
) -> None:
    base_raw = _joint_raw(repository_root)
    base_path = _write_config(tmp_path, base_raw, f"base-{change}.json")
    database = tmp_path / f"immutable-{change}.duckdb"
    with AuthoringPipeline(database, load_generator_config(base_path)) as pipeline:
        pipeline.create_smoke_snapshot()

    changed_raw = deepcopy(base_raw)
    physical, pricing = changed_raw["underlying_dependence_specs"]
    if change == "lambda":
        physical["factor_loading_matrix"][0][0] = 0.7
        pricing["factor_loading_matrix"][0][0] = 0.7
    elif change == "driver_order":
        for specification in (physical, pricing):
            specification["driver_order"].reverse()
            specification["factor_loading_matrix"].reverse()
    else:
        pricing["mapping_id"] = "SYNTH-UNIT-GIRSANOV-COVARIANCE-v2"
    changed_path = _write_config(tmp_path, changed_raw, f"changed-{change}.json")

    with AuthoringPipeline(database, load_generator_config(changed_path)) as pipeline:
        with pytest.raises(
            ValueError, match="underlying dependence cannot change within one snapshot"
        ):
            pipeline.sync_config()


def test_common_flat_rate_path_rejects_different_underlying_rates(
    tmp_path: Path, repository_root: Path
) -> None:
    raw = _joint_raw(repository_root)
    raw["underlyings"][1]["risk_free_rate"] = 0.04
    config = load_generator_config(
        _write_config(tmp_path, raw, "different-rates.json")
    )

    with AuthoringPipeline(tmp_path / "different-rates.duckdb", config) as pipeline:
        with pytest.raises(
            ValueError,
            match="one flat rate_path_id cannot identify different underlying rates",
        ):
            pipeline.create_smoke_snapshot()


def test_frozen_joint_snapshot_reopens_read_only_and_remains_byte_identical(
    tmp_path: Path, repository_root: Path
) -> None:
    config = load_generator_config(
        _write_config(tmp_path, _joint_raw(repository_root), "frozen.json")
    )
    database = tmp_path / "frozen.duckdb"
    with AuthoringPipeline(database, config) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.freeze()
    before = database.read_bytes()

    with AuthoringPipeline(database, config) as pipeline:
        assert pipeline.summary()["status"] == "FROZEN"
        with pytest.raises(RuntimeError, match="snapshot is FROZEN"):
            pipeline.sync_config()

    assert database.read_bytes() == before
