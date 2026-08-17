# Documentation index

## Current architecture and worked examples

- [DuckDB + QuantLib authoring pipeline](authoring_pipeline.md)
- [Implemented model-family and executable-capability boundary](../src/synthetic_derivatives/model_families/README.md)
- [Task-space design catalog and semantic TaskSpec v3](../src/synthetic_derivatives/task_space/README.md)
- [Family-aware curriculum](../src/synthetic_derivatives/curriculum/README.md)
- [Family-aware deterministic mutation](../src/synthetic_derivatives/mutation/README.md)
- [Deterministic ORM, task mutation and curriculum framework](financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
- [IV/Greeks/smile deterministic agent trajectory example](examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)

These documents explain the architecture, but the implementation status in the
root README and package-local READMEs takes precedence when a dated section is
stale.

## Plans and curricula

- [Implementation and migration plans](plans/README.md)
- [Target BSM curricula](curricula/README.md)

Plans and curricula intentionally include work that is not implemented. In
particular, L3 Monte Carlo, a second model family, broader cross-family
abstractions, and F2A arbitrage are not made executable by their documentation.
The identity/capability refactor is implemented only for the existing
`tdgbm_bsm` family; its current status is recorded in the root and package-local
READMEs above.

## Packaging evidence

Completed prompt, database-query, allocation, and delivery migrations are indexed
under [`task_packages/reports/`](../task_packages/reports/README.md). Portable
artifact semantics are documented in [`task_packages/README.md`](../task_packages/README.md).
