# `synthetic_derivatives` package map

| Package | Current responsibility | Status |
|:---|:---|:---:|
| [`model_families/`](model_families/README.md) | Stochastic-family identities, `model_family_id` ↔ `M` validation, legacy identity adapters and fail-closed executable-capability gating | Implemented for `tdgbm_bsm` only |
| [`authoring/`](authoring/README.md) | Config parsing, exact interval P-dynamics, option-chain generation, explicit family backend dispatch and DuckDB lifecycle | Implemented |
| `export/` | Immutable frozen parent to safe public Solver database | Implemented |
| [`task_space/`](task_space/README.md) | Seven-axis design-catalog compatibility and semantic TaskSpec v3 identity | Implemented as a non-executable catalog |
| `tasks/` | BSM inputs, units, methods and canonicalization | Implemented for current BSM families |
| [`mutation/`](mutation/README.md) | Legacy deterministic mutation plus family/M-immutable semantic lineage | Implemented for current BSM paths; F2A inactive |
| [`curriculum/`](curriculum/README.md) | Legacy coordinate scheduler plus capability-gated family-local sampling | Implemented for current portable BSM paths |
| `solver/analytic_and_implied_greeks_iv/` | Stdlib analytic BSM, fixed-schedule IV and market-implied Greeks | Implemented |
| `solver/mc/` | Planned L3 Monte Carlo boundary | Scaffold only |
| `verifier/` | Independent pinned-QuantLib BSM/IV/Greeks verification | Implemented |
| [`packaging_analytic_and_implied_greeks_iv/`](packaging_analytic_and_implied_greeks_iv/README.md) | Database projection, capability preflight, runtime, trusted tools, verifier leaves, views and portable suites | Implemented for v2/v3 BSM packaging |
| `training/` | Verified BSM package to nine-field JSONL | BSM-specific implementation |

The package layout is also a permission model. Importability inside the developer
repository does not mean the same modules are mounted for an Agent. Read the
closest `AGENTS.md` and local README before changing a package.

The separation between `task_space` and `model_families` is intentional:
`task_space` answers whether coordinates are structurally compatible;
`model_families` answers whether one exact semantic task has declared
implementation evidence. Runtime profiles under `environments/` apply a further
Agent-visible permission gate. No layer infers a larger capability from another
layer's catalog entry.
