#!/usr/bin/env python3
"""Replay and publish one observable v3 reference trajectory per metric."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (  # noqa: E402
    canonical_json_bytes,
    digest_file,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (  # noqa: E402
    TARGET_ORDER,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (  # noqa: E402
    expected_metric_submission_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_metric_suite import (  # noqa: E402
    verify_portable_bsm_metric_suite_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.portable_tools_v3 import (  # noqa: E402
    PORTABLE_TOOL_HOST_PROTOCOL_V3,
    PortableMetricToolsV3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (  # noqa: E402
    replay_solver_source_with_tools,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.trajectory import (  # noqa: E402
    reference_trajectory_v3,
)


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def _representative_assignment(
    assignments: list[dict[str, Any]], target: str
) -> dict[str, Any]:
    candidates = sorted(
        (item for item in assignments if item.get("target") == target),
        key=lambda item: (item["round_index"], item["allocation_rank"]),
    )
    if not candidates:
        raise ValueError(f"suite has no assignment for target {target}")
    return candidates[0]


def build_reference_trajectories(
    *, suite_root: str | Path, output_directory: str | Path
) -> dict[str, Any]:
    """Atomically publish six trajectory JSONL files and their digest manifest."""

    suite = Path(suite_root).resolve()
    output = Path(output_directory).resolve()
    manifest = verify_portable_bsm_metric_suite_v3(suite)
    assignments = manifest.get("assignments")
    if not isinstance(assignments, list) or any(
        not isinstance(item, dict) for item in assignments
    ):
        raise ValueError("suite assignments are invalid")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite trajectories: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    solver = (
        REPOSITORY_ROOT
        / "src/synthetic_derivatives"
        / "packaging_analytic_and_implied_greeks_iv/reference_solver_v3.py"
    )
    try:
        entries: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(
            prefix="bsm-v3-reference-replay-"
        ) as replay_directory:
            replay_root = Path(replay_directory)
            for target in TARGET_ORDER:
                assignment = _representative_assignment(assignments, target)
                relative_task = assignment["relative_path"]
                task_root = suite / relative_task
                result = replay_solver_source_with_tools(
                    source_path=solver,
                    tools=PortableMetricToolsV3(task_root),
                    submission_directory=replay_root / target,
                    runtime_contract=load_json_object(
                        task_root
                        / "evaluation_view/public/runtime_contract.json"
                    ),
                )
                if result.submission != expected_metric_submission_v3(
                    task_root, target
                ):
                    raise ValueError(
                        f"reference solver differs from verifier for {target}"
                    )
                trajectory_path = staging / f"{target}.jsonl"
                trajectory_path.write_bytes(
                    b"".join(
                        canonical_json_bytes(event.to_dict())
                        for event in reference_trajectory_v3(result)
                    )
                )
                entries.append(
                    {
                        "target": target,
                        "task_id": assignment["derived_task_id"],
                        "round_index": assignment["round_index"],
                        "task_relative_path": relative_task,
                        "database_digest": assignment[
                            "derived_database_digest"
                        ],
                        "trajectory_path": trajectory_path.name,
                        "trajectory_digest": digest_file(trajectory_path),
                        "submission_digest": result.submission_digest,
                        "tool_calls": result.tool_calls,
                    }
                )
        trajectory_manifest = {
            "reference_trajectory_set_schema_version": (
                "bsm-market-metric-reference-trajectories-v3.0.0"
            ),
            "host_protocol": PORTABLE_TOOL_HOST_PROTOCOL_V3,
            "suite_id": manifest["suite_id"],
            "suite_manifest_digest": digest_file(
                suite / "suite_manifest.json"
            ),
            "source_run_summary_digest": manifest["source_run"][
                "run_summary_digest"
            ],
            "reference_solver_path": str(solver.relative_to(REPOSITORY_ROOT)),
            "reference_solver_digest": digest_file(solver),
            "target_count": len(entries),
            "targets": entries,
        }
        _write_json(staging / "manifest.json", trajectory_manifest)
        os.rename(staging, output)
        return trajectory_manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    manifest = build_reference_trajectories(
        suite_root=arguments.suite_root,
        output_directory=arguments.output_directory,
    )
    print(
        json.dumps(
            {
                "host_protocol": manifest["host_protocol"],
                "suite_id": manifest["suite_id"],
                "target_count": manifest["target_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
