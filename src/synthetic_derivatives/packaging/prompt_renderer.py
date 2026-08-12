"""Render the BSM Greeks prompt from frozen method and runtime contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synthetic_derivatives.packaging.contracts import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_VARIANT_ID,
)


def render_runtime_permissions(runtime: Mapping[str, Any]) -> str:
    """Render the permission section solely from the effective contract."""

    tools = "\n".join(
        f"- `{item['name']}`: at most {item['max_calls']} call(s)."
        for item in runtime["trusted_tools"]
    )
    imports = ", ".join(f"`{item}`" for item in runtime["allowed_direct_imports"])
    denied_imports = ", ".join(f"`{item}`" for item in runtime["denied_imports"])
    budget = runtime["resource_budget"]
    return f"""## Effective runtime permissions

This task runs under `{runtime['profile_id']}` in `{runtime['environment_id']}`.
The task overlay is intersected with `{runtime['global_profile_id']}` and cannot
expand it.

Allowed direct imports: {imports}.

Trusted tools:

{tools}

Filesystem reads are limited to {runtime['filesystem']['read']}; writes are
limited to {runtime['filesystem']['write']}. Network access is
`{str(runtime['network']).lower()}`, dynamic installation is
`{str(runtime['dynamic_installation']).lower()}`, and process spawning is
`{str(runtime['process_spawning']).lower()}`.

Budget: {budget['vcpu']} vCPU, {budget['memory_mib']} MiB memory,
{budget['wall_clock_seconds']} seconds wall clock,
{budget['trusted_query_calls']} total trusted query calls,
{budget['submission_calls']} submission call, and
{budget['submission_bytes']} submission bytes.

Denied imports include {denied_imports}. Raw database connections; database
`ATTACH`, `COPY`, `INSTALL`, or `LOAD`; packaged pricing/IV/Greek/surface APIs;
networking; subprocesses; and undeclared authoring, trusted-evaluation, or
private resources are prohibited. This section describes enforced runtime
policy; it does not grant capabilities by itself."""


def render_bsm_greeks_prompt(
    method_contract: Mapping[str, Any], runtime: Mapping[str, Any]
) -> str:
    """Render one deterministic public prompt with no answer-bearing values."""

    if method_contract.get("contract_id") != BSM_MARKET_GREEKS_METHOD_ID:
        raise ValueError("prompt received a different method contract")
    if method_contract.get("variant_id") != BSM_MARKET_GREEKS_VARIANT_ID:
        raise ValueError("prompt received a different variant contract")
    inversion = method_contract["iv_inversion"]
    lower, upper = inversion["volatility_bracket"]
    runtime_section = render_runtime_permissions(runtime)
    row_order = ", ".join(method_contract["row_order"])
    return f"""# Market-implied BSM unit Greeks

## Task goal

For every published option row, recover the Black-Scholes-Merton implied
volatility from the visible bid/ask midpoint using the exact fixed method below,
then compute five unit-option Greeks. Submit one ordered canonical JSON file.

Variant: `{BSM_MARKET_GREEKS_VARIANT_ID}`  
Method: `{BSM_MARKET_GREEKS_METHOD_ID}`

## Visible database contract

`public/task.duckdb` contains exactly these relations:

- `metadata.public_task`: public task and joint-market provenance.
- `solver_visible.greeks_task_inputs`: the ordered row inputs and data dictionary.
- `solver_visible.greeks_task_contract`: one canonical method-contract JSON row.

Use `query_greeks_task_contract_v1` once and
`query_greeks_task_inputs_v1` once. Raw DuckDB access is not granted. Input
columns are `row_id`, `snapshot_id`, `valuation_date`, `underlying_id`,
`option_id`, `call_put`, `spot`, `strike`, `expiry`,
`time_to_expiry_actual365`, `bid`, `ask`, `contract_multiplier`, `currency`,
`risk_free_rate`, `dividend_yield`, `calendar`, `day_count`,
`exercise_style`, and `settlement_type`.

## Market and measure semantics

Each row is a cash-settled European vanilla on a known ex-dividend USD spot.
Pricing is under the declared common `Q` associated with the USD money-market
numeraire:

`dS/S = (r - q) dt + sigma_IV dW^Q`.

Rates and dividend yields are flat, continuously compounded annual rates. Time
is calendar time with Actual/365 Fixed. The package records a non-diagonal,
measure-qualified P/Q underlying dependence contract because the rows belong to
one joint market. Cross-asset correlation is provenance only: it is not an input
to a marginal vanilla BSM price, IV, or Greek formula.

## Visible-price inversion

For each row, construct

```text
observed_decimal = (Decimal(str(bid)) + Decimal(str(ask))) / 2
observed_price = float(observed_decimal)  # exactly one cast
low = {lower}
high = {upper}
repeat exactly {inversion['iterations']} times:
    mid = (low + high) / 2
    if bsm_price(mid) < observed_price:
        low = mid
    else:
        high = mid
sigma_IV = (low + high) / 2
```

There is no early stop, tolerance, Newton/Brent step, or fallback. Authoring
gates guarantee a valid finite root in the closed bracket for every published
row. Use the unrounded final binary64 root for the Greek calculation; round the
reported volatility only at output serialization.

## BSM definitions and unit Greeks

Use

```text
d1 = (log(S/K) + (r-q+0.5*sigma_IV^2)*T) / (sigma_IV*sqrt(T))
d2 = d1 - sigma_IV*sqrt(T)
N(x) = 0.5 * erfc(-x / sqrt(2))
n(x) = exp(-0.5*x*x) / sqrt(2*pi)
```

Evaluate the standard discounted European call or put price. Report:

- `unit_delta`: spot first derivative per one spot unit;
- `unit_gamma`: spot second derivative per one spot unit squared;
- `unit_vega_1volpt`: `0.01 * dV/dsigma`;
- `unit_theta_1calendar_day`: annual valuation-time theta with expiry fixed,
  divided by 365;
- `unit_rho_1pct`: `0.01 * dV/dr` for the continuous risk-free rate.

Hold the row-local `S,K,T,r,q,sigma_IV` inputs fixed according to each derivative
definition. Do not multiply any result by `contract_multiplier`. `d1` and `d2`
may exist only as transient formula variables; they are not inputs, labels, or
submission fields.

## Canonical output

Call `submit_greeks_submission_v1` once with the object written conceptually as
`submission/submission.json`. It must satisfy
`public/submission.schema.json` version
`{BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION}`.

Every numeric answer is a JSON string with exactly 8 decimal places, produced by
`Decimal(str(binary64_value)).quantize(Decimal("0.00000001"),
rounding=ROUND_HALF_EVEN)`. Canonicalize negative zero to `0.00000000`.
Rows must preserve the public `row_id` order, which corresponds to `{row_order}`.
Missing, duplicate, extra, reordered, non-finite, scientific-notation, or extra
free-text/intermediate fields are invalid.

{runtime_section}
"""


__all__ = ["render_bsm_greeks_prompt", "render_runtime_permissions"]
