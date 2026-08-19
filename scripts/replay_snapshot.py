#!/usr/bin/env python3
"""Replay a configured snapshot and compare it with a same-identity reference.

The comparison is exact for every logical market column. Run UUIDs, audit
timestamps and DuckDB physical file layout are excluded because they are fresh
lineage rather than generated market data.  A reference is required explicitly
so the config-1.8 successor cannot be compared accidentally with a legacy
public database.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from synthetic_derivatives.authoring.config import load_generator_config  # noqa: E402
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline  # noqa: E402
from synthetic_derivatives.authoring.schema import TABLE_SPECS  # noqa: E402


DEFAULT_CONFIG = REPOSITORY_ROOT / (
    "configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json"
)

METADATA_LOGICAL_COLUMNS = {
    "schema_versions": ("schema_version",),
    "snapshots": (
        "snapshot_id",
        "schema_version",
        "status",
        "generator_config_id",
        "generator_version",
        "quantlib_version",
        "duckdb_version",
        "seed",
        "rng",
        "current_revision",
    ),
    "generation_runs": (
        "snapshot_id",
        "operation",
        "status",
        "generator_config_id",
        "generator_version",
        "requested_start_date",
        "requested_end_date",
        "table_stats",
        "error_message",
    ),
    "snapshot_revisions": (
        "snapshot_id",
        "revision",
        "underlying_count",
        "option_contract_count",
        "underlying_daily_count",
        "option_daily_count",
        "pricing_metadata_count",
        "underlying_dependence_count",
        "option_chain_spec_count",
        "intraday_bridge_spec_count",
        "underlying_volume_model_count",
        "option_pricing_audit_count",
    ),
}


def _sql_path(path: Path) -> str:
    """Return one escaped DuckDB string literal body for a resolved path."""

    return str(path.resolve()).replace("'", "''")


def _table_difference(
    connection: duckdb.DuckDBPyConnection,
    *,
    qualified_table: str,
    columns: Sequence[str],
) -> dict[str, int]:
    """Return exact counts and bidirectional multiset differences for one table."""

    selected_columns = ", ".join(columns)
    reference_count = connection.execute(
        f"SELECT count(*) FROM reference.{qualified_table}"
    ).fetchone()[0]
    replay_count = connection.execute(
        f"SELECT count(*) FROM replay.{qualified_table}"
    ).fetchone()[0]
    reference_only = connection.execute(
        f"""
        SELECT count(*)
        FROM (
            SELECT {selected_columns} FROM reference.{qualified_table}
            EXCEPT ALL
            SELECT {selected_columns} FROM replay.{qualified_table}
        )
        """
    ).fetchone()[0]
    replay_only = connection.execute(
        f"""
        SELECT count(*)
        FROM (
            SELECT {selected_columns} FROM replay.{qualified_table}
            EXCEPT ALL
            SELECT {selected_columns} FROM reference.{qualified_table}
        )
        """
    ).fetchone()[0]
    return {
        "reference_rows": reference_count,
        "replay_rows": replay_count,
        "reference_only_rows": reference_only,
        "replay_only_rows": replay_only,
    }


def compare_databases(reference: Path, replay: Path) -> dict[str, Any]:
    """Compare every logical generated row and identity field exactly."""

    connection = duckdb.connect()
    try:
        connection.execute(
            f"ATTACH '{_sql_path(reference)}' AS reference (READ_ONLY)"
        )
        connection.execute(f"ATTACH '{_sql_path(replay)}' AS replay (READ_ONLY)")
        tables: dict[str, dict[str, int]] = {}
        # TABLE_SPECS is the current materialization contract.  Schema-retained
        # legacy tables such as option_pricing_audit are represented in the
        # manifest counts but are not expected outputs of a config-1.8 replay.
        for table_name, spec in TABLE_SPECS.items():
            tables[spec.name] = _table_difference(
                connection,
                qualified_table=spec.name,
                columns=spec.columns[:-1],
            )
        for table_name, columns in METADATA_LOGICAL_COLUMNS.items():
            qualified_table = f"metadata.{table_name}"
            tables[qualified_table] = _table_difference(
                connection,
                qualified_table=qualified_table,
                columns=columns,
            )
    finally:
        connection.close()

    reference_manifest_path = reference.with_suffix(".manifest.json")
    replay_manifest_path = replay.with_suffix(".manifest.json")
    manifest_matches: bool | None = None
    if reference_manifest_path.exists() and replay_manifest_path.exists():
        reference_manifest = json.loads(
            reference_manifest_path.read_text(encoding="utf-8")
        )
        replay_manifest = json.loads(replay_manifest_path.read_text(encoding="utf-8"))
        reference_manifest.pop("database_file", None)
        replay_manifest.pop("database_file", None)
        manifest_matches = reference_manifest == replay_manifest

    mismatched_tables = [
        table_name
        for table_name, result in tables.items()
        if result["reference_rows"] != result["replay_rows"]
        or result["reference_only_rows"]
        or result["replay_only_rows"]
    ]
    matches = not mismatched_tables and manifest_matches is not False
    return {
        "matches": matches,
        "reference_database": str(reference.resolve()),
        "replay_database": str(replay.resolve()),
        "manifest_matches": manifest_matches,
        "mismatched_tables": mismatched_tables,
        "tables": tables,
    }


def replay_to_database(config_path: Path, output: Path) -> None:
    """Materialize and freeze one complete snapshot from generator config."""

    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing replay output: {output}")
    config = load_generator_config(config_path)
    with AuthoringPipeline(output, config) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.freeze()


def build_parser() -> argparse.ArgumentParser:
    """Build the replay command-line contract."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="generator JSON used to replay the complete configured horizon",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        required=True,
        help="DuckDB with the same config/generator/snapshot identity",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "optional replay DuckDB to retain; when omitted, a temporary database "
            "is deleted after comparison"
        ),
    )
    return parser


def _run(config: Path, reference: Path, output: Path) -> dict[str, Any]:
    """Generate, freeze and compare one replay database."""

    if not config.is_file():
        raise FileNotFoundError(f"generator config does not exist: {config}")
    if not reference.is_file():
        raise FileNotFoundError(f"reference database does not exist: {reference}")
    if output.resolve() == reference.resolve():
        raise ValueError("replay output must not overwrite the reference database")
    print(f"Replaying {config} -> {output}", file=sys.stderr, flush=True)
    replay_to_database(config, output)
    print(f"Comparing logical rows with {reference}", file=sys.stderr, flush=True)
    return compare_databases(reference, output)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a retained or temporary full replay and emit a JSON report."""

    args = build_parser().parse_args(argv)
    try:
        if args.output is not None:
            report = _run(args.config, args.reference, args.output)
            report["replay_database_retained"] = True
        else:
            with tempfile.TemporaryDirectory(prefix="synthetic-data-replay-") as temp:
                output = Path(temp) / args.reference.name
                report = _run(args.config, args.reference, output)
                report["replay_database_retained"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["matches"] else 1
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"replay failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
