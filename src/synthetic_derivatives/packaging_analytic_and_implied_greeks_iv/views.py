"""Export exact authoring, train/dev, and evaluation package views."""

from __future__ import annotations

from pathlib import Path
import shutil

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    BSM_MARKET_GREEKS_SOLVER_INTERFACE_CONTRACT_VERSION,
    EVALUATION_VIEW_SCHEMA_VERSION,
    canonical_json_bytes,
    digest_file,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.leakage import assert_view_allowlist


_SOURCE_DIRECTORIES = ("public", "verifier", "reference", "authoring_private")
_SOLVER_PUBLIC_FILES = (
    "public/prompt.md",
    "public/runtime_contract.json",
    "public/submission.schema.json",
)


def release_view_files(package_root: str | Path) -> dict[str, list[str]]:
    root = Path(package_root)
    source_files = sorted(
        path.relative_to(root).as_posix()
        for directory in _SOURCE_DIRECTORIES
        for path in (root / directory).rglob("*")
        if path.is_file()
    )
    reference = [item for item in source_files if item.startswith("reference/")]
    return {
        "authoring": ["manifest.json", *source_files],
        "train_dev": ["manifest.json", *_SOLVER_PUBLIC_FILES, *reference],
        "evaluation": ["manifest.json", *_SOLVER_PUBLIC_FILES],
    }


def release_view_manifest(
    package_root: str | Path,
    view_name: str,
    files: list[str],
) -> dict[str, object]:
    """Build a view-local manifest that exposes no host or verifier details."""

    root = Path(package_root)
    source = load_json_object(root / "manifest.json")
    visible = [item for item in files if item != "manifest.json"]
    return {
        "evaluation_view_schema_version": EVALUATION_VIEW_SCHEMA_VERSION,
        "view_kind": view_name,
        "task_id": source["task_id"],
        "task_family": source["task_family"],
        "task_version": source["task_version"],
        "variant_id": source["variant_id"],
        "solver_interface_contract_version": (
            BSM_MARKET_GREEKS_SOLVER_INTERFACE_CONTRACT_VERSION
        ),
        "artifacts": {
            relative: digest_file(root / relative) for relative in visible
        },
        "trusted_tools": load_json_object(
            root / "public/runtime_contract.json"
        )["trusted_tools"],
    }


def export_release_views(
    package_root: str | Path, views: dict[str, list[str]]
) -> None:
    root = Path(package_root)
    views_root = root / "views"
    if views_root.exists():
        raise FileExistsError("refusing to overwrite package release views")
    for view_name, files in views.items():
        target_root = views_root / view_name
        for relative in files:
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "manifest.json" and view_name != "authoring":
                target.write_bytes(
                    canonical_json_bytes(
                        release_view_manifest(root, view_name, files)
                    )
                )
            else:
                shutil.copy2(root / relative, target)
        assert_view_allowlist(target_root, files)


__all__ = [
    "export_release_views",
    "release_view_files",
    "release_view_manifest",
]
