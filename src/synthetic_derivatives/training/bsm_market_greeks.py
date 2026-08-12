"""Export verified market-implied BSM Greeks packages as nine-field JSONL."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    BSM_MARKET_GREEKS_VARIANT_ID,
    canonical_json_bytes,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    load_bsm_greeks_contract,
    load_bsm_greeks_inputs,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.package import verify_bsm_greeks_package
from synthetic_derivatives.tasks.bsm_market_greeks import MarketGreeksSubmission


BSM_MARKET_GREEKS_DATASET_SCHEMA_VERSION = (
    "bsm-market-implied-greeks-nine-field-dataset-v1.0.0"
)
_RECORD_FIELDS = (
    "Problem",
    "Context",
    "Assumptions",
    "Skills",
    "Evidence",
    "Intermediate Reasoning",
    "Verification",
    "Confidence",
    "Outcome",
)


@dataclass(frozen=True)
class DatasetExport:
    dataset_root: Path
    records_path: Path
    manifest_path: Path
    task_ids: tuple[str, ...]


def _load_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"trajectory line {line_number} must be an object")
        records.append(raw)
    if not records:
        raise ValueError("reference trajectory must not be empty")
    return records


def _record(package_root: Path) -> dict[str, Any]:
    manifest = load_json_object(package_root / "manifest.json")
    if (
        manifest["task_family"] != "bsm_greeks"
        or manifest["variant_id"] != BSM_MARKET_GREEKS_VARIANT_ID
        or manifest["build_status"] not in {"ACCEPTED", "RELEASED"}
    ):
        raise ValueError("dataset export requires an accepted BSM Greeks package")
    task_id = str(manifest["task_id"])
    database = package_root / "public/task.duckdb"
    inputs = [row.to_tool_mapping() for row in load_bsm_greeks_inputs(database)]
    if len(inputs) != 160 or {row["task_id"] for row in inputs} != {task_id}:
        raise ValueError("dataset evidence must contain exactly 160 task rows")
    method_contract = load_bsm_greeks_contract(database)
    trajectory = _load_json_lines(package_root / "reference/trajectory.jsonl")
    submission = load_json_object(package_root / "reference/final_submission.json")
    if MarketGreeksSubmission.from_mapping(submission).to_dict() != submission:
        raise ValueError("dataset outcome must be a canonical submission")
    if submission["task_id"] != task_id or len(submission["rows"]) != len(inputs):
        raise ValueError("dataset outcome differs from its public evidence identity")

    record = {
        "Problem": {
            "task_id": task_id,
            "prompt": (package_root / "public/prompt.md").read_text(
                encoding="utf-8"
            ),
        },
        "Context": {
            "dataset_record_schema_version": (
                BSM_MARKET_GREEKS_DATASET_SCHEMA_VERSION
            ),
            "task_id": task_id,
            "task_family": manifest["task_family"],
            "task_version": manifest["task_version"],
            "variant_id": manifest["variant_id"],
            "coordinates": manifest["coordinates"],
            "valuation_date": manifest["valuation_date"],
            "selected_underlying_ids": manifest["selected_underlying_ids"],
            "parent_snapshot": manifest["parent_snapshot"],
            "public_child_snapshot": manifest["public_child_snapshot"],
            "joint_market_contract_id": manifest["joint_market_contract_id"],
            "p_dependence_spec_id": manifest["p_dependence_spec_id"],
            "q_dependence_spec_id": manifest["q_dependence_spec_id"],
            "dependence_policy_id": manifest["dependence_policy_id"],
            "runtime_environment": manifest["runtime_environment"],
        },
        "Assumptions": {
            "probability_measure": method_contract["probability_measure"],
            "numeraire_id": method_contract["numeraire_id"],
            "economic_object": method_contract["economic_object"],
            "conditioning_information": method_contract[
                "conditioning_information"
            ],
            "time_axis": method_contract["time_axis"],
            "day_count": method_contract["day_count"],
            "pricing_law": method_contract["pricing_law"],
            "risk_neutral_dynamics": method_contract[
                "risk_neutral_dynamics"
            ],
            "cross_asset_dependence_policy": method_contract[
                "cross_asset_dependence_policy"
            ],
        },
        "Skills": [
            "query the frozen public task contract and ordered inputs",
            "construct the visible Decimal bid/ask midpoint",
            "solve implied volatility with exactly 80 bisection updates",
            "compute analytic unit BSM Delta/Gamma/Vega/Theta/Rho",
            "canonicalize binary64 results with Decimal ROUND_HALF_EVEN",
            "submit rows in the frozen canonical order",
        ],
        "Evidence": {
            "method_contract": method_contract,
            "public_inputs": inputs,
        },
        "Intermediate Reasoning": trajectory,
        "Verification": {
            "status": "passed",
            "verifier_id": manifest["verifier"]["id"],
            "reference_replays": 2,
            "reference_replays_byte_identical": True,
            "trusted_quantlib_canonical_exact_match": True,
            "package_and_release_views_verified": True,
        },
        "Confidence": {
            "status": "verified",
            "basis": [
                "fixed-schedule solver replay",
                "independent pinned-QuantLib recomputation",
                "canonical exact equality",
            ],
        },
        "Outcome": submission,
    }
    if tuple(record) != _RECORD_FIELDS:
        raise RuntimeError("dataset record does not have the frozen nine fields")
    return record


def export_bsm_market_greeks_dataset(
    package_roots: Iterable[str | Path],
    *,
    repository_root: str | Path,
    output_directory: str | Path,
    dataset_id: str,
) -> DatasetExport:
    """Verify packages and atomically export one self-contained JSONL dataset."""

    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("dataset_id must be a non-empty string")
    repository = Path(repository_root).resolve()
    output = Path(output_directory).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite dataset: {output}")
    roots_with_ids = []
    for raw_root in package_roots:
        root = Path(raw_root).resolve()
        manifest = load_json_object(root / "manifest.json")
        roots_with_ids.append((str(manifest["task_id"]), root))
    roots_with_ids.sort()
    task_ids = tuple(task_id for task_id, _ in roots_with_ids)
    if not task_ids or len(task_ids) != len(set(task_ids)):
        raise ValueError("dataset packages must have unique task identities")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bsm-greeks-dataset-", dir=output.parent))
    try:
        records_path = staging / "records.jsonl"
        with records_path.open("wb") as stream:
            parent_groups = set()
            valuation_dates = set()
            for task_id, root in roots_with_ids:
                verify_bsm_greeks_package(root, repository_root=repository)
                record = _record(root)
                if record["Context"]["task_id"] != task_id:
                    raise ValueError("dataset task ordering identity changed")
                parent_groups.add(
                    (
                        record["Context"]["parent_snapshot"]["snapshot_id"],
                        record["Context"]["parent_snapshot"]["revision"],
                        record["Context"]["parent_snapshot"]["logical_checksum"],
                    )
                )
                valuation_dates.add(record["Context"]["valuation_date"])
                stream.write(canonical_json_bytes(record))
        manifest = {
            "dataset_schema_version": BSM_MARKET_GREEKS_DATASET_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "task_family": "bsm_greeks",
            "variant_id": BSM_MARKET_GREEKS_VARIANT_ID,
            "task_count": len(task_ids),
            "task_ids": list(task_ids),
            "records_file": "records.jsonl",
            "record_order": "task_id_lexicographic",
            "record_fields": list(_RECORD_FIELDS),
            "parent_snapshot_group_count": len(parent_groups),
            "valuation_dates": sorted(valuation_dates),
            "snapshot_split_policy": "group_by_parent_snapshot_identity",
            "contains_public_inputs": True,
            "contains_verified_reference_outcomes": True,
            "contains_private_oracle": False,
            "contains_private_sampling_seed": False,
        }
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        os.replace(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return DatasetExport(
        dataset_root=output,
        records_path=output / "records.jsonl",
        manifest_path=output / "manifest.json",
        task_ids=task_ids,
    )


__all__ = [
    "BSM_MARKET_GREEKS_DATASET_SCHEMA_VERSION",
    "DatasetExport",
    "export_bsm_market_greeks_dataset",
]
