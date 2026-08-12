from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from synthetic_derivatives.task_space import (
    AXES,
    F_LEVELS,
    TaskCoordinates,
    TaskSpaceRegistry,
    TaskSpec,
    migrate_legacy_six_axis_task,
)


def test_checked_in_base_task_is_compatible(
    task_space_config_path: Path, base_task_manifest_path: Path
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)
    task = TaskSpec.from_mapping(
        json.loads(base_task_manifest_path.read_text(encoding="utf-8"))
    )

    decision = registry.validate_task(task)

    assert decision.compatible
    assert decision.rule_id == "bsm-vanilla-f0"
    assert task.coordinates.to_dict() == {
        "L": 0,
        "P": 0,
        "M": 0,
        "A": 0,
        "D": 0,
        "R": 0,
        "F": "F0",
    }
    assert task.coordinates.canonical_id == "L0-P0-M0-A0-D0-R0-F0"
    assert task.snapshot_revision == 1


def test_registry_rejects_incompatible_product_model_method(
    task_space_config_path: Path,
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)

    decision = registry.evaluate(
        TaskCoordinates(L=0, P=6, M=0, A=1, D=0, R=0, F="F0"),
        "barrier",
    )

    assert not decision.compatible
    assert "no compatible" in decision.reason


def test_registry_rejects_coordinate_outside_documented_axis(
    task_space_config_path: Path,
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)

    with pytest.raises(ValueError, match="outside registry bounds"):
        registry.require_compatible(
            TaskCoordinates(L=7, P=0, M=0, A=0, D=0, R=0, F="F0")
        )


