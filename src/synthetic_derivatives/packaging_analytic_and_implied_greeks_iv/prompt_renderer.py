"""Render the minimal BSM Greeks routing prompt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_VARIANT_ID,
)


def render_bsm_greeks_prompt(
    method_contract: Mapping[str, Any], runtime: Mapping[str, Any]
) -> str:
    """Render one deterministic prompt that delegates details to public contracts."""

    if method_contract.get("contract_id") != BSM_MARKET_GREEKS_METHOD_ID:
        raise ValueError("prompt received a different method contract")
    if method_contract.get("variant_id") != BSM_MARKET_GREEKS_VARIANT_ID:
        raise ValueError("prompt received a different variant contract")
    required_tools = {
        "query_greeks_task_contract_v1": 1,
        "query_greeks_task_inputs_v1": 1,
        "submit_greeks_submission_v1": 1,
    }
    actual_tools = {
        str(item["name"]): int(item["max_calls"])
        for item in runtime["trusted_tools"]
    }
    if actual_tools != required_tools:
        raise ValueError("prompt received a different trusted-tool schedule")
    return """# Market-implied BSM unit Greeks

Produce a complete submission for every public input row.

Call `query_greeks_task_contract_v1` and `query_greeks_task_inputs_v1` exactly
once each. Treat their returned data, `public/submission.schema.json`, and
`public/runtime_contract.json` as the complete authoritative specification.
Call `submit_greeks_submission_v1` exactly once.
"""


__all__ = ["render_bsm_greeks_prompt"]
