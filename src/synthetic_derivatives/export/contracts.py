"""Versioned contracts for a public-only solver DuckDB.

The export contract selects already-materialized market observations.  Its
sampling seed is authoring-side selection state: it is not a random stream of
the physical or pricing model and is never persisted in the public database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import json
from typing import Any


PUBLIC_DATABASE_SCHEMA_VERSION = "solver-market-public-duckdb-v1.0.0"
SOLVER_DATABASE_EXPORT_VERSION = "solver-database-export-v1.0.0"
SAMPLING_CONTRACT_ID = "underlying-sha256-rank-without-replacement-v1"
LOGICAL_CHECKSUM_CONTRACT_ID = "sha256-canonical-logical-rows-v1"
PINNED_DUCKDB_VERSION = "1.5.5"


@dataclass(frozen=True)
class PublicColumnContract:
    """One public column with an exact DuckDB logical type."""

    name: str
    duckdb_type: str


@dataclass(frozen=True)
class PublicTableContract:
    """Exact table/column order and canonical logical row order."""

    qualified_name: str
    columns: tuple[PublicColumnContract, ...]
    order_by: tuple[str, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


PUBLIC_TABLE_CONTRACTS = (
    PublicTableContract(
        "metadata.public_task",
        (
            PublicColumnContract("schema_version", "VARCHAR"),
            PublicColumnContract("export_version", "VARCHAR"),
            PublicColumnContract("task_id", "VARCHAR"),
            PublicColumnContract("sample_id", "VARCHAR"),
            PublicColumnContract("variant_id", "VARCHAR"),
            PublicColumnContract("snapshot_id", "VARCHAR"),
            PublicColumnContract("snapshot_revision", "INTEGER"),
            PublicColumnContract("status", "VARCHAR"),
            PublicColumnContract("parent_snapshot_id", "VARCHAR"),
            PublicColumnContract("parent_snapshot_revision", "INTEGER"),
            PublicColumnContract("parent_logical_checksum", "VARCHAR"),
            PublicColumnContract("sampling_contract_id", "VARCHAR"),
            PublicColumnContract("subset_manifest", "JSON"),
            PublicColumnContract("duckdb_version", "VARCHAR"),
            PublicColumnContract("canonicalization", "JSON"),
        ),
        ("task_id",),
    ),
    PublicTableContract(
        "solver_visible.underlying_state",
        (
            PublicColumnContract("snapshot_id", "VARCHAR"),
            PublicColumnContract("valuation_date", "DATE"),
            PublicColumnContract("underlying_id", "VARCHAR"),
            PublicColumnContract("measure", "VARCHAR"),
            PublicColumnContract("spot_close", "DECIMAL(24,8)"),
        ),
        ("valuation_date", "underlying_id"),
    ),
    PublicTableContract(
        "solver_visible.option_contracts",
        (
            PublicColumnContract("snapshot_id", "VARCHAR"),
            PublicColumnContract("option_id", "VARCHAR"),
            PublicColumnContract("underlying_id", "VARCHAR"),
            PublicColumnContract("call_put", "VARCHAR"),
            PublicColumnContract("strike", "DECIMAL(24,8)"),
            PublicColumnContract("expiry", "DATE"),
            PublicColumnContract("exercise_style", "VARCHAR"),
            PublicColumnContract("settlement_type", "VARCHAR"),
            PublicColumnContract("contract_multiplier", "DECIMAL(24,8)"),
        ),
        ("underlying_id", "expiry", "strike", "call_put", "option_id"),
    ),
    PublicTableContract(
        "solver_visible.option_chain_quotes",
        (
            PublicColumnContract("snapshot_id", "VARCHAR"),
            PublicColumnContract("valuation_date", "DATE"),
            PublicColumnContract("underlying_id", "VARCHAR"),
            PublicColumnContract("option_id", "VARCHAR"),
            PublicColumnContract("bid", "DECIMAL(24,8)"),
            PublicColumnContract("ask", "DECIMAL(24,8)"),
        ),
        ("valuation_date", "underlying_id", "option_id"),
    ),
    PublicTableContract(
        "solver_visible.pricing_context",
        (
            PublicColumnContract("snapshot_id", "VARCHAR"),
            PublicColumnContract("valuation_date", "DATE"),
            PublicColumnContract("underlying_id", "VARCHAR"),
            PublicColumnContract("measure", "VARCHAR"),
            PublicColumnContract("risk_neutral_measure_id", "VARCHAR"),
            PublicColumnContract("numeraire_id", "VARCHAR"),
            PublicColumnContract("rate_path_id", "VARCHAR"),
            PublicColumnContract("currency", "VARCHAR"),
            PublicColumnContract("discount_curve", "JSON"),
            PublicColumnContract("risk_free_rate", "DOUBLE"),
            PublicColumnContract("dividend_curve", "JSON"),
            PublicColumnContract("dividend_yield", "DOUBLE"),
            PublicColumnContract("calendar", "VARCHAR"),
            PublicColumnContract("day_count", "VARCHAR"),
        ),
        ("valuation_date", "underlying_id"),
    ),
    PublicTableContract(
        "solver_visible.underlying_dependence",
        (
            PublicColumnContract("snapshot_id", "VARCHAR"),
            PublicColumnContract("dependence_spec_id", "VARCHAR"),
            PublicColumnContract("measure", "VARCHAR"),
            PublicColumnContract("source_dependence_spec_id", "VARCHAR"),
            PublicColumnContract("mapping_id", "VARCHAR"),
            PublicColumnContract("mapping_type", "VARCHAR"),
            PublicColumnContract("risk_neutral_measure_id", "VARCHAR"),
            PublicColumnContract("numeraire_id", "VARCHAR"),
            PublicColumnContract("rate_path_id", "VARCHAR"),
            PublicColumnContract("driver_order", "JSON"),
            PublicColumnContract("formulation", "VARCHAR"),
            PublicColumnContract("factor_loading_matrix", "JSON"),
            PublicColumnContract("idiosyncratic_diagonal", "JSON"),
            PublicColumnContract("correlation_matrix", "JSON"),
            PublicColumnContract("matrix_dtype", "VARCHAR"),
            PublicColumnContract("factorization_method", "VARCHAR"),
            PublicColumnContract("factorization_order", "VARCHAR"),
            PublicColumnContract("time_grid", "VARCHAR"),
            PublicColumnContract("regime_id", "VARCHAR"),
        ),
        ("measure", "dependence_spec_id"),
    ),
)


_PRIVATE_FIELD_NAMES = frozenset(
    {
        "seed",
        "sampling_seed",
        "mutation_seed",
        "parameter_generator_seed",
        "rng",
        "run_id",
        "created_run_id",
        "generated_run_id",
        "generation_runs",
        "generation_audit",
        "generator_config_id",
        "generator_version",
        "mutation_lineage",
        "requested_signature",
        "private_truth_signature",
        "option_pricing_audit",
        "q_effective_volatility",
        "theoretical_price",
        "implied_volatility",
        "iv_solver",
        "iv_status",
        "iv_error",
        "canonical_answer",
        "reference_answer",
        "oracle",
        "hidden_oracle",
        "physical_dynamics",
        "pricing_dynamics",
        "drift_function",
        "volatility_function",
        "node_values",
        "nodes",
        "value",
        "d1",
        "d2",
    }
)
_NORMALIZED_PRIVATE_FIELD_NAMES = frozenset(
    "".join(character for character in field.casefold() if character.isalnum())
    for field in _PRIVATE_FIELD_NAMES
)


def _normalized_field_name(value: Any) -> str:
    return "".join(
        character for character in str(value).casefold() if character.isalnum()
    )


def assert_no_private_leakage(value: Any, path: str = "public") -> None:
    """Recursively reject private keys, including keys inside encoded JSON."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if _normalized_field_name(key) in _NORMALIZED_PRIVATE_FIELD_NAMES:
                raise ValueError(f"private field leaked at {path}.{key}")
            assert_no_private_leakage(item, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, item in enumerate(value):
            assert_no_private_leakage(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                return
            assert_no_private_leakage(decoded, path)


@dataclass(frozen=True)
class SolverDatabaseExportContract:
    """Private authoring selector for one immutable public task database."""

    variant_id: str
    valuation_date: date
    sampling_seed: int
    sample_size: int = 8
    option_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.variant_id, str) or not self.variant_id.strip():
            raise ValueError("variant_id must be a non-empty string")
        if type(self.valuation_date) is not date:
            raise ValueError("valuation_date must be a datetime.date")
        if (
            isinstance(self.sampling_seed, bool)
            or not isinstance(self.sampling_seed, int)
            or self.sampling_seed < 0
        ):
            raise ValueError("sampling_seed must be a nonnegative integer")
        if (
            isinstance(self.sample_size, bool)
            or not isinstance(self.sample_size, int)
            or self.sample_size < 1
        ):
            raise ValueError("sample_size must be a positive integer")
        if self.option_ids is not None:
            canonical = tuple(sorted(str(item) for item in self.option_ids))
            if not canonical or len(canonical) != len(set(canonical)):
                raise ValueError("option_ids must be non-empty and unique when supplied")
            object.__setattr__(self, "option_ids", canonical)


@dataclass(frozen=True)
class PublicDatabaseManifest:
    """Deterministic public manifest returned after atomic materialization."""

    task_id: str
    sample_id: str
    child_snapshot_id: str
    parent_snapshot_id: str
    parent_snapshot_revision: int
    parent_logical_checksum: str
    public_logical_checksum: str
    variant_id: str
    valuation_date: date
    selected_underlyings: tuple[str, ...]
    selected_option_ids: tuple[str, ...]
    table_row_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": PUBLIC_DATABASE_SCHEMA_VERSION,
            "export_version": SOLVER_DATABASE_EXPORT_VERSION,
            "task_id": self.task_id,
            "sample_id": self.sample_id,
            "sampling_contract_id": SAMPLING_CONTRACT_ID,
            "variant_id": self.variant_id,
            "child_snapshot_id": self.child_snapshot_id,
            "snapshot_revision": 1,
            "status": "FROZEN",
            "parent_snapshot_id": self.parent_snapshot_id,
            "parent_snapshot_revision": self.parent_snapshot_revision,
            "parent_logical_checksum": self.parent_logical_checksum,
            "public_logical_checksum": self.public_logical_checksum,
            "valuation_date": self.valuation_date.isoformat(),
            "selected_underlyings": list(self.selected_underlyings),
            "selected_option_ids": list(self.selected_option_ids),
            "table_row_counts": dict(sorted(self.table_row_counts.items())),
        }
        assert_no_private_leakage(payload)
        return payload


__all__ = [
    "LOGICAL_CHECKSUM_CONTRACT_ID",
    "PINNED_DUCKDB_VERSION",
    "PUBLIC_DATABASE_SCHEMA_VERSION",
    "PUBLIC_TABLE_CONTRACTS",
    "SAMPLING_CONTRACT_ID",
    "SOLVER_DATABASE_EXPORT_VERSION",
    "PublicDatabaseManifest",
    "PublicTableContract",
    "SolverDatabaseExportContract",
    "assert_no_private_leakage",
]
