"""Render the complete solver-facing BSM market-Greeks task prompt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS_BY_TARGET,
    METRIC_SPECS_DB_QUERY_V3_BY_TARGET,
    MetricSpec,
)


_REQUIRED_TOOLS = {
    "query_greeks_underlying_market_v2": 1,
    "query_greeks_option_quotes_v2": 1,
    "submit_greeks_submission_v2": 1,
}
_V3_REQUIRED_TOOLS = {
    "query_public_duckdb_v3": 10,
    "submit_greeks_submission_v3": 1,
}
_V3_HOST_PROTOCOL = "read-only-duckdb-query-schema-submit-v3"
_V3_QUERY_RESOURCE_BUDGET = {
    "trusted_query_calls": 10,
    "submission_calls": 1,
    "query_result_rows": 1000,
    "query_result_bytes": 1048576,
    "query_timeout_seconds": 5,
    "duckdb_memory_mib": 256,
}


def render_bsm_greeks_prompt(runtime: Mapping[str, Any]) -> str:
    """Render the deterministic v2 prompt without formula-bearing hints."""

    actual_tools = {
        str(item["name"]): int(item["max_calls"])
        for item in runtime["trusted_tools"]
    }
    if actual_tools != _REQUIRED_TOOLS:
        raise ValueError("prompt received a different trusted-tool schedule")
    return """# Task: Market-implied BSM unit Greeks

For every public option quote, recover its market-implied volatility and compute the following Black–Scholes–Merton unit Greeks:

- `market_implied_volatility`
- `unit_delta`
- `unit_gamma`
- `unit_vega_1volpt`
- `unit_theta_1calendar_day`
- `unit_rho_1pct`

Return one result for every option row. Outputs are for one unit of the option; do not apply the contract multiplier.

## Public data

Call `query_greeks_underlying_market_v2` exactly once to obtain the public underlying spots and pricing contexts.

Call `query_greeks_option_quotes_v2` exactly once to obtain the public option contracts and bid/ask quotes.

Match each option row to its underlying-market row using `task_id`, `snapshot_id`, `valuation_date`, and `underlying_id`. Preserve the option-query row order in the submission.

Use only the returned public data. Do not access the raw database or any verifier, reference, private, audit, parent, or generator artifact.

## Model and numerical conventions

- Use the European Black–Scholes–Merton model.
- Treat the supplied risk-free rate and dividend yield as annualized continuously compounded decimals.
- Use `Actual365Fixed` time to expiry.
- Compute the observed option price from the bid/ask midpoint using Decimal arithmetic, then cast that midpoint once to binary64 for model calculations.
- Recover annualized market-implied volatility as a decimal using exactly 80 bisection updates on the closed volatility bracket `[1e-6, 5.0]`.
- Do not stop early, change the bracket, use a fallback, use another root finder, or regress pricing terms.
- Compute analytic BSM Greeks using the unrounded implied volatility obtained after all 80 updates.
- This is a quote-level Q-measure market-implied task. Do not use historical or P-measure drift/diffusion, hidden latent volatility, or private generator values.
- Implement BSM pricing, implied-volatility inversion, and analytic Greeks yourself. No pricing or Greek formulas are provided by the task.

## Units

- Implied volatility is annualized and expressed as a decimal; `0.20` means 20%.
- Delta is reported per `+1.00` change in spot.
- Gamma is reported as the Delta change per `+1.00` change in spot.
- Vega is reported for a `+0.01` absolute change in volatility.
- Theta is reported for one calendar day passing with expiry fixed.
- Rho is reported for a `+0.01` absolute change in the continuously compounded risk-free rate.

## Allowed and forbidden resources

Allowed direct imports:

`dataclasses`, `datetime`, `decimal`, `hashlib`, `itertools`, `json`, `math`, `typing`

Forbidden imports and shortcuts include:

`QuantLib`, `duckdb`, `mibian`, `numpy`, `pandas`, `py_vollib`, `rateslib`, `requests`, `scipy`, `socket`, `subprocess`, and `urllib`.

Do not use a package function that directly computes BSM prices, implied volatility, or Greeks. Do not use network access, package installation, dynamic imports, process spawning, undeclared filesystem access, raw database access, or hidden verifier/reference/private files.

## Output and submission

The final object must match `public/submission.schema.json` exactly.

- Include every public option row exactly once.
- Preserve the queried option row order.
- Do not add extra fields or commentary.
- Do not round intermediate values.
- Canonical numeric outputs must be decimal strings with exactly 8 digits after the decimal point, using `ROUND_HALF_EVEN`.
- Canonicalize negative zero to `0.00000000`.

