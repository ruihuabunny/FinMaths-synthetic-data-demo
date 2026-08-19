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

## Metric leaf verifier refactor

Phases 0–7 of the metric leaf-verifier refactor are implemented. The
[`metric_leaf_verifier_runtime/`](metric_leaf_verifier_runtime/) package
mechanically separates input/domain models, DuckDB schema and logical identity,
and the independent QuantLib oracle. It preserves the USD money-market `Q`
measure, USD money-market-account numeraire, `Actual365Fixed` time axis,
binary64 bisection and Greek evaluation, unit scaling, and canonical decimal
boundary of the accepted runtime. Typed profiles bind each protocol's task,
database, metric, method, and submission identities, while explicit static-v2
and DuckDB-query-v3 submission modules retain ordered and unordered row
semantics respectively.

The renderer deterministically builds a 15-file verifier tree
with a small compatibility facade, private shared modules, one frozen protocol
profile, and exactly one selected submission parser. `metric_verifier.py` now
uses that renderer for both protocols, and `portable_metric_suite.py` consumes
the renderer-owned 15-path inventory for exact-tree validation, artifact
digests, and `verifier_only` visibility records, including every nested
`_runtime/*.py` module. Generated bundles are recursively leak-scanned,
relocated, and executed under Python isolated mode. The accepted deliveries and
their monolithic runtime copies remain unchanged. The repository-side
monolithic v2 template and v3 suffix/`compile`/`exec` assembly have been
deleted; all new leaves are produced only by the modular renderer. See the
[runtime architecture note](metric_leaf_verifier_runtime/README.md).

The frozen `20260819` static-v2 and DuckDB-query-v3 runtimes are also compared
with production-rendered modular runtimes across all 48 assignments. Parsed
records, logical checksums, canonical truth, valid verification, systematic
invalid-submission outcomes, protocol ordering behavior, exception causes, and
dependency-pin failures must agree exactly. This equivalence proof reads but
does not modify the accepted deliveries.

Phase 6 materialized two new 24-task deliveries from the same frozen source run
and assignment policy:

- `20260819_metric_6x4_static_v2_modular`;
- `20260819_metric_6x4_db_query_v3_modular`.

Their public databases, logical checksums, source/target assignments, oracle
configs, and expected answers match the corresponding `*_rerun` baselines.
Only the modular verifier trees and the manifests that bind them changed. The
exact identities and changed-path audit are recorded in the
[delivery comparison report](../../../task_packages/reports/bsm_metric_leaf_verifier_modular_runtime_migration.md).

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
