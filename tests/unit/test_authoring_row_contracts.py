from __future__ import annotations

from synthetic_derivatives.authoring.row_contracts import ROW_TYPES_BY_TABLE
from synthetic_derivatives.authoring.schema import TABLE_SPECS


def test_named_rows_exactly_match_explicit_storage_column_order() -> None:
    assert set(ROW_TYPES_BY_TABLE) == set(TABLE_SPECS)
    for table_name, row_type in ROW_TYPES_BY_TABLE.items():
        assert issubclass(row_type, tuple)
        assert row_type._fields == TABLE_SPECS[table_name].columns


def test_business_row_contracts_expose_named_identity_and_value_fields() -> None:
    assert ROW_TYPES_BY_TABLE["underlying_daily"]._fields[1:7] == (
        "date",
        "underlying_id",
        "spot_open",
        "spot_high",
        "spot_low",
        "spot_close",
    )
    assert ROW_TYPES_BY_TABLE["option_contracts"]._fields[1:7] == (
        "option_id",
        "underlying_id",
        "template_id",
        "call_put",
        "strike",
        "expiry",
    )