Call `submit_greeks_submission_v2` exactly once with the complete submission.
"""


def render_bsm_metric_prompt(
    spec: MetricSpec, runtime_contract: Mapping[str, Any]
) -> str:
    """Render one frozen single-metric prompt without formula-bearing hints."""

    if not isinstance(spec, MetricSpec):
        raise TypeError("metric prompt requires a MetricSpec")
    if METRIC_SPECS_BY_TARGET.get(spec.target) != spec:
        raise ValueError("metric prompt requires a frozen registered spec")
    trusted_tools = runtime_contract["trusted_tools"]
    if not isinstance(trusted_tools, list) or len(trusted_tools) != len(
        _REQUIRED_TOOLS
    ):
        raise ValueError("prompt received a different trusted-tool schedule")
    actual_tools = {
        str(item["name"]): int(item["max_calls"])
        for item in trusted_tools
    }
    if actual_tools != _REQUIRED_TOOLS:
        raise ValueError("prompt received a different trusted-tool schedule")

    if spec.needs_iv_status:
        task_description = (
            "For every public option quote, recover its market-implied "
            "volatility."
        )
        model_instruction = (
            "- After all 80 updates, use the unrounded binary64 root as the "
            "result before final canonicalization."
        )
        row_contract = (
            "Each result row must contain exactly `row_id`, `iv_status` set "
            "to `CONVERGED_FIXED_ITERATIONS`, and "
            f"`{spec.output_field}`."
        )
        checkpoint_instruction = ""
        exclusion_instruction = (
            "Do not submit any Greek or additional result field."
        )
    else:
        task_description = (
            "For every public option quote, recover its market-implied "
            f"volatility internally and compute only {spec.display_name}."
        )
        model_instruction = (
            "- Compute the requested analytic BSM metric using the unrounded "
            "binary64 implied volatility obtained after all 80 updates."
        )
        row_contract = (
            "Each result row must contain exactly `row_id` and "
            f"`{spec.output_field}`."
        )
        checkpoint_instruction = (
            "The recovered implied volatility is an internal calculation "
            "checkpoint. Do not include it or its convergence status in the "
            "submission.\n\n"
        )
        exclusion_instruction = (
            "Do not submit any other Greek or additional result field."
        )

    return f"""# Task: Market-implied BSM {spec.display_name}

{task_description}

Return one result for every option row. Outputs are for one unit of the option; do not apply the contract multiplier.

## Public data

Call `query_greeks_underlying_market_v2` exactly once to obtain the public underlying spots and pricing contexts.

Call `query_greeks_option_quotes_v2` exactly once to obtain the public option contracts and bid/ask quotes.

Match each option row to its underlying-market row using `task_id`, `snapshot_id`, `valuation_date`, and `underlying_id`. Preserve the option-query row order in the submission.

Use only the returned public data. Do not access the raw database or any verifier, reference, private, audit, parent, or generator artifact.

## Model and numerical conventions

- Use the European Black–Scholes–Merton model under the pricing measure associated with the supplied money-market numeraire.
- Treat the supplied risk-free rate and dividend yield as annualized continuously compounded decimals.
- Use `Actual365Fixed` time to expiry.
- Compute the observed option price from the bid/ask midpoint using Decimal arithmetic, then cast that midpoint once to binary64 for model calculations.
- Recover annualized market-implied volatility as a decimal using exactly 80 bisection updates on the closed volatility bracket `[1e-6, 5.0]`.
- Do not stop early, change the bracket, use a fallback, use another root finder, or regress pricing terms.
{model_instruction}
- This is a quote-level Q-measure market-implied task. Do not use historical or P-measure drift/diffusion, hidden latent volatility, or private generator values.
- Implement BSM pricing, implied-volatility inversion, and the requested calculation yourself. No pricing or metric formula is provided by the task.

## Target output and units

- Target field: `{spec.output_field}`.
- {spec.unit_description}
- {row_contract}

{checkpoint_instruction}{exclusion_instruction}

## Allowed and forbidden resources

Allowed direct imports:

`dataclasses`, `datetime`, `decimal`, `hashlib`, `itertools`, `json`, `math`, `typing`

Forbidden imports and shortcuts include:

`QuantLib`, `duckdb`, `mibian`, `numpy`, `pandas`, `py_vollib`, `rateslib`, `requests`, `scipy`, `socket`, `subprocess`, and `urllib`.

