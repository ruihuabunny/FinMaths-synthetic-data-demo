#!/usr/bin/env python3
"""Build one accepted market-implied BSM Greeks golden task package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.package import (  # noqa: E402
    build_bsm_greeks_package,
    verify_bsm_greeks_package,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.parent import (  # noqa: E402
    materialize_joint_parent,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one deterministic BSM Greeks agent task package"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--parent", type=Path, help="existing frozen 1.7 parent")
    source.add_argument(
        "--base-parent-config",
        type=Path,
        help="1.6 config from which to build a temporary private P/Q parent",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPOSITORY_ROOT
        / "configs/task_packages/bsm_market_implied_greeks_v1.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / "task_packages",
    )
    parser.add_argument(
        "--build-status",
        choices=("ACCEPTED", "RELEASED"),
        default="ACCEPTED",
    )
    return parser


def _build(parent: Path, arguments: argparse.Namespace) -> dict[str, object]:
    package = build_bsm_greeks_package(
        repository_root=REPOSITORY_ROOT,
        parent_database=parent,
        output_root=arguments.output_root,
        package_config_path=arguments.config,
        build_status=arguments.build_status,
    )
    verify_bsm_greeks_package(
        package.package_root,
        repository_root=REPOSITORY_ROOT,
    )
    return {
        "status": "accepted",
        "task_id": package.database_manifest.task_id,
        "package_root": str(package.package_root),
        "public_logical_checksum": (
            package.database_manifest.public_logical_checksum
        ),
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.parent is not None:
        result = _build(arguments.parent, arguments)
    else:
        with tempfile.TemporaryDirectory(prefix="bsm-greeks-parent-") as temp:
            root = Path(temp)
            parent = materialize_joint_parent(
                base_config=arguments.base_parent_config,
                private_config_output=root / "parent.config.json",
                parent_database=root / "parent.duckdb",
            )
            result = _build(parent, arguments)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
