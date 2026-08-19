"""Command-line editor for mutable authoring snapshots."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def build_parser() -> argparse.ArgumentParser:
    """Build the supported snapshot-editing command contract.

    Defaults target a disposable ``/tmp`` database so an unqualified command
    cannot mutate the checked-in FROZEN public snapshot or the legacy active
    development database. They use the config-1.8 bridge/volume identity.
    """

    parser = argparse.ArgumentParser(
        prog="edit-derivatives-snapshot",
        description="Incrementally generate and edit a deterministic DuckDB snapshot.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("/tmp/metals-tdgbm-bb-keyed-volume-v1.duckdb"),
        help="DuckDB file to create or edit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json"
        ),
        help="versioned generator configuration",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "create-smoke",
        help="create or idempotently sync the config's complete business-date horizon",
    )
    append_parser = subparsers.add_parser(
        "append-dates", help="append new business dates without regenerating old rows"
    )
    append_parser.add_argument("--days", type=int, required=True)
    subparsers.add_parser(
        "sync-config",
        help="add newly configured underlyings/options and backfill the existing date span",
    )
    range_parser = subparsers.add_parser(
        "sync-range", help="sync one explicit business-date interval"
    )
    range_parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    range_parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    subparsers.add_parser("summary", help="show logical counts and revision")
    subparsers.add_parser(
        "validate", help="run read-only contract, replay and leakage gates"
    )
    subparsers.add_parser(
        "freeze", help="run quality gates and make this snapshot immutable"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse one command, execute it in a managed pipeline, and print JSON."""

    args = build_parser().parse_args(argv)
    config = load_generator_config(args.config)
    with AuthoringPipeline(args.database, config) as pipeline:
        result: dict[str, Any]
        if args.command == "create-smoke":
            result = pipeline.create_smoke_snapshot()
        elif args.command == "append-dates":
            result = pipeline.append_business_days(args.days)
        elif args.command == "sync-config":
            result = pipeline.sync_config()
        elif args.command == "sync-range":
            result = pipeline.sync_range(args.start_date, args.end_date)
        elif args.command == "summary":
            result = pipeline.summary()
        elif args.command == "validate":
            result = pipeline.validate()
        elif args.command == "freeze":
            result = pipeline.freeze()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
