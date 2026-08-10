from __future__ import annotations

from datetime import date
import json

import pytest

from synthetic_derivatives.export import (
    SolverDatabaseExportContract,
    sample_underlyings,
    stable_sample_id,
    stable_task_id,
)
from synthetic_derivatives.export.contracts import assert_no_private_leakage


def test_sha256_rank_sampler_is_order_invariant_and_without_replacement() -> None:
    universe = tuple(f"SYNTH-U{index:02d}" for index in range(22))

    first = sample_underlyings(universe, sample_size=8, seed=17)
    replay = sample_underlyings(tuple(reversed(universe)), sample_size=8, seed=17)
    different = sample_underlyings(universe, sample_size=8, seed=18)

    assert first == replay == tuple(sorted(first))
    assert len(first) == len(set(first)) == 8
    assert different != first


@pytest.mark.parametrize(
    ("universe", "sample_size", "seed", "message"),
    [
        (("A", "A"), 1, 0, "unique"),
        (("A",), 2, 0, "within"),
        (("A",), 0, 0, "within"),
        (("A",), 1, -1, "nonnegative"),
        (("A",), 1, True, "nonnegative"),
    ],
)
def test_sampler_rejects_ambiguous_selection_contracts(
    universe: tuple[str, ...], sample_size: int, seed: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        sample_underlyings(universe, sample_size=sample_size, seed=seed)


def test_stable_sample_and_task_ids_cover_complete_selection() -> None:
    common = {
        "parent_logical_checksum": "a" * 64,
        "seed": 17,
        "underlying_ids": ("A", "B"),
        "variant_id": "bsm_market_implied_greeks_v1",
    }
    sample_id = stable_sample_id(**common)

    assert sample_id == stable_sample_id(
        **(common | {"underlying_ids": ("B", "A")})
    )
    assert sample_id != stable_sample_id(**(common | {"seed": 18}))
    task_common = {
        "sample_id": sample_id,
        "parent_snapshot_id": "PARENT-v1",
        "parent_snapshot_revision": 1,
        "variant_id": common["variant_id"],
        "valuation_date": date(2026, 8, 3),
        "option_ids": ("O2", "O1"),
    }
    task_id = stable_task_id(**task_common)

    assert task_id == stable_task_id(
        **(task_common | {"option_ids": ("O1", "O2")})
    )
    assert task_id != stable_task_id(
        **(task_common | {"valuation_date": date(2026, 8, 4)})
    )
    assert task_id != stable_task_id(
        **(task_common | {"option_ids": ("O1",)})
    )


def test_export_contract_canonicalizes_explicit_option_ids() -> None:
    contract = SolverDatabaseExportContract(
        variant_id="bsm_market_implied_greeks_v1",
        valuation_date=date(2026, 8, 3),
        sampling_seed=4,
        option_ids=("O2", "O1"),
    )

    assert contract.option_ids == ("O1", "O2")
    with pytest.raises(ValueError, match="unique"):
        SolverDatabaseExportContract(
            variant_id="bsm_market_implied_greeks_v1",
            valuation_date=date(2026, 8, 3),
            sampling_seed=4,
            option_ids=("O1", "O1"),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"outer": {"seed": 7}},
        {"outer": json.dumps({"deeper": {"generatedRunId": "private"}})},
        {"outer": [{"node": {"value": 0.2}}]},
        {"outer": {"answer": {"d1": 1.25}}},
    ],
)
def test_leakage_scan_recurses_through_objects_lists_and_encoded_json(
    payload: object,
) -> None:
    with pytest.raises(ValueError, match="private field leaked"):
        assert_no_private_leakage(payload)


def test_leakage_scan_accepts_public_curve_and_dependence_json() -> None:
    assert_no_private_leakage(
        {
            "discount_curve": {"type": "flat_continuous", "rate": 0.03},
            "dependence": {
                "measure": "Q",
                "driver_order": ["A", "B"],
                "correlation_matrix": [[1.0, 0.4], [0.4, 1.0]],
            },
        }
    )