def test_runtime_axes_and_f_enum_exactly_match_the_v2_schema(
    repository_root: Path,
) -> None:
    difficulty = json.loads(
        (repository_root / "schemas/difficulty-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    task_schema = json.loads(
        (repository_root / "schemas/task-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    mutation_schema = json.loads(
        (repository_root / "schemas/mutation-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    curriculum_schema = json.loads(
        (repository_root / "schemas/curriculum-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    registry_config = json.loads(
        (repository_root / "configs/task_space/derivatives_v2.json").read_text(
            encoding="utf-8"
        )
    )
    curriculum_config = json.loads(
        (repository_root / "configs/curricula/adaptive_v2.json").read_text(
            encoding="utf-8"
        )
    )

    assert AXES == ("L", "P", "M", "A", "D", "R", "F")
    assert difficulty["required"] == list(AXES)
    assert tuple(difficulty["properties"]["F"]["enum"]) == F_LEVELS
    assert tuple(registry_config["axes"]) == AXES
    assert tuple(registry_config["axes"]["F"]["values"]) == F_LEVELS
    for axis in AXES[:-1]:
        assert registry_config["axes"][axis]["min"] == (
            difficulty["properties"][axis]["minimum"]
        )
        assert registry_config["axes"][axis]["max"] == (
            difficulty["properties"][axis]["maximum"]
        )
    assert task_schema["properties"]["coordinates"]["$ref"] == (
        "difficulty-v2.schema.json"
    )
    lineage = mutation_schema["properties"]["lineage"]["properties"]
    assert lineage["before"]["$ref"] == "difficulty-v2.schema.json"
    assert lineage["after"]["$ref"] == "difficulty-v2.schema.json"
    assert curriculum_schema["properties"]["curriculum_id"]["const"] == (
        curriculum_config["curriculum_id"]
    )
    assert all(
        stage["coordinates"]["F"] == ["F0"]
        for stage in curriculum_config["stages"]
    )


@pytest.mark.parametrize("invalid_f", ["F7", "F2", 0, None])
def test_coordinates_reject_unknown_or_non_string_f_levels(
    invalid_f: object,
) -> None:
    with pytest.raises(ValueError, match="F must be one of"):
        TaskCoordinates(L=0, P=0, M=0, A=0, D=0, R=0, F=invalid_f)


def test_seven_axis_parser_never_silently_defaults_missing_f() -> None:
    legacy = {"L": 0, "P": 0, "M": 0, "A": 0, "D": 0, "R": 0}

    with pytest.raises(ValueError, match=r"missing=\['F'\]"):
        TaskCoordinates.from_mapping(legacy)


def test_explicit_legacy_adapter_adds_only_f0_and_preserves_task_id(
    repository_root: Path,
) -> None:
    legacy = json.loads(
        (
            repository_root
            / "datasets/manifests/tasks/quantlib_bsm_smoke_price_v1.json"
        ).read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match=r"missing=\['F'\]"):
        TaskSpec.from_mapping(legacy)

    first = migrate_legacy_six_axis_task(legacy)
    second = migrate_legacy_six_axis_task(legacy)

    assert first == second
    assert first.task_id == legacy["task_id"]
    assert first.coordinates.F == "F0"
    assert first.to_dict()["coordinates"] == {
        **legacy["coordinates"],
        "F": "F0",
    }
    with pytest.raises(ValueError, match=r"extra=\['F'\]"):
        migrate_legacy_six_axis_task(first.to_dict())


def test_registered_bsm_variants_use_f0_and_their_actual_data_interfaces(
    repository_root: Path,
    task_space_config_path: Path,
) -> None:
    registry = TaskSpaceRegistry.from_path(task_space_config_path)
    variants = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (
            repository_root / "configs/variants/bsm_analytic_greeks_v1.json",
            repository_root / "configs/variants/bsm_iv_scalar_v1.json",
            repository_root
            / "configs/variants/bsm_market_implied_greeks_v1.json",
        )
    }
    analytic = TaskCoordinates.from_mapping(
        variants["bsm_analytic_greeks_v1"]["coordinates"]
    )
    scalar_iv = TaskCoordinates.from_mapping(
        variants["bsm_iv_scalar_v1"]["coordinates"]
    )
    market = TaskCoordinates.from_mapping(
        variants["bsm_market_implied_greeks_v1"]["coordinates"]
    )

    assert (analytic.D, analytic.F) == (1, "F0")
    assert (scalar_iv.D, scalar_iv.F) == (1, "F0")
    assert (market.D, market.F) == (4, "F0")
    assert registry.require_compatible(
        analytic, "bsm_greeks"
    ).rule_id == "bsm-analytic-greeks-f0"
    assert registry.require_compatible(
        market, "bsm_greeks"
    ).rule_id == "bsm-market-implied-greeks-f0"
    assert registry.require_compatible(
        scalar_iv, "bsm_vanilla"
    ).rule_id == "bsm-vanilla-f0"
    assert variants["bsm_market_implied_greeks_v1"]["input_interface"] == (
        "trusted_query_adapter_over_public_duckdb_market_snapshot"
    )
    assert variants["bsm_market_implied_greeks_v1"]["status"] == (
        "ACCEPTED_GOLDEN_PACKAGE"
    )

    incompatible = registry.evaluate(replace(market, F="F1"), "bsm_greeks")
    assert not incompatible.compatible


def test_current_f0_task_contracts_do_not_add_arbitrage_business_outputs(
    repository_root: Path,
    base_task_manifest_path: Path,
) -> None:
    payloads = [
        json.loads(base_task_manifest_path.read_text(encoding="utf-8")),
        *(
            json.loads(path.read_text(encoding="utf-8"))
            for path in (
                repository_root / "configs/variants/bsm_analytic_greeks_v1.json",
                repository_root / "configs/variants/bsm_iv_scalar_v1.json",
                repository_root
                / "configs/variants/bsm_market_implied_greeks_v1.json",
            )
        ),
    ]
    forbidden = {
        "arbitrage_opportunity",
        "arbitrage_type",
        "maximal_spread",
        "x_signal",
        "u_signal",
        "t_signal",
        "transaction_cost_certificate",
        "false_positive_target",
        "false_negative_target",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert all(keys(payload).isdisjoint(forbidden) for payload in payloads)
