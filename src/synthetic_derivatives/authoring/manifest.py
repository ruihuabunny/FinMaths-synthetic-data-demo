"""Atomic publication of the manifest adjacent to a committed DuckDB snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from synthetic_derivatives.authoring.schema_ddl import SCHEMA_VERSION
from synthetic_derivatives.authoring.visibility import assert_no_private_metadata_leakage


def write_snapshot_manifest(database: Path, summary: dict[str, Any]) -> None:
    """Atomically publish the current logical revision next to ``database``."""

    manifest_path = database.with_suffix(".manifest.json")
    temporary_path = manifest_path.with_suffix(".manifest.json.tmp")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": summary["snapshot_id"],
        "status": summary["status"],
        "revision": summary["revision"],
        "database_file": database.name,
        "generator_config_id": summary["generator_config_id"],
        "generator_version": summary["generator_version"],
        "quantlib_version": summary["quantlib_version"],
        "duckdb_version": summary["duckdb_version"],
        "date_min": summary["date_min"],
        "date_max": summary["date_max"],
        "business_date_count": summary["business_date_count"],
        "underlying_count": summary["underlying_count"],
        "option_contract_count": summary["option_contract_count"],
        "underlying_daily_count": summary["underlying_daily_count"],
        "option_daily_count": summary["option_daily_count"],
        "pricing_metadata_count": summary["pricing_metadata_count"],
        "underlying_dependence_count": summary["underlying_dependence_count"],
        "option_chain_spec_count": summary["option_chain_spec_count"],
        "intraday_bridge_spec_count": summary["intraday_bridge_spec_count"],
        "underlying_volume_model_count": summary[
            "underlying_volume_model_count"
        ],
        "option_pricing_audit_count": summary["option_pricing_audit_count"],
    }
    assert_no_private_metadata_leakage(manifest, path="manifest")
    temporary_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(manifest_path)
