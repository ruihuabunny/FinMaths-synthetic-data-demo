from __future__ import annotations

from pathlib import Path

import pytest

from synthetic_derivatives.model_families import (
    ExecutableCapabilityRegistry,
    ModelFamilyRegistry,
)
from synthetic_derivatives.task_space import (
    TaskCoordinates,
    TaskSpaceRegistry,
    TaskSpecV3,
)


def _registries(repository_root: Path):
    families = ModelFamilyRegistry.from_directory(
        repository_root / "configs/model_families"
    )
    catalog = TaskSpaceRegistry.from_path(
        repository_root / "configs/task_space/derivatives_v2.json"
    )
    capabilities = ExecutableCapabilityRegistry.from_path(
        repository_root / "configs/task_space/executable_capabilities_v1.json",
        model_families=families,
        design_catalog=catalog,
    )
    return catalog, capabilities


@pytest.mark.parametrize(
    ("model_family_id", "catalog_family_id", "coordinates", "method_id"),
    [
        (
            "heston",
            "heston_vanilla",
            TaskCoordinates(3, 0, 5, 3, 2, 4, "F0"),
            "heston-fourier-v1",
        ),
        (
            "local_vol",
            "local_vol_vanilla",
            TaskCoordinates(3, 0, 4, 5, 2, 4, "F0"),
            "local-vol-grid-v1",
        ),
    ],
)
def test_catalog_compatible_unimplemented_family_is_not_runtime_capability(
    repository_root: Path,
    model_family_id: str,
    catalog_family_id: str,
    coordinates: TaskCoordinates,
    method_id: str,
) -> None:
    catalog, capabilities = _registries(repository_root)
    assert catalog.registry_role == "design_catalog"
    assert catalog.require_compatible(coordinates, catalog_family_id).compatible
    candidate = TaskSpecV3(
        task_id=f"catalog-{model_family_id}-only",
        model_family_id=model_family_id,
        task_family_id="vanilla_pricing",
        task_kind_id="european_option",
        solver_interface_id="unimplemented-heston-interface-v1",
        coordinates=coordinates,
        snapshot_id="CATALOG-ONLY",
        snapshot_revision=1,
        method_id=method_id,
        output_contract_id="price-v1",
    )

    decision = capabilities.evaluate_task(candidate)

    assert not decision.admitted
    assert decision.code == "MODEL_FAMILY_UNAVAILABLE"
    assert "unknown or unimplemented" in decision.reason


def test_tdgbm_bsm_mc_scaffold_is_absent_from_portable_pool(
    repository_root: Path,
) -> None:
    catalog, capabilities = _registries(repository_root)
    coordinates = TaskCoordinates(3, 0, 0, 6, 2, 0, "F0")
    assert catalog.require_compatible(coordinates, "bsm_vanilla").compatible
    candidate = TaskSpecV3(
        task_id="catalog-mc-scaffold",
        model_family_id="tdgbm_bsm",
        task_family_id="monte_carlo",
        task_kind_id="european_price",
        solver_interface_id="mc-scaffold-v1",
        coordinates=coordinates,
        snapshot_id="CATALOG-ONLY",
        snapshot_revision=1,
        method_id="bsm-mc-scaffold-v1",
        output_contract_id="price-v1",
    )

    pool = capabilities.gate_task_pool((candidate,))

    assert pool.tasks == ()
    assert pool.decisions[0].decision.code == "CAPABILITY_NOT_REGISTERED"
