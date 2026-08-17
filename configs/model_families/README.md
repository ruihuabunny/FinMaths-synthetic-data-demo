# Model-family identities

This directory contains declarative identities only. The only currently
implemented family is `tdgbm_bsm`: deterministic time-inhomogeneous GBM history
under `P`, European BSM pricing under the declared money-market `Q`, and the
current drift-only/same-diffusion measure mapping.

JSON documents never select Python imports. Authoring implementations are
resolved by the explicit registry in `synthetic_derivatives.authoring.backends`.
Adding a catalog model here without its real authoring, solver, verifier,
runtime, package, and test evidence does not make it executable.
