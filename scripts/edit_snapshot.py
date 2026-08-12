#!/usr/bin/env python3
"""Repository-local entry point for the incremental DuckDB editor."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.authoring.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
