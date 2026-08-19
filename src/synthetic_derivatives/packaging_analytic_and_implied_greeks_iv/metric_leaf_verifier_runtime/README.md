# Modular metric leaf verifier runtime

This package is the authoring source for the trusted verifier embedded in each
portable BSM single-metric task. It is deliberately BSM-specific and supports
the two accepted protocols: ordered static-v2 submissions and row-key-aligned
DuckDB-query-v3 submissions.

## Architecture

| Module | Responsibility |
|:---|:---|
| `models.py` | Input records, binary64/date conversion, domain checks, and canonical decimal syntax |
| `database.py` | Dependency pins, DuckDB schema and identity checks, public-row loading, file digest, and logical checksum |
| `oracle.py` | Independent QuantLib pricing, fixed 80-step IV inversion, Greeks, unit scaling, and half-even canonicalization |
| `submission_static_v2.py` | Exact 160-row ordered static-v2 contract and comparison |
| `submission_duckdb_v3.py` | Strict row identity, duplicate rejection, key alignment, and order-insensitive v3 comparison |
| `profiles.py` | Frozen protocol and metric identities selected before rendering |
| `renderer.py` | Deterministic self-contained leaf tree and its authoritative nested-file inventory |

The generated `verifier/runtime.py` is only a stable facade. Its private
`verifier/_runtime/` modules use relative imports and contain one selected
submission implementation; they never import this repository package at task
evaluation time.

## Why every leaf is self-contained

Portable task leaves may be copied outside this repository and evaluated with
the repository source absent from `PYTHONPATH`. Repeating the small verifier
bundle in each leaf is therefore an intentional trust and portability boundary,
not shared-runtime duplication to deduplicate. Every trusted file is included
in the delivery manifest artifact map and classified `verifier_only`.

Accepted delivery trees are immutable. The old monolithic `runtime.py` copies
inside the frozen `20260819_*_rerun` deliveries remain historical baselines;
only a new delivery identity may contain a newly rendered modular bundle.

## Change rules

- Keep Solver numerics and reference answers outside this package.
- Keep the dependency pins, IV bracket, 80 bisection steps, Greek units,
  binary64 boundary, eight-decimal `ROUND_HALF_EVEN` output, and exact equality
  unchanged unless a separate versioned contract migration is approved.
- Add a generated file only through `METRIC_LEAF_VERIFIER_FILENAMES`, then bind
  it in package-tree, digest, visibility, relocation, and tamper tests.
- Select a protocol in `profiles.py`/`renderer.py`; do not use dynamic execution,
  suffix concatenation, global reassignment, or a runtime protocol dispatcher.

Focused tests live under
`tests/packaging_analytic_and_implied_greeks_iv/test_metric_leaf_verifier_*.py`.
