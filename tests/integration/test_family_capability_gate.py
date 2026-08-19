from __future__ import annotations

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.semantic_capabilities import (
    require_portable_metric_capabilities,
)


def test_new_metric_package_preflight_requires_source_and_six_targets() -> None:
    static = require_portable_metric_capabilities(query_v3=False)
    query = require_portable_metric_capabilities(query_v3=True)

    assert len(static) == len(query) == 7
    assert static[0].key.task_kind_id == query[0].key.task_kind_id == (
        "core_greeks_bundle"
    )
    assert {
        item.key.solver_interface_id for item in static[1:]
    } == {"static-json-query-schema-submit-v2"}
    assert {
        item.key.solver_interface_id for item in query[1:]
    } == {"read-only-duckdb-query-schema-submit-v3"}
