# Documentation index

## Current architecture and worked examples

- [DuckDB + QuantLib authoring pipeline](authoring_pipeline.md)
- [Deterministic ORM, task mutation and curriculum framework](financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
- [IV/Greeks/smile deterministic agent trajectory example](examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)

These documents explain the architecture, but the implementation status in the
root README and package-local READMEs takes precedence when a dated section is
stale.

## Plans and curricula

- [Implementation and migration plans](plans/README.md)
- [Target BSM curricula](curricula/README.md)

Plans and curricula intentionally include work that is not implemented. In
particular, L3 Monte Carlo, wider model-family refactoring, and F2A arbitrage are
not made executable by their documentation.

## Packaging evidence

Completed prompt, database-query, allocation, and delivery migrations are indexed
under [`task_packages/reports/`](../task_packages/reports/README.md). Portable
artifact semantics are documented in [`task_packages/README.md`](../task_packages/README.md).

