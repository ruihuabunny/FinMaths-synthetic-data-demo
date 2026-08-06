from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


EXPIRIES = [30, 90, 180]
MONEYNESS = [0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2]


def _chain_config(
    source: Path, target: Path, *, business_days: int = 5
) -> Path:
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw.update(
        {
            "schema_version": "1.3.0",
            "generator_config_id": "option-chain-unit-v1",
            "generator_version": "0.4.0",
            "snapshot_id": "OPTION-CHAIN-UNIT-v1",
            "business_days": business_days,
        }
    )
    raw.pop("option_templates")
    raw["option_chain"] = {
        "chain_id": "STATIC-GRID-v1",
        "expiry_days": EXPIRIES,
        "moneyness_grid": MONEYNESS,
        "call_put": ["call", "put"],
        "listing_rule": "snapshot_start",
        "roll_rule": "static",
        "strike_increment": 0.5,
        "strike_rounding": "ROUND_HALF_EVEN",
        "exercise_style": "european",
        "settlement_type": "cash",
        "contract_multiplier": 100,
    }
    target.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def test_builder_expands_complete_paired_cartesian_grid(
    tmp_path: Path, repository_root: Path
) -> None:
    path = _chain_config(
        repository_root
        / "authoring/templates/quantlib_bsm_correlated_underlyings.template.json",
        tmp_path / "chain.json",
    )
    config = load_generator_config(path)

    assert config.schema_version == "1.3.0"
    assert config.option_chain is not None
    assert len(config.option_templates) == 3 * 7 * 2
    assert len({item.template_id for item in config.option_templates}) == 42
    for expiry_days in EXPIRIES:
        for moneyness in MONEYNESS:
            assert {
                item.call_put
                for item in config.option_templates
                if item.expiry_days == expiry_days
                and item.strike_moneyness == Decimal(str(moneyness))
            } == {"call", "put"}


