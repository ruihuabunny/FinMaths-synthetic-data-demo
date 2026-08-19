# Task: Market-implied BSM unit Rho for one percentage point

For every public option quote, recover its market-implied volatility internally and compute only unit Rho for one percentage point.

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
- Compute the requested analytic BSM metric using the unrounded binary64 implied volatility obtained after all 80 updates.
- This is a quote-level Q-measure market-implied task. Do not use historical or P-measure drift/diffusion, hidden latent volatility, or private generator values.
- Implement BSM pricing, implied-volatility inversion, and the requested calculation yourself. No pricing or metric formula is provided by the task.

## Target output and units

- Target field: `unit_rho_1pct`.
- Reported for a +0.01 absolute change in the continuously compounded risk-free rate.
- Each result row must contain exactly `row_id` and `unit_rho_1pct`.

The recovered implied volatility is an internal calculation checkpoint. Do not include it or its convergence status in the submission.

Do not submit any other Greek or additional result field.

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
