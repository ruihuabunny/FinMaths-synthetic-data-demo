"""Render the complete solver-facing BSM market-Greeks task prompt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS_BY_TARGET,
    MetricSpec,
)


_REQUIRED_TOOLS = {
    "query_greeks_underlying_market_v2": 1,
    "query_greeks_option_quotes_v2": 1,
    "submit_greeks_submission_v2": 1,
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


__all__ = ["render_bsm_greeks_prompt", "render_bsm_metric_prompt"]
