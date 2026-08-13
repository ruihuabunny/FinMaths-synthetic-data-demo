#!/usr/bin/env python3
"""Convert a completed BSM Greeks run into a portable agent-task delivery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_delivery import (  # noqa: E402
    build_portable_bsm_greeks_delivery,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package a completed BSM Greeks batch as portable agent tasks"
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / "task_packages/deliveries",
    )
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="package only this accepted task; repeat to select a subset",
    )
    parser.add_argument("--expected-task-count", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    delivery = build_portable_bsm_greeks_delivery(
        source_run=arguments.run_root,
        output_root=arguments.output_root,
        delivery_id=arguments.delivery_id,
        task_ids=arguments.task_ids,
        expected_task_count=arguments.expected_task_count,
    )
    print(
        json.dumps(
            {
                "status": delivery.manifest["delivery_status"],
                "delivery_root": str(delivery.delivery_root),
                "task_count": len(delivery.task_ids),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
