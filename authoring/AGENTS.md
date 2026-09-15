# Authoring assets

This directory contains internal job inputs and reusable templates, not
solver-visible task data.

- Keep templates synchronized with the parser and schema versions implemented in
  `src/synthetic_derivatives/authoring/` and covered by `tests/public/`.
- Treat seed, RNG partition, draw order, model parameters, latent volatility,
  private dependence state, quality-gate evidence, and oracle material as private
  authoring information.
- A template may document field semantics and safe examples. It must not be copied
  into an Agent evaluation view or used as a substitute for a versioned task
  contract.
- Do not place generated databases or task deliveries here. Generate into `/tmp`
  or another explicit scratch path unless the user requests a versioned artifact.

