# Solver implementation

- Solver code may consume only declared public inputs and effective runtime
  capabilities. Do not import authoring, verifier, private package state, QuantLib,
  packaged pricing libraries, or undeclared data clients.
- Repository functions are developer/reference implementations; they are not
  automatically mounted in evaluation. Keep standalone reference artifacts within
  their source/runtime audit policy.
- Current analytic BSM/IV/Greeks kernels use Python binary64 and `math`. Preserve
  the task-declared operation order, Decimal midpoint conversion, bracket,
  unconditional 80-step bisection schedule, unit scaling and 8-place
  `ROUND_HALF_EVEN` canonicalization.
- Do not expose `d1`, `d2`, formulas, hidden volatility or intermediate oracle
  values through configs, schemas, prompts or query payloads.
- Keep raw values unrounded until the output contract. Failure status and price
  domain behavior are contract-visible and must not be replaced by fallback
  algorithms.
- Never share pricing/root/Greek numerical code with the trusted verifier. Exact
  canonical agreement is tested across independent implementations.

