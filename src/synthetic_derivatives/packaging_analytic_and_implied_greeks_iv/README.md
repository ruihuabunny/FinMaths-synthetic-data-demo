# BSM analytic/IV/Greeks packaging

This package owns the existing BSM-specific source-package and portable-suite
materialization pipeline. It is intentionally not a generic cross-family
packaging kernel: only `tdgbm_bsm` has a complete authoring, solver, verifier,
runtime and delivery evidence chain in the repository.

## Model-family refactor boundary

[`metric_specs.py`](metric_specs.py) remains the single registry for the six
market-implied targets. Its adapters map each frozen metric spec to semantic
TaskSpec v3 and an exact capability key. Static v2 and DuckDB-query v3 retain
different solver interfaces and output contracts even when they share the same
financial method.

[`semantic_capabilities.py`](semantic_capabilities.py) performs a sidecar-only
preflight before a new single-metric suite creates its output tree. It requires
the source bundle and all selected metric/interface capabilities to be
`portable_verified`, and cross-checks their accepted delivery aliases. Missing
family, method, interface, output, evidence, or alias identity fails closed.

The preflight is not serialized into a task and does not change task IDs,
manifests, runtime contracts, toolsets, release views or verifier truth. Replay
and verification of an accepted artifact continue through the artifact's frozen
contracts. Existing deliveries under `task_packages/deliveries/` remain
immutable.

## Existing interfaces

- combined IV + five Greeks: static JSON query/submit v2;
- six single metrics: static JSON query/submit v2;
- the same six metrics: read-only DuckDB query/submit v3.

All builders stage in a sibling directory, validate every leaf and suite,
perform recursive leakage and digest checks, and publish with one
non-overwriting atomic rename. Solver numerics and the pinned-QuantLib verifier
remain independent.

See the [artifact documentation](../../../task_packages/README.md) and
[packaging tests](../../../tests/packaging_analytic_and_implied_greeks_iv/README.md).