def test_builder_accepts_mutually_exclusive_absolute_strike_grid(
    tmp_path: Path, repository_root: Path
) -> None:
    path = _chain_config(
        repository_root
        / "authoring/templates/quantlib_bsm_correlated_underlyings.template.json",
        tmp_path / "strike-chain.json",
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["option_chain"].pop("moneyness_grid")
    raw["option_chain"]["strike_grid"] = [80, 90, 95, 100, 105, 110, 120]
    path.write_text(json.dumps(raw), encoding="utf-8")

    config = load_generator_config(path)

    assert config.option_chain is not None
    assert config.option_chain.moneyness_grid is None
    assert config.option_chain.strike_grid == tuple(
        Decimal(str(value)) for value in [80, 90, 95, 100, 105, 110, 120]
    )
    assert all(item.strike_moneyness is None for item in config.option_templates)
    assert {item.strike_absolute for item in config.option_templates} == {
        Decimal(str(value)) for value in [80, 90, 95, 100, 105, 110, 120]
    }

    database = tmp_path / "strike-chain.duckdb"
    with AuthoringPipeline(database, config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        strikes_by_underlying = pipeline.connection.execute(
            """
            SELECT underlying_id, list(DISTINCT strike ORDER BY strike)
            FROM market.option_contracts GROUP BY underlying_id
            """
        ).fetchall()
    assert result["summary"]["option_contract_count"] == 2 * 3 * 7 * 2
    assert all(
        strikes
        == [Decimal(str(value)).quantize(Decimal("0.00000001"))
            for value in [80, 90, 95, 100, 105, 110, 120]]
        for _, strikes in strikes_by_underlying
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("expiry_days", [90, 30], "strictly increasing"),
        ("moneyness_grid", [0.9, 0.9], "strictly increasing"),
        ("call_put", ["call"], "paired call and put"),
        ("roll_rule", "monthly", "must be static"),
    ],
)
def test_chain_contract_rejects_ambiguous_first_version_rules(
    tmp_path: Path,
    repository_root: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    path = _chain_config(
        repository_root
        / "authoring/templates/quantlib_bsm_correlated_underlyings.template.json",
        tmp_path / "invalid-chain.json",
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["option_chain"][field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_generator_config(path)


def test_chain_persists_frozen_listing_strikes_and_is_append_invariant(
    tmp_path: Path, repository_root: Path
) -> None:
    path = _chain_config(
        repository_root
        / "authoring/templates/quantlib_bsm_correlated_underlyings.template.json",
        tmp_path / "chain.json",
    )
    database = tmp_path / "chain.duckdb"
    config = load_generator_config(path)
    with AuthoringPipeline(database, config) as pipeline:
        first = pipeline.create_smoke_snapshot()
        contracts_before = pipeline.connection.execute(
            """
            SELECT option_id, call_put, strike, expiry, chain_id, listing_date,
                   listing_spot, strike_moneyness
            FROM market.option_contracts ORDER BY option_id
            """
        ).fetchall()
        appended = pipeline.append_business_days(2)
        synced = pipeline.sync_config()
        contracts_after = pipeline.connection.execute(
            """
            SELECT option_id, call_put, strike, expiry, chain_id, listing_date,
                   listing_spot, strike_moneyness
            FROM market.option_contracts ORDER BY option_id
            """
        ).fetchall()

    assert first["summary"]["option_chain_spec_count"] == 1
    assert first["summary"]["option_contract_count"] == 2 * 3 * 7 * 2
    assert first["summary"]["option_daily_count"] == 2 * 3 * 7 * 2 * 5
    assert appended["table_stats"]["option_contracts"]["inserted"] == 0
    assert appended["table_stats"]["option_daily"]["inserted"] == 2 * 3 * 7 * 2 * 2
    assert synced["status"] == "NOOP"
    assert contracts_after == contracts_before

    connection = duckdb.connect(str(database), read_only=True)
    try:
        group_shapes = connection.execute(
            """
            SELECT underlying_id, expiry, count(DISTINCT strike),
                   count(DISTINCT call_put), count(*)
            FROM market.option_contracts
            GROUP BY underlying_id, expiry
            ORDER BY underlying_id, expiry
            """
        ).fetchall()
        assert all(row[2:] == (7, 2, 14) for row in group_shapes)
        assert connection.execute(
            """
            SELECT count(*) FROM information_schema.views
            WHERE table_schema = 'solver_visible'
              AND table_name = 'option_chain_specs'
            """
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_listed_chain_rules_cannot_change_within_snapshot(
    tmp_path: Path, repository_root: Path
) -> None:
    source = (
        repository_root
        / "authoring/templates/quantlib_bsm_correlated_underlyings.template.json"
    )
    path = _chain_config(source, tmp_path / "chain.json")
    database = tmp_path / "immutable.duckdb"
    with AuthoringPipeline(database, load_generator_config(path)) as pipeline:
        pipeline.create_smoke_snapshot()

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["option_chain"]["strike_increment"] = 1.0
    changed_path = tmp_path / "changed-chain.json"
    changed_path.write_text(json.dumps(raw), encoding="utf-8")
    with AuthoringPipeline(database, load_generator_config(changed_path)) as pipeline:
        with pytest.raises(ValueError, match="option_chain cannot change"):
            pipeline.sync_config()


def test_checked_in_metals_smoke_has_expected_underlying_only_breadth(
    repository_root: Path,
) -> None:
    config = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json"
    )

    assert len(config.underlyings) == 20
    assert len(config.option_templates) == 3 * 7 * 2
    assert config.underlying_simulation is not None
    assert len(config.underlying_simulation.driver_order) == 20
    assert set(config.underlying_simulation.driver_order) == {
        underlying.underlying_id for underlying in config.underlyings
    }
    assert not any(
        option.template_id in config.underlying_simulation.driver_order
        for option in config.option_templates
    )
