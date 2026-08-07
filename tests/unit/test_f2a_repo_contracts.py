from __future__ import annotations

import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

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
    assert (
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v2.json"
    ).is_file()
    assert (repository_root / "configs/mutations/f2a_point_v2.json").is_file()
    assert (repository_root / "authoring/configs/f2a_dataset_v2.json").is_file()

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


def test_f2a_successor_contracts_use_new_ids_and_remain_blocked(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v2.json"
    )
    mutation = _read_json(repository_root / "configs/mutations/f2a_point_v2.json")
    dataset = _read_json(repository_root / "authoring/configs/f2a_dataset_v2.json")
    lineage = _read_json(repository_root / "schemas/f2a-lineage-v2.schema.json")
    submission = _read_json(repository_root / "schemas/submission-v2.schema.json")

    assert variant["variant_id"] == "bsm-arbitrage-finding-f2a-v2"
    assert variant["status"] == "BLOCKED"
    assert variant["runtime_enabled"] is False
    catalogue = variant["candidate_catalogue"]
    assert catalogue["candidate_catalogue_id"] == (
        "bsm-f2a-candidate-catalogue-v3"
    )
    assert catalogue["calendar_family"] is None
    assert catalogue["runtime_enabled"] is False
    assert "must_not_modify" in catalogue["calendar_activation_identity_policy"]

    assert mutation["engine_id"] == "f2a-point-mutation-v2"
    assert dataset["variant_id"] == variant["variant_id"]
    assert dataset["candidate_catalogue_id"] == catalogue["candidate_catalogue_id"]
    assert dataset["mutation_engine_id"] == mutation["engine_id"]
    assert lineage["properties"]["engine_id"]["const"] == mutation["engine_id"]
    assert lineage["properties"]["candidate_catalogue_id"]["const"] == (
        catalogue["candidate_catalogue_id"]
    )
    assert submission["properties"]["variant_id"]["const"] == (
        variant["variant_id"]
    )
    assert submission["properties"]["output_contract_id"]["const"] == (
        variant["output_contract"]["output_contract_id"]
    )


def test_f2a_legacy_contracts_are_not_silently_migrated(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    )
    mutation = _read_json(repository_root / "configs/mutations/f2a_point_v1.json")
    dataset = _read_json(repository_root / "authoring/configs/f2a_dataset_v1.json")
    lineage = _read_json(repository_root / "schemas/f2a-lineage.schema.json")

    assert variant["candidate_catalogue"]["candidate_catalogue_id"] == (
        "bsm-f2a-candidate-catalogue-v2"
    )
    assert variant["candidate_catalogue"]["calendar_family"] is None
    assert variant["candidate_catalogue"]["decision_rule"] == (
        "candidate_spread_strictly_greater_than_zero"
    )
    assert mutation["engine_id"] == "f2a-point-mutation-v1"
    assert dataset["dataset_config_id"] == "f2a-dataset-v1"
    assert lineage["properties"]["candidate_catalogue_id"]["const"] == (
        "bsm-f2a-candidate-catalogue-v2"
    )


def test_f2a_successor_mutation_contract_uses_exact_public_fields_and_gates(
    repository_root: Path,
) -> None:
    mutation = _read_json(repository_root / "configs/mutations/f2a_point_v2.json")
    operators = {item["operator_id"]: item for item in mutation["operators"]}
    spot = operators["mutate_underlying_spot_point_v1"]

    assert spot["logical_field"] == "spot_close"
    assert spot["unchanged_fields"] == ["all_option_quotes"]
    assert "child_option_price_in_model_domain" not in mutation[
        "authoring_domain_gates"
    ]
    assert set(mutation["forbidden_domain_gates"]) == {
        "bsm_price_bounds",
        "put_call_parity",
        "strike_monotonicity",
        "strike_convexity",
        "calendar_price_ordering",
        "model_repricing_or_clipping",
    }


def test_f2a_successor_selector_requires_clean_spot_baseline_and_audit(
    repository_root: Path,
) -> None:
    dataset = _read_json(repository_root / "authoring/configs/f2a_dataset_v2.json")

    assert dataset["selection"]["clean_slice_policy"] == (
        "require_realized_signature_000_before_any_mutation"
    )
    assert dataset["selection"]["spot_mutation_invariant"] == (
        "X_after_equals_X_before"
    )
    assert dataset["reachability_audit"]["status"] == "NOT_RUN"
    assert dataset["selector"]["unreachable_policy"] == "deterministic_skip"
    assert dataset["selector"]["guard_role"] == (
        "sample_selection_only_not_canonical_verifier_predicate"
    )


