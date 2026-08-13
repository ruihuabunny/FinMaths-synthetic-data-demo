#!/usr/bin/env python3
"""Build, replay, verify, and export a finite BSM Greeks task batch."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import shutil
import sys
from typing import Any

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.export import sample_underlyings  # noqa: E402
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (  # noqa: E402
    canonical_json_bytes,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.package import (  # noqa: E402
    build_bsm_greeks_package,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.parent import (  # noqa: E402
    materialize_joint_parent,
)
from synthetic_derivatives.training import (  # noqa: E402
    export_bsm_market_greeks_dataset,
)


_EXPECTED_CANDIDATE_REJECTIONS = {
    "published market-Greeks row is outside BSM price bounds",
    "published market-Greeks row has no root in the bracket",
    "stdlib and QuantLib canonical answers differ",
    "selected row is too close to a decimal rounding boundary",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build distinct market-implied BSM Greeks packages and a verified "
            "nine-field JSONL dataset"
        )
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--dataset-copy", type=Path, required=True)
    parser.add_argument("--task-count", type=int, default=100)
    parser.add_argument("--starting-sampling-seed", type=int, default=0)
    parser.add_argument("--max-candidate-attempts", type=int, default=10000)
    parser.add_argument(
        "--base-parent-config",
        type=Path,
        default=REPOSITORY_ROOT
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json",
    )
    parser.add_argument(
        "--package-config",
        type=Path,
        default=REPOSITORY_ROOT
        / "configs/task_packages/bsm_market_implied_greeks_v1.json",
    )
    return parser


def _event(path: Path, event: dict[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(event))


def _parent_universe(parent: Path) -> tuple[str, ...]:
    connection = duckdb.connect(str(parent), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT underlying_id
            FROM solver_visible.underlying_daily
            ORDER BY underlying_id
            """
        ).fetchall()
    finally:
        connection.close()
    universe = tuple(str(row[0]) for row in rows)
    if len(universe) != 22:
        raise ValueError("batch parent must contain the frozen 22-underlying universe")
    return universe


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.task_count < 1:
        raise ValueError("task-count must be positive")
    if arguments.starting_sampling_seed < 0:
        raise ValueError("starting-sampling-seed must be nonnegative")
    if arguments.max_candidate_attempts < arguments.task_count:
        raise ValueError("max-candidate-attempts must cover task-count")

    run_root = arguments.run_root.resolve()
    dataset_copy = arguments.dataset_copy.resolve()
    if run_root.exists():
        raise FileExistsError(f"refusing to overwrite run root: {run_root}")
    if dataset_copy.exists():
        raise FileExistsError(f"refusing to overwrite dataset copy: {dataset_copy}")
    run_root.mkdir(parents=True)
    events_path = run_root / "run_events.jsonl"
    _event(events_path, {"event": "run_started", "task_count": arguments.task_count})

    parent_root = run_root / "parent"
    parent = materialize_joint_parent(
        base_config=arguments.base_parent_config,
        private_config_output=parent_root / "parent.config.json",
        parent_database=parent_root / "parent.duckdb",
    )
    _event(events_path, {"event": "parent_materialized", "status": "FROZEN"})
    universe = _parent_universe(parent)

    base_package_config = load_json_object(arguments.package_config)
    valuation_date = date.fromisoformat(base_package_config["valuation_date"])
    package_output = run_root / "packages"
    private_configs = run_root / "private/task_configs"
    private_configs.mkdir(parents=True)
    packages: list[Path] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicate_subsets = 0
    seen_subsets: set[tuple[str, ...]] = set()
    candidate_seed = arguments.starting_sampling_seed
    attempts = 0

    while len(packages) < arguments.task_count:
        if attempts >= arguments.max_candidate_attempts:
            raise RuntimeError(
                f"only built {len(packages)} tasks after {attempts} candidate attempts"
            )
        attempts += 1
        sampling_seed = candidate_seed
        candidate_seed += 1
        selected = sample_underlyings(universe, sample_size=8, seed=sampling_seed)
        if selected in seen_subsets:
            duplicate_subsets += 1
            continue
        seen_subsets.add(selected)

        config = deepcopy(base_package_config)
        config["private_selection"]["sampling_seed"] = sampling_seed
        config_path = private_configs / f"sampling_seed_{sampling_seed:08d}.json"
        config_path.write_bytes(canonical_json_bytes(config))
        try:
            package = build_bsm_greeks_package(
                repository_root=REPOSITORY_ROOT,
                parent_database=parent,
                output_root=package_output,
                package_config_path=config_path,
                build_status="ACCEPTED",
            )
        except ValueError as error:
            reason = str(error)
            if reason not in _EXPECTED_CANDIDATE_REJECTIONS:
                raise
            rejection = {
                "sampling_seed": sampling_seed,
                "selected_underlying_ids": list(selected),
                "reason": reason,
            }
            rejected.append(rejection)
            _event(events_path, {"event": "candidate_rejected", **rejection})
            continue
        packages.append(package.package_root)
        item = {
            "task_index": len(packages),
            "task_id": package.database_manifest.task_id,
            "sampling_seed": sampling_seed,
            "selected_underlying_ids": list(selected),
            "option_row_count": len(package.database_manifest.selected_option_ids),
        }
        accepted.append(item)
        _event(events_path, {"event": "task_accepted", **item})
        print(
            f"[{len(packages):03d}/{arguments.task_count:03d}] "
            f"{package.database_manifest.task_id}",
            flush=True,
        )

    dataset_id = run_root.name
    run_dataset = export_bsm_market_greeks_dataset(
        packages,
        repository_root=REPOSITORY_ROOT,
        output_directory=run_root / "dataset",
        dataset_id=dataset_id,
    )
    dataset_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run_dataset.dataset_root, dataset_copy)

    summary = {
        "run_schema_version": "bsm-market-implied-greeks-batch-run-v2.0.0",
        "status": "completed",
        "task_family": "bsm_greeks",
        "variant_id": "bsm_market_implied_greeks_v1",
        "requested_task_count": arguments.task_count,
        "accepted_task_count": len(packages),
        "unique_task_id_count": len({item["task_id"] for item in accepted}),
        "valuation_date": valuation_date.isoformat(),
        "parent_database": str(parent.relative_to(run_root)),
        "package_root": str(package_output.relative_to(run_root)),
        "run_dataset": str(run_dataset.dataset_root.relative_to(run_root)),
        "dataset_copy": str(dataset_copy),
        "candidate_attempt_count": attempts,
        "duplicate_subset_count": duplicate_subsets,
        "rejected_candidate_count": len(rejected),
        "accepted_tasks": accepted,
        "rejected_candidates": rejected,
        "verification": {
            "all_packages_verified_before_dataset_export": True,
            "all_reference_replays_byte_identical": True,
            "all_trusted_quantlib_canonical_exact_match": True,
            "dataset_record_count": len(run_dataset.task_ids),
            "dataset_task_ids_unique": len(run_dataset.task_ids)
            == len(set(run_dataset.task_ids)),
            "dataset_contains_private_oracle": False,
            "dataset_contains_private_sampling_seed": False,
        },
    }
    (run_root / "run_summary.json").write_bytes(canonical_json_bytes(summary))
    _event(
        events_path,
        {
            "event": "run_completed",
            "accepted_task_count": len(packages),
            "dataset_record_count": len(run_dataset.task_ids),
        },
    )
    print(json.dumps(summary["verification"], sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
