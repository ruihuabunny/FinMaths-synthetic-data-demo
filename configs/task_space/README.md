# Task-space catalog and executable capabilities

`derivatives_v2.json` is the broad design catalog. Its coordinate compatibility
rules describe structurally meaningful combinations, including planned model
and product families; they do not authorize generation, scheduling, packaging,
or runtime execution.

`executable_capabilities_v1.json` is the fail-closed sidecar for the one model
family currently implemented in this repository, `tdgbm_bsm`. Runtime-facing
entrypoints require an exact five-field key plus the declared output contract.
An absent key is unavailable even when the design catalog says its coordinates
are compatible.

The allowed evidence statuses are:

- `catalog_only`: structural declaration only; never admitted to a portable pool;
- `library_implemented`: repository solver/verifier path exists, without a
  portable package/runtime claim;
- `portable_verified`: generator, solver, verifier, materializer, runtime and
  tests are all explicitly identified.

The checked-in registry contains only current `tdgbm_bsm` evidence. It does not
register Heston, local-vol, L3 Monte Carlo, or another planned family. Registry
parsing and admission live under
[`model_families/`](../../src/synthetic_derivatives/model_families/README.md),
while the Python `task_space` package remains compatibility-only.
