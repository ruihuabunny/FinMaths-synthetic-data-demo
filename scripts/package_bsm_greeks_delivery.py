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
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (  # noqa: E402
    build_portable_bsm_metric_suite,
    build_portable_bsm_metric_suite_v3,
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
    parser.add_argument(
        "--profile",
        type=Path,
        help="build one atomic single-metric suite using this frozen profile",
    )
    parser.add_argument(
        "--allocation-id",
        help="public deterministic allocation identity for metric-suite mode",
    )
    parser.add_argument(
        "--assignment-file",
        type=Path,
        help="optional explicit target/source assignment in metric-suite mode",
    )
    parser.add_argument(
        "--expected-source-task-count",
        type=int,
        default=24,
        help="required completed source-run size in metric-suite mode",
    )
    parser.add_argument(
        "--metric-protocol",
        choices=("v2-static-json", "v3-duckdb-query"),
        default="v2-static-json",
        help="trusted-tool protocol used only in metric-suite mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    metric_mode = arguments.profile is not None or arguments.allocation_id is not None
    if metric_mode:
        if arguments.profile is None or arguments.allocation_id is None:
            parser.error("metric-suite mode requires both --profile and --allocation-id")
        if arguments.task_ids or arguments.expected_task_count is not None:
            parser.error(
                "--task-id/--expected-task-count belong to legacy combined mode"
            )
        builder = (
            build_portable_bsm_metric_suite_v3
            if arguments.metric_protocol == "v3-duckdb-query"
            else build_portable_bsm_metric_suite
        )
        delivery = builder(
            source_run=arguments.run_root,
            output_root=arguments.output_root,
            delivery_id=arguments.delivery_id,
            profile=arguments.profile,
            allocation_id=arguments.allocation_id,
            assignment_file=arguments.assignment_file,
            expected_source_task_count=arguments.expected_source_task_count,
        )
        manifest = delivery.manifest
        print(
            json.dumps(
                {
                    "status": manifest["delivery_status"],
                    "target_count": len(manifest["target_order"]),
                    "tasks_per_target": manifest["tasks_per_target"],
                    "total_task_count": manifest["total_task_count"],
                    "unique_source_task_count": manifest[
                        "unique_source_task_count"
                    ],
                    "unique_source_database_count": manifest[
                        "unique_source_database_count"
                    ],
                    "unique_market_content_count": manifest[
                        "unique_market_content_count"
                    ],
                    "unique_derived_task_count": manifest[
                        "unique_derived_task_count"
                    ],
                    "unique_derived_database_count": manifest[
                        "unique_derived_database_count"
                    ],
                    "delivery_root": str(delivery.delivery_root),
                },
                sort_keys=True,
            )
        )
        return 0
    if arguments.assignment_file is not None:
        parser.error("--assignment-file requires metric-suite mode")
    if arguments.metric_protocol != "v2-static-json":
        parser.error("--metric-protocol v3-duckdb-query requires metric-suite mode")
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
