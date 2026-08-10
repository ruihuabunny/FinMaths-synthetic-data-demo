"""Export exact authoring, train/dev, and evaluation package views."""

from __future__ import annotations

from pathlib import Path
import shutil

from synthetic_derivatives.packaging.leakage import assert_view_allowlist


_SOURCE_DIRECTORIES = ("public", "verifier", "reference", "authoring_private")


def release_view_files(package_root: str | Path) -> dict[str, list[str]]:
    root = Path(package_root)
    source_files = sorted(
        path.relative_to(root).as_posix()
        for directory in _SOURCE_DIRECTORIES
        for path in (root / directory).rglob("*")
        if path.is_file()
    )
    public = [item for item in source_files if item.startswith("public/")]
    reference = [item for item in source_files if item.startswith("reference/")]
    return {
        "authoring": ["manifest.json", *source_files],
        "train_dev": ["manifest.json", *public, *reference],
        "evaluation": ["manifest.json", *public],
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
            source = root / relative
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        assert_view_allowlist(target_root, files)


__all__ = ["export_release_views", "release_view_files"]
