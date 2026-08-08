#!/usr/bin/env python3
"""Materialize deterministic 8-underlying F2A v5 task packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.authoring.f2a_v5 import (  # noqa: E402
    NONZERO_SIGNATURES,
    PILOT_TARGET_SIGNATURES,
    materialize_agent_task,
)
from synthetic_derivatives.verifier.f2a_database import qualify_parent_db  # noqa: E402


def _signatures(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    result = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(result) != len(set(result)) or any(item not in NONZERO_SIGNATURES for item in result):
        raise argparse.ArgumentTypeError(
            "target signatures must be unique values from 001,010,011,100,101,110,111"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-db", type=Path, required=True)
    parser.add_argument("--num-samples", type=int, default=1)
    parser.add_argument("--sampling-seed", type=int)
    parser.add_argument("--mutation-seed", type=int)
    parser.add_argument(
        "--target-signatures",
        type=_signatures,
        default=PILOT_TARGET_SIGNATURES,
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--private-output-dir", type=Path)
    parser.add_argument(
        "--publication-mode",
        choices=("pilot", "release"),
        default="pilot",
        help="release mode requires a gate-passing private cohort report",
    )
    parser.add_argument(
        "--cohort-calibration-report",
        type=Path,
        help="private JSON report used only by the release publication gate",
    )
    parser.add_argument(
        "--qualify-only",
        action="store_true",
        help="print parent qualification and do not write task packages",
    )
    args = parser.parse_args()
    if args.num_samples <= 0:
        parser.error("num-samples must be positive")
    if args.qualify_only:
        qualification = qualify_parent_db(args.parent_db, run_estimator_gate=True)
        print(json.dumps(qualification.to_dict(), indent=2, sort_keys=True))
        return 0 if qualification.qualified else 1
    if args.sampling_seed is None or args.mutation_seed is None or args.output_dir is None:
        parser.error(
            "task materialization requires --sampling-seed, --mutation-seed and --output-dir"
        )
    cohort_report = None
    if args.cohort_calibration_report is not None:
        cohort_report = json.loads(
            args.cohort_calibration_report.read_text(encoding="utf-8")
        )
    private_root = args.private_output_dir or args.output_dir.with_name(
        args.output_dir.name + "_private"
    )
    results = []
    for sample_index in range(args.num_samples):
        task = materialize_agent_task(
            parent_db=args.parent_db,
            output_root=args.output_dir,
            private_output_root=private_root,
            sampling_seed=args.sampling_seed + sample_index,
            mutation_seed=args.mutation_seed + sample_index,
            target_signatures=args.target_signatures,
            publication_mode=args.publication_mode,
            cohort_calibration_report=cohort_report,
        )
        results.append(
            {
                "public_directory": str(task.public_directory),
                "private_directory": str(task.private_directory),
                "task_id": task.public_manifest["task_id"],
                "release_status": task.release_status,
            }
        )
    print(json.dumps({"tasks": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
