from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    bsm_market_greeks_solver_interface_digest,
    market_greeks_method_contract,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import stable_package_task_id
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.prompt_renderer import (
    render_bsm_greeks_prompt,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
    audit_solver_source,
    compose_runtime_contract,
)


def _profiles(repository_root: Path) -> tuple[dict, dict]:
    global_profile = json.loads(
        (
            repository_root
            / "environments/solver/capabilities.global_v1.json"
        ).read_text(encoding="utf-8")
    )
    overlay = json.loads(
        (
            repository_root
            / "environments/solver/capabilities.bsm_greeks_v1.json"
        ).read_text(encoding="utf-8")
    )
    return global_profile, overlay


def test_effective_contract_is_a_strict_intersection_and_renders_prompt(
    repository_root: Path,
) -> None:
    global_profile, overlay = _profiles(repository_root)
    runtime = compose_runtime_contract(global_profile, overlay)
    method_contract = market_greeks_method_contract()
    prompt = render_bsm_greeks_prompt(method_contract, runtime)
    prompt_casefold = prompt.casefold()

    assert runtime["network"] is False
    assert runtime["dynamic_installation"] is False
    assert runtime["process_spawning"] is False
    assert runtime["resource_budget"]["trusted_query_calls"] == 2
    assert [tool["max_calls"] for tool in runtime["trusted_tools"]] == [1, 1, 1]
    assert "every public input row" in prompt_casefold
    assert "complete authoritative specification" in prompt_casefold
    assert "query_greeks_task_contract_v1" in prompt
    assert "query_greeks_task_inputs_v1" in prompt
    assert "public/submission.schema.json" in prompt
    assert "public/runtime_contract.json" in prompt
    assert "submit_greeks_submission_v1" in prompt
    for teaching_detail in (
        "Raw DuckDB access is not granted",
        "exactly 80",
        "unrounded final binary64 root",
        "Cross-asset correlation is provenance only",
        "ROUND_HALF_EVEN",
        "observed_decimal",
        "d1 =",
        "d2 =",
        "N(x)",
        "n(x)",
        "Allowed direct imports:",
        "Trusted tools:",
        "Filesystem reads are limited",
        "Denied imports include",
        "Budget:",
        "Preserve the queried row order",
        "Do not substitute undeclared conventions",
    ):
        assert teaching_detail.casefold() not in prompt_casefold
    assert "sampling_seed" not in prompt
    assert "oracle_answer" not in prompt

    assert method_contract["rate_curve"] == "flat_continuously_compounded"
    assert method_contract["dividend_curve"] == "flat_continuously_compounded"
    assert method_contract["analytic_operation_order_id"] == (
        "bsm-analytic-float64-operation-order-v1"
    )
    assert method_contract["analytic_operation_sequence"] == [
        "sqrt_tau",
        "variance",
        "root_variance",
        "carry",
        "log_moneyness",
        "standardized_drift",
        "standardized_normal_arguments",
        "discount_factors",
        "discounted_spot_and_strike",
        "normal_density",
        "unscaled_price_and_greeks",
        "vega_rho_0.01_scaling",
        "theta_365_scaling",
    ]


def test_solver_interface_bytes_are_bound_into_the_task_identity(
    repository_root: Path,
) -> None:
    global_profile, overlay = _profiles(repository_root)
    runtime = compose_runtime_contract(global_profile, overlay)
    prompt = render_bsm_greeks_prompt(market_greeks_method_contract(), runtime)
    schema_bytes = (
        repository_root / "schemas/bsm-greeks-submission-v1.schema.json"
    ).read_bytes()
    baseline = bsm_market_greeks_solver_interface_digest(
        prompt=prompt,
        runtime_contract=runtime,
        submission_schema_bytes=schema_bytes,
    )
    changed = bsm_market_greeks_solver_interface_digest(
        prompt=prompt.replace("a complete submission", "one complete submission"),
        runtime_contract=runtime,
        submission_schema_bytes=schema_bytes,
    )
    identity_arguments = {
        "parent_logical_checksum": "a" * 64,
        "valuation_date": date(2026, 8, 3),
        "selected_underlyings": ("SYN-A", "SYN-B"),
    }

    assert baseline != changed
    assert stable_package_task_id(
        **identity_arguments, solver_interface_digest=baseline
    ) != stable_package_task_id(
        **identity_arguments, solver_interface_digest=changed
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda overlay: overlay["allowed_direct_imports"].append("numpy"),
        lambda overlay: overlay["trusted_tools"].update({"raw_database": 1}),
        lambda overlay: overlay["filesystem"]["read"].append("authoring_private/*"),
        lambda overlay: overlay["resource_budget"].update({"memory_mib": 4096}),
        lambda overlay: overlay.update({"network": True}),
    ],
)
def test_task_overlay_cannot_expand_global_capabilities(
    repository_root: Path, change
) -> None:
    global_profile, overlay = _profiles(repository_root)
    changed = deepcopy(overlay)
    change(changed)

    with pytest.raises(CapabilityViolation, match="cannot|expands"):
        compose_runtime_contract(global_profile, changed)


@pytest.mark.parametrize(
    "source",
    [
        "import QuantLib\n",
        "import duckdb\n",
        "import requests\n",
        "import subprocess\nsubprocess.run(['x'])\n",
        "open('authoring_private/answer.json')\n",
        "for _ in range(79):\n    pass\n",
        "for _ in range(81):\n    pass\n",
        "for _ in range(80):\n    break\n",
        "tolerance = 1e-8\n",
        "observed = (float(row['bid']) + float(row['ask'])) / 2\n",
        "value = __import__('math')\n",
    ],
)
def test_solver_policy_blocks_shortcuts_and_method_changes(
    repository_root: Path, source: str
) -> None:
    global_profile, overlay = _profiles(repository_root)
    runtime = compose_runtime_contract(global_profile, overlay)

    with pytest.raises(CapabilityViolation):
        audit_solver_source(source, runtime)


def test_checked_in_reference_solver_passes_the_same_source_audit(
    repository_root: Path,
) -> None:
    global_profile, overlay = _profiles(repository_root)
    runtime = compose_runtime_contract(global_profile, overlay)
    source = (
        repository_root
        / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/reference_solver.py"
    ).read_text(encoding="utf-8")

    audit_solver_source(source, runtime)
    assert "synthetic_derivatives" not in source
    assert "QuantLib" not in source
    assert "duckdb" not in source


def test_solver_and_verifier_dependency_locks_are_separate(
    repository_root: Path,
) -> None:
    solver_lock = (
        repository_root / "environments/solver/requirements.lock"
    ).read_text(encoding="utf-8")
    verifier_lock = (
        repository_root / "environments/verifier/requirements.lock"
    ).read_text(encoding="utf-8")

    assert "QuantLib" not in solver_lock
    assert "QuantLib==1.39" in verifier_lock