def test_f2a_successor_public_contract_contains_support_clock_and_timeline(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v2.json"
    )
    pricing = variant["pricing_contract"]

    assert pricing["measure_equivalence"] == (
        "P_equivalent_to_Q_on_each_candidate_horizon"
    )
    assert pricing["q_volatility_time_origin"] == "2026-08-03T16:00:00Z"
    assert pricing["q_volatility_node_offset_clock"] == (
        "calendar_days_from_time_origin"
    )
    assert pricing["borrow_or_carry_is_not_dividend_cashflow"] is True
    assert variant["settlement_timeline"]["same_timestamp_operation_order_is_normative"] is True


def test_f2a_successor_public_contract_does_not_leak_private_selector_state(
    repository_root: Path,
) -> None:
    variant_text = (
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v2.json"
    ).read_text(encoding="utf-8")
    submission_text = (
        repository_root / "schemas/submission-v2.schema.json"
    ).read_text(encoding="utf-8")

    forbidden = (
        "requested_signature",
        "realized_signature",
        "selector_trace",
        "mutation_count",
        "before_value",
        "after_value",
        "private_margin",
    )
    assert all(token not in variant_text for token in forbidden)
    assert all(token not in submission_text for token in forbidden)


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


def test_f2a_markdown_has_no_adjacent_duplicate_headings_or_broken_local_links(
    repository_root: Path,
) -> None:
    markdown_paths = [
        repository_root / "AGENTS.md",
        repository_root / "README.md",
        repository_root / "docs/authoring_pipeline.md",
        repository_root
        / "docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md",
        repository_root / "environments/solver/README.md",
        repository_root
        / "snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md",
        repository_root / "src/synthetic_derivatives/authoring/README.md",
        repository_root
        / "src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md",
        repository_root
        / "src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md",
        repository_root / "tests/unit/README.md",
    ]
    link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")

    for path in markdown_paths:
        text = path.read_text(encoding="utf-8")
        headings = [line for line in text.splitlines() if line.startswith("#")]
        assert all(left != right for left, right in zip(headings, headings[1:])), path
        for target in link_pattern.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            local_target = target.split("#", 1)[0]
            assert (path.parent / local_target).resolve().exists(), (path, target)


def test_f2a_markdown_uses_candidate_specific_predicate_and_family_order(
    repository_root: Path,
) -> None:
    master = (
        repository_root
        / "docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md"
    ).read_text(encoding="utf-8")
    solver = (repository_root / "environments/solver/README.md").read_text(
        encoding="utf-8"
    )
    agents = (repository_root / "AGENTS.md").read_text(encoding="utf-8")

    assert "candidate_spread > 0" not in master
    assert "candidate_spread > 0" not in solver
    assert "(calendar, cross_sectional, cross_asset)" not in agents
    assert "(cross-sectional, cross-asset, calendar)" in agents


def test_f2a_complete_grammar_uses_fresh_parallel_identities(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v3.json"
    )
    mutation = _read_json(
        repository_root / "configs/mutations/f2a_complete_v3.json"
    )
    dataset = _read_json(repository_root / "authoring/configs/f2a_dataset_v3.json")
    lineage = _read_json(repository_root / "schemas/f2a-lineage-v3.schema.json")
    submission = _read_json(repository_root / "schemas/submission-v3.schema.json")

    assert variant["variant_id"] == "bsm-arbitrage-finding-f2a-v3"
    assert variant["runtime_enabled"] is False
    assert variant["candidate_catalogue"]["calendar_family"] is None
    assert variant["candidate_catalogue"]["candidate_catalogue_id"] == (
        "bsm-f2a-candidate-catalogue-v4"
    )
    assert mutation["engine_id"] == "f2a-complete-mutation-v3"
    assert dataset["variant_id"] == variant["variant_id"]
    assert dataset["candidate_catalogue_id"] == (
        variant["candidate_catalogue"]["candidate_catalogue_id"]
    )
    assert dataset["mutation_engine_id"] == mutation["engine_id"]
    assert lineage["properties"]["variant_id"]["const"] == variant["variant_id"]
    assert lineage["properties"]["engine_id"]["const"] == mutation["engine_id"]
    assert submission["properties"]["variant_id"]["const"] == (
        variant["variant_id"]
    )
    assert submission["properties"]["output_contract_id"]["const"] == (
        variant["output_contract"]["output_contract_id"]
    )

    # Earlier identities remain parallel replay/review records.
    assert _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v1.json"
    )["variant_id"] == "bsm-arbitrage-finding-f2a-v1"
    assert _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v2.json"
    )["variant_id"] == "bsm-arbitrage-finding-f2a-v2"


