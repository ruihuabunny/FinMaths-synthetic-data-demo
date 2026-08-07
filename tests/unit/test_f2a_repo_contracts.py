from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_f2a_uses_existing_repo_boundaries(repository_root: Path) -> None:
    generator_path = repository_root / (
        "configs/generators/quantlib_bsm_metals_f2a_parent_v2.json"
    )
    authoring_path = repository_root / "authoring/configs/f2a_dataset_v1.json"
    assert not (repository_root / "configs/arbitrage").exists()
    assert generator_path.is_file()
    assert (
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    ).is_file()
    assert (repository_root / "configs/mutations/f2a_point_v1.json").is_file()
    assert authoring_path.is_file()

    generator = _read_json(generator_path)
    authoring = _read_json(authoring_path)
    assert authoring["parent_generator_config_id"] == generator[
        "generator_config_id"
    ]
    assert authoring["parent_snapshot_id"] == generator["snapshot_id"]
    assert authoring["parent_required_status"] == "FROZEN"
    assert authoring["artifacts"]["public_child_root"] == (
        "snapshots/generated/f2a/children"
    )


def test_f2a_execution_contract_is_not_zero_transaction_cost(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    )
    execution = variant["execution_contract"]

    assert execution["transaction_cost_model"] == (
        "directional_option_bid_ask_plus_option_per_contract_fee_plus_"
        "underlying_proportional_cost"
    )
    assert execution["option_execution"] == "directional_bid_ask"
    assert Decimal(execution["option_fee_per_contract_per_side"]) > 0
    assert execution["underlying_execution"] == (
        "single_price_plus_proportional_cost"
    )
    assert Decimal(execution["underlying_trading_cost_rate_per_side"]) == (
        Decimal("0.0005")
    )
    assert execution["underlying_trading_cost_basis"] == (
        "absolute_traded_notional"
    )
    assert execution["underlying_trading_cost_scope"] == (
        "every_trade_including_dynamic_rebalancing"
    )
    assert variant["market_contract"]["option_quote_fields"] == [
        "bid",
        "ask",
        "mid",
    ]


def test_f2a_versioned_contract_ids_are_consistent_across_boundaries(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    )
    lineage = _read_json(repository_root / "schemas/f2a-lineage.schema.json")
    submission = _read_json(repository_root / "schemas/submission.schema.json")

    assert lineage["properties"]["execution_contract_id"]["const"] == (
        variant["execution_contract"]["execution_contract_id"]
    )
    assert lineage["properties"]["candidate_catalogue_id"]["const"] == (
        variant["candidate_catalogue"]["candidate_catalogue_id"]
    )
    assert submission["properties"]["output_contract_id"]["const"] == (
        variant["output_contract"]["output_contract_id"]
    )


def test_f2a_solver_is_allowlist_only_and_denies_pricing_capabilities(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    )
    permissions = variant["solver_permissions"]

    assert permissions["dependency_policy"] == "allowlist_only"
    assert permissions["allowed_python_distributions"] == ["duckdb==1.5.5"]
    assert {
        "QuantLib",
        "py_vollib",
        "mibian",
        "rateslib",
        "financepy",
        "pyfeng",
    } <= set(permissions["denied_python_imports"])
    assert {
        "prebuilt_option_pricing",
        "prebuilt_implied_volatility",
        "prebuilt_option_greeks",
        "prebuilt_volatility_smile_or_surface",
        "prebuilt_static_or_calendar_arbitrage_scanner",
    } == set(permissions["denied_capabilities"])

    solver_lock = (
        repository_root / "environments/solver/requirements.lock"
    ).read_text(encoding="utf-8")
    assert "duckdb==1.5.5" in solver_lock
    assert not any(
        package.casefold() in solver_lock.casefold()
        for package in permissions["denied_python_imports"]
    )


def test_v2_registry_is_parallel_to_unchanged_v1(repository_root: Path) -> None:
    v1 = _read_json(repository_root / "configs/task_space/derivatives_v1.json")
    v2 = _read_json(repository_root / "configs/task_space/derivatives_v2.json")

    assert list(v1["axes"]) == ["L", "P", "M", "A", "D", "R"]
    assert list(v2["axes"]) == ["L", "P", "M", "A", "D", "R", "F"]
    f2a_rule = next(
        rule
        for rule in v2["compatibility_rules"]
        if rule["rule_id"] == "bsm-arbitrage-finding-f2a-v1"
    )
    assert f2a_rule["coordinates"] == {
        "L": [5],
        "P": [0],
        "M": [0],
        "A": [0],
        "D": [4],
        "R": [3],
        "F": ["F2A"],
    }
    ordinary_rules = [
        rule
        for rule in v2["compatibility_rules"]
        if rule["rule_id"] != "bsm-arbitrage-finding-f2a-v1"
    ]
    assert all(rule["coordinates"]["F"] == ["F0"] for rule in ordinary_rules)


def test_f2a_orm_schema_has_only_canonical_type_combinations(
    repository_root: Path,
) -> None:
    trajectory = _read_json(repository_root / "schemas/trajectory.schema.json")
    variants = trajectory["$defs"]["orm_answer"]["oneOf"]

    assert variants[0]["properties"]["arbitrage_opportunity"]["const"] is False
    assert variants[0]["properties"]["arbitrage_type"]["const"] == []
    assert variants[1]["properties"]["arbitrage_opportunity"]["const"] is True
    assert variants[1]["properties"]["arbitrage_type"]["enum"] == [
        ["cross-sectional"],
        ["cross-asset"],
        ["calendar"],
        ["cross-sectional", "cross-asset"],
        ["cross-sectional", "calendar"],
        ["cross-asset", "calendar"],
        ["cross-sectional", "cross-asset", "calendar"],
    ]

    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    )
    catalogue = variant["candidate_catalogue"]
    assert catalogue["canonical_type_order"] == [
        "cross-sectional",
        "cross-asset",
        "calendar",
    ]
    assert catalogue["cross_asset_families"] == [
        "discounted_price_bounds",
        "executable_put_call_parity",
    ]
    assert catalogue["cross_sectional_families"] == [
        "strike_monotonicity",
        "nonuniform_strike_convexity",
    ]
