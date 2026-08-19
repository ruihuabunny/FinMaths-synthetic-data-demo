from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import duckdb

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.authoring.schema import TABLE_SPECS


def _logical_snapshot_fingerprint(database: Path, snapshot_id: str) -> str:
    """Hash sorted logical rows, excluding only run lineage and wall-clock audit."""

    connection = duckdb.connect(str(database), read_only=True)
    try:
        payload = {}
        for table_key, specification in TABLE_SPECS.items():
            logical_columns = specification.columns[:-1]
            selected_columns = [
                (
                    f"CAST({column} AS VARCHAR) AS {column}"
                    if table_key == "pricing_metadata"
                    and column == "valuation_timestamp"
                    else column
                )
                for column in logical_columns
            ]
            ordering = ", ".join(specification.keys)
            payload[table_key] = connection.execute(
                f"""
                SELECT {', '.join(selected_columns)}
                FROM {specification.name}
                WHERE snapshot_id = ?
                ORDER BY {ordering}
                """,
                [snapshot_id],
            ).fetchall()
    finally:
        connection.close()
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_one_shot_and_append_have_one_exact_logical_snapshot_fingerprint(
    tmp_path: Path, repository_root: Path
) -> None:
    config = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json"
    )
    one_shot_config = replace(
        config, business_days=3, option_templates=config.option_templates[:2]
    )
    append_config = replace(
        config, business_days=2, option_templates=config.option_templates[:2]
    )
    one_shot_database = tmp_path / "logical-one-shot.duckdb"
    append_database = tmp_path / "logical-append.duckdb"

    with AuthoringPipeline(one_shot_database, one_shot_config) as pipeline:
        pipeline.create_smoke_snapshot()
    with AuthoringPipeline(append_database, append_config) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.append_business_days(1)

    assert _logical_snapshot_fingerprint(
        one_shot_database, config.snapshot_id
    ) == _logical_snapshot_fingerprint(append_database, config.snapshot_id)