def test_f2a_complete_grammar_discloses_grouped_physical_point_count(
    repository_root: Path,
) -> None:
    mutation = _read_json(
        repository_root / "configs/mutations/f2a_complete_v3.json"
    )
    operators = {item["operator_id"]: item for item in mutation["operators"]}
    pair = operators["mutate_call_put_pair_equal_shift_v1"]

    assert pair["logical_mutation_groups"] == 1
    assert pair["logical_quote_points_changed"] == 2
    assert pair["physical_quote_points_changed"] == 2
    assert pair["group_semantics"] == (
        "same-strike-same-expiry-call-put-equal-shift"
    )
    assert mutation["difficulty_count_contract"][
        "max_physical_quote_points_changed"
    ] == 2


def test_f2a_v3_distribution_reachability_and_single_parent_split_are_explicit(
    repository_root: Path,
) -> None:
    dataset = _read_json(repository_root / "authoring/configs/f2a_dataset_v3.json")
    signatures = ["000", "100", "010", "001", "110", "101", "011", "111"]

    assert dataset["requested_signature_order"] == signatures
    assert list(dataset["requested_signature_distribution"]["target_mass"]) == (
        signatures
    )
    assert sum(
        dataset["requested_signature_distribution"]["target_mass"].values()
    ) == 1.0
    assert dataset["operator_policy_by_signature"]["000"] == ["clean_control_v1"]
    assert dataset["profiles"]["smoke"]["requested_count_by_signature"] == {
        signature: 1 for signature in signatures
    }
    assert dataset["profiles"]["pilot"]["requested_count_by_signature"] == {
        signature: 16 for signature in signatures
    }
    assert dataset["profiles"]["smoke"]["publication_task_count"] == 0
    assert dataset["profiles"]["pilot"]["publication_task_count"] == 0

    audit = dataset["reachability_audit"]
    assert audit["status"] == "BLOCKED_NOT_RUN"
    assert list(audit["results"]) == signatures
    assert all(result["reachable"] is None for result in audit["results"].values())
    assert all(not result["integer_tick_windows"] for result in audit["results"].values())
    for signature in ("001", "101", "011", "111"):
        assert audit["results"][signature]["diagnostic"] == (
            "BLOCKED_CALENDAR_FAMILY_NULL"
        )

    split = dataset["split"]
    assert split["current_names"] == ["audit"]
    assert split["usable_train_validation_test_split"] is False
    assert dataset["artifacts"]["split_manifest"] == (
        "datasets/manifests/splits/f2a_v3.json"
    )


def test_f2a_v3_calendar_contract_checks_g_not_total_wealth(
    repository_root: Path,
) -> None:
    variant = _read_json(
        repository_root
        / "configs/variants/bsm_arbitrage_finding_f2a_v3.json"
    )
    calendar = variant["candidate_catalogue"]["calendar_target_specification"]

    assert calendar["canonical_certificate_target"] == (
        "prove_g_j_greater_than_or_equal_to_zero_on_(0,infinity)^2"
    )
    assert "W_T2" in calendar["total_terminal_wealth_display_only"]
    assert "does_not_establish" in calendar["forbidden_substitute"]
    assert all("g_j" in step for step in calendar["certificate_order"][1:])


_SIGNATURE_FAMILIES = {
    "000": (),
    "100": ("cross-sectional",),
    "010": ("cross-asset",),
    "001": ("calendar",),
    "110": ("cross-sectional", "cross-asset"),
    "101": ("cross-sectional", "calendar"),
    "011": ("cross-asset", "calendar"),
    "111": ("cross-sectional", "cross-asset", "calendar"),
}


def _guard() -> dict:
    return {
        "candidate_ids": ["candidate-1"],
        "setup_boundary": {
            "kind": "closed",
            "distance": 1.0,
            "unit": "USD_per_candidate_strategy",
        },
        "terminal_certificate": {
            "finite_vertex_slack": None,
            "actual_boundary_slack": None,
            "one_sided_limit_slack": None,
            "recession_ray_slope_slack": None,
            "strict_gain": True,
        },
    }


def _quote_mutation(call_put: str, option_id: str) -> dict:
    return {
        "table": "solver_visible.option_daily",
        "row_key": {
            "valuation_date": "2026-08-03",
            "underlying_id": "XAU",
            "option_id": option_id,
            "expiry": "2026-09-03",
            "strike": 100.0,
            "call_put": call_put,
            "contract_multiplier": 100.0,
        },
        "field": "mid",
        "before": 5.0,
        "after": 6.0,
        "derived_fields": [
            {"field": "bid", "before": 4.9, "after": 5.9},
            {"field": "ask", "before": 5.1, "after": 6.1},
        ],
    }


