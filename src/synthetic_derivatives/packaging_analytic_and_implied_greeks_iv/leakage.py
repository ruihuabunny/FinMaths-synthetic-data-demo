"""Public artifact and package-view leakage checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from synthetic_derivatives.export.contracts import assert_no_private_leakage
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    assert_bsm_greeks_database_safe,
    load_bsm_greeks_option_quotes,
    load_bsm_greeks_underlying_market,
)


_PUBLIC_FILES = {
    "prompt.md",
    "runtime_contract.json",
    "submission.schema.json",
    "task.duckdb",
}
_EVALUATION_VIEW_FILES = [
    "manifest.json",
    "public/prompt.md",
    "public/runtime_contract.json",
    "public/submission.schema.json",
]
_FORBIDDEN_PUBLIC_TEXT = (
    "sampling_seed",
    "generator_seed",
    "canonical_answer",
    "oracle_answer",
    "authoring_private/",
    "reference/trajectory",
    "reference_solver",
    "latent_iv",
    "generator_sigma",
    "private_diffusion",
)
_FORBIDDEN_FORMULA_INDICATORS = (
    "analytic_operation_formulas",
    "analytic_operation_sequence",
    "operation_order",
    "d1 =",
    "d2 =",
    "cdf_d1",
    "cdf_d2",
    "cdf_minus_d1",
    "cdf_minus_d2",
    "discounted_spot *",
    "discounted_strike *",
    "diffusion_theta",
    "vega_per_unit",
    "rho_per_unit",
    "normal_cdf formula",
    "normal_pdf formula",
)
_FORBIDDEN_QUERY_FIELDS = {
    "market_implied_volatility",
    "delta",
    "gamma",
    "vega",
    "theta",
    "rho",
    "unit_delta",
    "unit_gamma",
    "unit_vega_1volpt",
    "unit_theta_1calendar_day",
    "unit_rho_1pct",
    "d1",
    "d2",
    "canonical_answer",
    "oracle_answer",
    "reference_answer",
    "latent_iv",
    "generator_sigma",
    "private_diffusion",
}


def _json_object(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def scan_solver_observable_content(
    value: Any,
    label: str,
    *,
    forbid_answer_fields: bool = False,
) -> None:
    """Reject formula-bearing hints and answer fields on a solver-facing surface."""

    if forbid_answer_fields and isinstance(value, dict):
        leaked = _FORBIDDEN_QUERY_FIELDS & {str(key).casefold() for key in value}
        if leaked:
            raise ValueError(f"{label} contains answer-bearing fields: {sorted(leaked)}")
        for key, item in value.items():
            scan_solver_observable_content(
                item,
                f"{label}.{key}",
                forbid_answer_fields=True,
            )
    elif forbid_answer_fields and isinstance(value, list):
        for index, item in enumerate(value):
            scan_solver_observable_content(
                item,
                f"{label}[{index}]",
                forbid_answer_fields=True,
            )
    serialized = (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    ).casefold()
    for token in (*_FORBIDDEN_PUBLIC_TEXT, *_FORBIDDEN_FORMULA_INDICATORS):
        if token in serialized:
            raise ValueError(f"{label} contains a solution-leakage token: {token}")


def scan_public_artifacts(package_root: str | Path) -> dict[str, Any]:
    """Reject undeclared files, answer data, private keys, and unsafe DB rows."""

    root = Path(package_root)
    public = root / "public"
    actual = {path.name for path in public.iterdir() if path.is_file()}
    if actual != _PUBLIC_FILES:
        raise ValueError(f"public artifact allowlist changed: {sorted(actual)}")
    assert_bsm_greeks_database_safe(public / "task.duckdb")
    runtime = _json_object(public / "runtime_contract.json")
    assert_no_private_leakage(runtime, "public.runtime_contract.json")
    combined_text = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in public.iterdir()
        if path.suffix in {".md", ".json"}
    )
    scan_solver_observable_content(combined_text, "public artifacts")
    submission_schema = _json_object(public / "submission.schema.json")
    row_properties = submission_schema["$defs"]["resultRow"]["properties"]
    if {"d1", "d2"} & set(row_properties):
        raise ValueError("submission schema exposes standardized-normal intermediates")
    # ``iv_status`` and ``market_implied_volatility`` are declared output field
    # names, not hidden answer values.  The generic market-DB scanner forbids
    # those names because they would be leakage in an input database, so the
    # output schema is checked against its exact public field set instead.
    expected_output_fields = {
        "row_id",
        "iv_status",
        "market_implied_volatility",
        "unit_delta",
        "unit_gamma",
        "unit_vega_1volpt",
        "unit_theta_1calendar_day",
        "unit_rho_1pct",
    }
    if set(row_properties) != expected_output_fields:
        raise ValueError("submission schema output fields changed")
    underlyings = list(load_bsm_greeks_underlying_market(public / "task.duckdb"))
    options = list(load_bsm_greeks_option_quotes(public / "task.duckdb"))
    scan_solver_observable_content(
        underlyings,
        "query_greeks_underlying_market_v2 response",
        forbid_answer_fields=True,
    )
    scan_solver_observable_content(
        options,
        "query_greeks_option_quotes_v2 response",
        forbid_answer_fields=True,
    )
    return {
        "status": "clean",
        "public_files": sorted(actual),
        "database_relations": [
            "metadata.public_task",
            "solver_visible.underlying_market_inputs",
            "solver_visible.option_quote_inputs",
        ],
        "query_payloads": {
            "underlying_row_count": len(underlyings),
            "option_row_count": len(options),
            "semantic_scan": "clean",
        },
        "private_token_scan": "clean",
    }


def assert_view_allowlist(
    view_root: str | Path, expected_files: list[str]
) -> None:
    root = Path(view_root)
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError(f"release view contains a symlink: {root.name}")
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )
    if actual != sorted(expected_files):
        raise ValueError(f"release view differs from its allowlist: {root.name}")


def scan_evaluation_view(view_root: str | Path) -> dict[str, Any]:
    """Prove that the mountable solver tree contains only four safe files."""

    root = Path(view_root)
    assert_view_allowlist(root, _EVALUATION_VIEW_FILES)
    for relative in _EVALUATION_VIEW_FILES:
        path = root / relative
        if path.suffix in {".json", ".md"}:
            scan_solver_observable_content(
                path.read_text(encoding="utf-8"),
                f"evaluation view {relative}",
            )
    return {
        "status": "clean",
        "files": list(_EVALUATION_VIEW_FILES),
        "physical_isolation": "allowlist_exact",
        "semantic_scan": "clean",
    }


__all__ = [
    "assert_view_allowlist",
    "scan_evaluation_view",
    "scan_public_artifacts",
    "scan_solver_observable_content",
]