Do not use a package function that directly computes BSM prices, implied volatility, or analytic metrics. Do not use network access, package installation, dynamic imports, process spawning, undeclared filesystem access, raw database access, or hidden verifier/reference/private files.

## Output and submission

The final object must match `public/submission.schema.json` exactly.

- Include every public option row exactly once.
- Preserve the queried option row order.
- Do not add extra fields or commentary.
- Do not round intermediate values.
- Canonical numeric outputs must be decimal strings with exactly 8 digits after the decimal point, using `ROUND_HALF_EVEN`.
- Canonicalize negative zero to `0.00000000`.

Call `submit_greeks_submission_v2` exactly once with the complete submission.
"""


def _validate_v3_runtime_contract(runtime_contract: Mapping[str, Any]) -> None:
    if runtime_contract.get("host_protocol") != _V3_HOST_PROTOCOL:
        raise ValueError("prompt received a different v3 host protocol")
    trusted_tools = runtime_contract.get("trusted_tools")
    if not isinstance(trusted_tools, list) or len(trusted_tools) != len(
        _V3_REQUIRED_TOOLS
    ):
        raise ValueError("prompt received a different v3 trusted-tool schedule")
    actual_tools = {
        str(item["name"]): int(item["max_calls"])
        for item in trusted_tools
    }
    if actual_tools != _V3_REQUIRED_TOOLS:
        raise ValueError("prompt received a different v3 trusted-tool schedule")
    resource_budget = runtime_contract.get("resource_budget")
    if not isinstance(resource_budget, Mapping) or any(
        resource_budget.get(name) != value
        for name, value in _V3_QUERY_RESOURCE_BUDGET.items()
    ):
        raise ValueError("prompt received different v3 query resource limits")


def render_bsm_metric_task_objective_v3(spec: MetricSpec) -> str:
    """Render the target-specific objective without database-layout hints."""

    if spec.needs_iv_status:
        task_description = (
            "For every public option quote, recover its market-implied "
            "volatility."
        )
    else:
        task_description = (
            "For every public option quote, recover its market-implied "
            f"volatility internally and compute only {spec.display_name}."
        )
    return f"""# Task: Market-implied BSM {spec.display_name}

{task_description}

Return one result for every public option row. Outputs are for one unit of the option; do not apply the contract multiplier.
"""


def render_public_database_access_v3() -> str:
    """Render the common v3 DuckDB-access contract without layout leakage."""

    return """## Public data access

All public inputs required to solve this task are stored in a read-only DuckDB dataset.

Use `query_public_duckdb_v3` to inspect the available schemas, relations, columns, keys, and public data, and then retrieve the inputs required for the calculation. You must make at least one and at most 10 read-only database queries; inspection queries count toward this limit. Each call accepts exactly one SQL statement.

Each query has a 5-second execution limit and a 256 MiB DuckDB memory limit. One result is limited to 1,000 rows and 1 MiB of compact UTF-8 JSON. Query results preserve the selected column order. Row order follows the SQL result and is not guaranteed unless your query uses an explicit ordering. DATE values are ISO date strings, DECIMAL values are exact decimal strings, INTEGER and DOUBLE values are JSON numbers, BOOLEAN values are JSON booleans, VARCHAR values are JSON strings, and NULL is JSON null. A truncated result is marked explicitly; do not treat a truncated result as complete input.

Use only information obtained from the public database through this trusted tool and from this task contract. Do not access the database file directly or access verifier, reference, private, audit, parent, or generator artifacts. Once you submit, you may not make another database query.
"""


def render_bsm_numerical_conventions_v3(spec: MetricSpec) -> str:
    """Render the frozen mathematical and numerical contract for one metric."""

    if spec.needs_iv_status:
        target_instruction = (
            "- After all 80 updates, use the unrounded binary64 root as the "
            "result before final canonicalization."
        )
    else:
        target_instruction = (
            "- Compute the requested analytic BSM metric using the unrounded "
            "binary64 implied volatility obtained after all 80 updates."
        )
    return f"""## Model and numerical conventions