def _lineage_record(signature: str) -> dict:
    active = set(_SIGNATURE_FAMILIES[signature])
    all_families = {"cross-sectional", "cross-asset", "calendar"}
    if signature == "000":
        operator = "clean_control_v1"
        mutation = None
        chi = 0
    elif signature in {"100", "001", "101"}:
        operator = "mutate_call_put_pair_equal_shift_v1"
        mutation = {
            "kind": "call_put_pair_equal_shift",
            "delta_ticks": 100,
            "tick_unit": "option_ticks",
            "logical_mutation_groups": 1,
            "logical_quote_points_changed": 2,
            "physical_quote_points_changed": 2,
            "physical_spot_points_changed": 0,
            "group_semantics": "same-strike-same-expiry-call-put-equal-shift",
            "quote_mutations": [
                _quote_mutation("call", "call-1"),
                _quote_mutation("put", "put-1"),
            ],
        }
        chi = 1
    elif signature == "011":
        operator = "mutate_underlying_spot_point_v2"
        mutation = {
            "kind": "underlying_spot_shift",
            "delta_ticks": -100,
            "tick_unit": "underlying_ticks",
            "logical_mutation_groups": 1,
            "logical_quote_points_changed": 0,
            "physical_quote_points_changed": 0,
            "physical_spot_points_changed": 1,
            "spot_mutation": {
                "table": "solver_visible.underlying_daily",
                "row_key": {
                    "valuation_date": "2026-08-03",
                    "underlying_id": "XAU",
                },
                "field": "spot_close",
                "before": 100.0,
                "after": 99.0,
            },
        }
        chi = 1
    else:
        operator = "mutate_option_price_point_v2"
        mutation = {
            "kind": "single_option_quote_shift",
            "delta_ticks": 100,
            "tick_unit": "option_ticks",
            "logical_mutation_groups": 1,
            "logical_quote_points_changed": 1,
            "physical_quote_points_changed": 1,
            "physical_spot_points_changed": 0,
            "quote_mutations": [_quote_mutation("call", "call-1")],
        }
        chi = 1

    return {
        "parent_task_id": "parent-task",
        "child_task_id": "child-task",
        "parent_snapshot_id": "DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2",
        "parent_snapshot_revision": 1,
        "child_snapshot_id": "child-snapshot",
        "child_snapshot_revision": 1,
        "variant_id": "bsm-arbitrage-finding-f2a-v3",
        "output_contract_id": "arbitrage-opportunity-type-trajectory-v4",
        "stable_selector": {"index": 1},
        "operator": operator,
        "seed": 1,
        "engine_id": "f2a-complete-mutation-v3",
        "mutation": mutation,
        "realized_chi_F": chi,
        "source_pricing_model_ids": ["bsm"],
        "execution_contract_id": "us-options-underlying-5bps-options-flat-050-v3",
        "candidate_catalogue_id": "bsm-f2a-candidate-catalogue-v4",
        "requested_signature": signature,
        "realized_signature": signature,
        "reachability_audit_id": "f2a-complete-grammar-reachability-v1",
        "oracle_result": {
            "arbitrage_opportunity": signature != "000",
            "arbitrage_type": list(_SIGNATURE_FAMILIES[signature]),
        },
        "authoring_guard_evidence": {
            "execution_profile_id": "us-options-underlying-5bps-options-flat-050-v3",
            "candidate_evaluator_id": "candidate-specific-exact-cashflow-v2",
            "active_family_guards": {family: _guard() for family in active},
            "inactive_family_guards": {
                family: _guard() for family in all_families - active
            },
            "spot_cross_sectional_signature_before": (
                False if operator == "mutate_underlying_spot_point_v2" else None
            ),
            "spot_cross_sectional_signature_after": (
                False if operator == "mutate_underlying_spot_point_v2" else None
            ),
            "selector_trace": [{"tick": 100}],
        },
    }


def _lineage_validator(repository_root: Path) -> Draft202012Validator:
    schema = _read_json(repository_root / "schemas/f2a-lineage-v3.schema.json")
    trajectory = _read_json(repository_root / "schemas/trajectory.schema.json")
    registry = Registry().with_resource(
        "trajectory.schema.json",
        Resource.from_contents(trajectory),
    )
    return Draft202012Validator(schema, registry=registry)


