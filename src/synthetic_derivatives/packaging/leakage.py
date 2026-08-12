"""Public artifact and package-view leakage checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from synthetic_derivatives.export.contracts import assert_no_private_leakage
from synthetic_derivatives.packaging.database import (
    assert_bsm_greeks_database_safe,
)


_PUBLIC_FILES = {
    "prompt.md",
    "runtime_contract.json",
    "submission.schema.json",
    "task.duckdb",
}
_FORBIDDEN_PUBLIC_TEXT = (
    "sampling_seed",
    "generator_seed",
    "canonical_answer",
    "oracle_answer",
    "authoring_private/",
    "reference/trajectory",
)


def _json_object(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
    for token in _FORBIDDEN_PUBLIC_TEXT:
        if token in combined_text:
            raise ValueError(f"public artifact contains a private token: {token}")
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
    return {
        "status": "clean",
        "public_files": sorted(actual),
        "database_relations": [
            "metadata.public_task",
            "solver_visible.greeks_task_contract",
            "solver_visible.greeks_task_inputs",
        ],
        "private_token_scan": "clean",
    }


def assert_view_allowlist(
    view_root: str | Path, expected_files: list[str]
) -> None:
    root = Path(view_root)
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )
    if actual != sorted(expected_files):
        raise ValueError(f"release view differs from its allowlist: {root.name}")


__all__ = ["assert_view_allowlist", "scan_public_artifacts"]
