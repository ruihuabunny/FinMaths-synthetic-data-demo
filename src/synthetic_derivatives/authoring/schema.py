"""Stable compatibility façade for authoring DuckDB schema operations."""

from synthetic_derivatives.authoring.persistence import merge_rows, table_columns
from synthetic_derivatives.authoring.schema_ddl import (
    DDL,
    SCHEMA_BOOTSTRAP,
    SCHEMA_VERSION,
)
from synthetic_derivatives.authoring.schema_migrations import (
    MIGRATABLE_SCHEMA_VERSIONS,
    initialize_schema,
)
from synthetic_derivatives.authoring.table_specs import TABLE_SPECS, TableSpec
from synthetic_derivatives.authoring.visibility import (
    PRIVATE_METADATA_KEYS,
    assert_no_private_metadata_leakage,
    public_dynamics_projection,
)

__all__ = [
    "PRIVATE_METADATA_KEYS",
    "DDL",
    "MIGRATABLE_SCHEMA_VERSIONS",
    "SCHEMA_BOOTSTRAP",
    "SCHEMA_VERSION",
    "TABLE_SPECS",
    "TableSpec",
    "assert_no_private_metadata_leakage",
    "initialize_schema",
    "merge_rows",
    "public_dynamics_projection",
    "table_columns",
]
