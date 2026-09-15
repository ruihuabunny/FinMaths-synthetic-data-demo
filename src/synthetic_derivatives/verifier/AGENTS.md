# Trusted verifier

- Reconstruct expected output only from solver-visible public inputs plus the
  frozen private method/oracle configuration. Never read a reference answer or
  hidden authoring latent volatility as truth.
- Use pinned QuantLib/DuckDB and an implementation independent of Solver numerical
  code. Shared imports are limited to neutral contracts when the package boundary
  explicitly permits them; portable leaves remain self-contained.
- Validate submission schema, exact public key set/count, units, status,
  canonical strings, data identity and package digests before comparing results.
- Compare canonical results with exact equality. Do not add tolerances or suppress
  a numerical disagreement; resolve the method/canonicalization mismatch.
- Verifier code, tests, oracle config and filesystem are hidden from the Agent and
  physically absent from evaluation views.

