# Solver runtime allowlist

This directory defines the dependency boundary for solver-visible BSM tasks.

Allowed runtime:

- the pinned Python interpreter and its standard library;
- task-declared trusted query and submission adapters with fixed call budgets;
- repository artifacts explicitly copied into the public solver bundle.

`duckdb==1.5.5` exists in the trusted query-adapter image, but the
`bsm_market_implied_greeks_v1` task overlay does not grant the Agent a raw
DuckDB connection. Effective capabilities are computed as the intersection of
`capabilities.global_v1.json` and `capabilities.bsm_greeks_v1.json`; the overlay
can only remove capabilities or reduce budgets.

Not allowed in the solver image:

- QuantLib, py_vollib, mibian, rateslib, or packaged pricing/Greek/IV APIs;
- authoring, verifier, mutation, private lineage, or hidden-oracle modules;
- dynamic package installation, network access, or undeclared filesystem reads.
- raw database connections, `ATTACH/COPY/INSTALL/LOAD`, and process spawning.

QuantLib remains available only to trusted authoring and verifier code. The task
contract must freeze formulas, conventions, dtype, operation order and canonical
serialization so the solver implementation and independent verifier can be
compared exactly.
