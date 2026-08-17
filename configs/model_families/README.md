# Model-family identities

This directory contains declarative identities only. The only currently
implemented family is `tdgbm_bsm`: deterministic time-inhomogeneous GBM history
under `P`, European BSM pricing under the declared money-market `Q`, and the
current drift-only/same-diffusion measure mapping.

JSON documents never select Python imports. Authoring implementations are
resolved by the explicit registry in `synthetic_derivatives.authoring.backends`.
Adding a catalog model here without its real authoring, solver, verifier,
runtime, package, and test evidence does not make it executable.

The document also freezes the probability measures, numeraire, conditioning
information, time axis/day count, units, transition law, parameter semantics,
dtype, random ordering, canonicalization, pricing model, measure mapping, and
persisted state identity. These fields identify a stochastic model; they are not
task targets or Agent interfaces.

Parsing and `model_family_id` ↔ `M` validation live in
[`src/synthetic_derivatives/model_families/`](../../src/synthetic_derivatives/model_families/README.md).
Unknown fields and JSON-provided Python import paths are rejected.
