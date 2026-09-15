# Task: Market-implied BSM unit Delta

For every public option quote, recover its market-implied volatility internally and compute only unit Delta.

Return one result for every public option row. Outputs are for one unit of the option; do not apply the contract multiplier.
## Public data access

All public inputs required to solve this task are stored in a read-only DuckDB dataset.

Use `query_public_duckdb_v3` to inspect the available schemas, relations, columns, keys, and public data, and then retrieve the inputs required for the calculation. You must make at least one and at most 10 read-only database queries; inspection queries count toward this limit. Each call accepts exactly one SQL statement.

Each query has a 5-second execution limit and a 256 MiB DuckDB memory limit. One result is limited to 1,000 rows and 1 MiB of compact UTF-8 JSON. Query results preserve the selected column order. Row order follows the SQL result and is not guaranteed unless your query uses an explicit ordering. DATE values are ISO date strings, DECIMAL values are exact decimal strings, INTEGER and DOUBLE values are JSON numbers, BOOLEAN values are JSON booleans, VARCHAR values are JSON strings, and NULL is JSON null. A truncated result is marked explicitly; do not treat a truncated result as complete input.

Use only information obtained from the public database through this trusted tool and from this task contract. Do not access the database file directly or access verifier, reference, private, audit, parent, or generator artifacts. Once you submit, you may not make another database query.
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

- Target field: `unit_delta`.
- Required method ID: `bsm-mid-iv-bisection80-analytic-delta-v1`.
- Reported per +1.00 change in spot.
- Each result row must contain exactly `row_id` and `unit_delta`.

The recovered implied volatility is an internal calculation checkpoint. Do not include it or its convergence status in the submission.

Do not submit any other Greek or additional result field.
## Allowed and forbidden resources

Allowed direct imports:

`dataclasses`, `datetime`, `decimal`, `hashlib`, `itertools`, `json`, `math`, `typing`

Forbidden imports and shortcuts include:

`QuantLib`, `duckdb`, `mibian`, `numpy`, `pandas`, `py_vollib`, `rateslib`, `requests`, `scipy`, `socket`, `subprocess`, and `urllib`.

Do not use a package function that directly computes BSM prices, implied volatility, or analytic metrics. Do not use network access, package installation, dynamic imports, process spawning, undeclared filesystem access, raw database access, or hidden verifier/reference/private files.

The overall solver budget is 1 GiB of memory and 600 seconds of wall-clock time.
## Output and submission

The final object must match `public/submission.schema.json` exactly.

- Include exactly one result for every public option row.
- Each public `row_id` must appear exactly once; missing, duplicate, or extra row IDs are invalid.
- Row order is not semantically significant.
- Do not add extra fields or commentary.
- Do not round intermediate values.
- Canonical numeric outputs must be decimal strings with exactly 8 digits after the decimal point, using `ROUND_HALF_EVEN`.
- Canonicalize negative zero to `0.00000000`.

Call `submit_greeks_submission_v3` exactly once with the complete submission. Submission is final, and no query may follow it.