- Use the European Black–Scholes–Merton model under the pricing measure associated with the supplied money-market numeraire.
- Treat the supplied risk-free rate and dividend yield as annualized continuously compounded decimals.
- Use `Actual365Fixed` time to expiry.
- Compute the observed option price from the bid/ask midpoint using Decimal arithmetic, then cast that midpoint once to binary64 for model calculations.
- Recover annualized market-implied volatility as a decimal using exactly 80 bisection updates on the closed volatility bracket `[1e-6, 5.0]`.
- Do not stop early, change the bracket, use a fallback, use another root finder, or regress pricing terms.
{target_instruction}
- This is a quote-level Q-measure market-implied task. Do not use historical or P-measure drift/diffusion, hidden latent volatility, or private generator values.
- Implement BSM pricing, implied-volatility inversion, and the requested calculation yourself. No pricing or metric formula is provided by the task.
"""


def render_bsm_target_output_v3(spec: MetricSpec) -> str:
    """Render the target field, method identity, units, and row shape."""

    if spec.needs_iv_status:
        row_contract = (
            "Each result row must contain exactly `row_id`, `iv_status` set "
            "to `CONVERGED_FIXED_ITERATIONS`, and "
            f"`{spec.output_field}`."
        )
        checkpoint_instruction = ""
        exclusion_instruction = (
            "Do not submit any Greek or additional result field."
        )
    else:
        row_contract = (
            "Each result row must contain exactly `row_id` and "
            f"`{spec.output_field}`."
        )
        checkpoint_instruction = (
            "The recovered implied volatility is an internal calculation "
            "checkpoint. Do not include it or its convergence status in the "
            "submission.\n\n"
        )
        exclusion_instruction = (
            "Do not submit any other Greek or additional result field."
        )
    return f"""## Target output and units

- Target field: `{spec.output_field}`.
- Required method ID: `{spec.method_id}`.
- {spec.unit_description}
- {row_contract}

{checkpoint_instruction}{exclusion_instruction}
"""


def render_bsm_runtime_restrictions_v3() -> str:
    """Render the common solver capability restrictions."""

    return """## Allowed and forbidden resources

Allowed direct imports:

`dataclasses`, `datetime`, `decimal`, `hashlib`, `itertools`, `json`, `math`, `typing`

Forbidden imports and shortcuts include:

`QuantLib`, `duckdb`, `mibian`, `numpy`, `pandas`, `py_vollib`, `rateslib`, `requests`, `scipy`, `socket`, `subprocess`, and `urllib`.

Do not use a package function that directly computes BSM prices, implied volatility, or analytic metrics. Do not use network access, package installation, dynamic imports, process spawning, undeclared filesystem access, raw database access, or hidden verifier/reference/private files.

The overall solver budget is 1 GiB of memory and 600 seconds of wall-clock time.
"""


def render_bsm_submission_contract_v3() -> str:
    """Render the common unordered, key-aligned submission contract."""

    return """## Output and submission

The final object must match `public/submission.schema.json` exactly.

- Include exactly one result for every public option row.
- Each public `row_id` must appear exactly once; missing, duplicate, or extra row IDs are invalid.
- Row order is not semantically significant.
- Do not add extra fields or commentary.
- Do not round intermediate values.
- Canonical numeric outputs must be decimal strings with exactly 8 digits after the decimal point, using `ROUND_HALF_EVEN`.
- Canonicalize negative zero to `0.00000000`.

Call `submit_greeks_submission_v3` exactly once with the complete submission. Submission is final, and no query may follow it.
"""


def render_bsm_metric_prompt_v3(
    spec: MetricSpec, runtime_contract: Mapping[str, Any]
) -> str:
    """Render one v3 database-exploration prompt without layout or formulas."""

    if not isinstance(spec, MetricSpec):
        raise TypeError("metric prompt requires a MetricSpec")
    if METRIC_SPECS_DB_QUERY_V3_BY_TARGET.get(spec.target) != spec:
        raise ValueError("v3 metric prompt requires a frozen v3 registered spec")
    _validate_v3_runtime_contract(runtime_contract)
    blocks = (
        render_bsm_metric_task_objective_v3(spec),
        render_public_database_access_v3(),
        render_bsm_numerical_conventions_v3(spec),
        render_bsm_target_output_v3(spec),
        render_bsm_runtime_restrictions_v3(),
        render_bsm_submission_contract_v3(),
    )
    return "\n".join(block.rstrip() for block in blocks) + "\n"


__all__ = [
    "render_bsm_greeks_prompt",
    "render_bsm_metric_prompt",
    "render_bsm_metric_prompt_v3",
    "render_bsm_metric_task_objective_v3",
    "render_public_database_access_v3",
    "render_bsm_numerical_conventions_v3",
    "render_bsm_target_output_v3",
    "render_bsm_runtime_restrictions_v3",
    "render_bsm_submission_contract_v3",
]