@pytest.mark.parametrize("signature", list(_SIGNATURE_FAMILIES))
def test_f2a_lineage_v3_accepts_each_signature_including_empty_guard_end(
    repository_root: Path,
    signature: str,
) -> None:
    _lineage_validator(repository_root).validate(_lineage_record(signature))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(
            {"mutation": _lineage_record("010")["mutation"]}
        ),
        lambda record: record.update({"requested_signature": "100"}),
        lambda record: record["authoring_guard_evidence"][
            "active_family_guards"
        ].update({"cross-asset": _guard()}),
    ],
)
def test_f2a_lineage_v3_rejects_invalid_clean_record(
    repository_root: Path,
    mutate,
) -> None:
    record = _lineage_record("000")
    mutate(record)
    assert list(_lineage_validator(repository_root).iter_errors(record))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update({"mutation": None}),
        lambda record: record.update({"realized_chi_F": 0}),
        lambda record: record.update({"realized_signature": "110"}),
        lambda record: record["mutation"].update({"tick_unit": "underlying_ticks"}),
        lambda record: record["mutation"]["quote_mutations"][0].update(
            {"field": "spot_close"}
        ),
    ],
)
def test_f2a_lineage_v3_rejects_operator_mutation_chi_or_signature_mismatch(
    repository_root: Path,
    mutate,
) -> None:
    record = _lineage_record("010")
    mutate(record)
    assert list(_lineage_validator(repository_root).iter_errors(record))


def test_f2a_lineage_v3_rejects_grouped_pair_missing_put_leg(
    repository_root: Path,
) -> None:
    record = _lineage_record("100")
    record["mutation"]["quote_mutations"] = [
        record["mutation"]["quote_mutations"][0]
    ]
    assert list(_lineage_validator(repository_root).iter_errors(record))


def test_f2a_lineage_v3_rejects_grouped_pair_missing_before_value(
    repository_root: Path,
) -> None:
    record = _lineage_record("100")
    del record["mutation"]["quote_mutations"][1]["before"]
    assert list(_lineage_validator(repository_root).iter_errors(record))


def test_f2a_submission_v3_rejects_private_selector_leak(
    repository_root: Path,
) -> None:
    schema = _read_json(repository_root / "schemas/submission-v3.schema.json")
    trajectory = _read_json(repository_root / "schemas/trajectory.schema.json")
    registry = Registry().with_resource(
        "trajectory.schema.json",
        Resource.from_contents(trajectory),
    )
    record = {
        "task_id": "task",
        "snapshot_id": "child",
        "snapshot_revision": 1,
        "variant_id": "bsm-arbitrage-finding-f2a-v3",
        "output_contract_id": "arbitrage-opportunity-type-trajectory-v4",
        "trajectory": {
            "Problem": "p",
            "Context": "c",
            "Assumptions": "a",
            "Skills": "s",
            "Evidence": "e",
            "Intermediate Reasoning": "i",
            "Verification": "v",
            "Confidence": 1.0,
            "Outcome": {
                "orm_answer": {
                    "arbitrage_opportunity": False,
                    "arbitrage_type": [],
                }
            },
        },
    }
    validator = Draft202012Validator(schema, registry=registry)
    validator.validate(record)
    leaked = deepcopy(record)
    leaked["requested_signature"] = "000"
    assert list(validator.iter_errors(leaked))


def test_f2a_successor_docs_do_not_route_v3_to_legacy_artifacts(
    repository_root: Path,
) -> None:
    plan = (
        repository_root
        / "src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md"
    ).read_text(encoding="utf-8")
    readme = (repository_root / "README.md").read_text(encoding="utf-8")

    assert "完整 grammar review 读取\n`configs/variants/bsm_arbitrage_finding_f2a_v3.json`" in plan
    assert "datasets/manifests/splits/f2a_v1.json  # legacy only" in plan
    assert "datasets/manifests/splits/f2a_v3.json  # blocked v3" in plan
    assert "datasets/manifests/splits/f2a_v1.json  # legacy only" in readme
    assert "一个 logical group、两个" in readme


def test_first_f2a_handoff_is_historical_and_not_at_repo_root(
    repository_root: Path,
) -> None:
    assert not (repository_root / "f2a_markdown_math_rework_handoff.md").exists()
    historical = repository_root / (
        "docs/audits/2026-08-07_f2a_first_math_audit.md"
    )
    assert historical.is_file()
    assert historical.read_text(encoding="utf-8").startswith(
        "# HISTORICAL / SUPERSEDED"
    )
