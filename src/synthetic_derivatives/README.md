# `synthetic_derivatives` package map

| Package | Current responsibility | Status |
|:---|:---|:---:|
| `authoring/` | Config parsing, exact interval P-dynamics, option-chain generation, DuckDB lifecycle | Implemented |
| `export/` | Immutable frozen parent to safe public Solver database | Implemented |
| `task_space/` | Seven-axis coordinates, compatibility and legacy migration | Minimal implementation |
| `tasks/` | BSM inputs, units, methods and canonicalization | Implemented for current BSM families |
| `mutation/` | Deterministic operators and lineage | Minimal implementation; F2A inactive |
| `curriculum/` | Stage/mastery sampling scheduler | Minimal implementation |
| `solver/analytic_and_implied_greeks_iv/` | Stdlib analytic BSM, fixed-schedule IV and market-implied Greeks | Implemented |
| `solver/mc/` | Planned L3 Monte Carlo boundary | Scaffold only |
| `verifier/` | Independent pinned-QuantLib BSM/IV/Greeks verification | Implemented |
| `packaging_analytic_and_implied_greeks_iv/` | Database projection, runtime, trusted tools, verifier leaves, views and portable suites | Implemented for v2/v3 BSM packaging |
| `training/` | Verified BSM package to nine-field JSONL | BSM-specific implementation |

The package layout is also a permission model. Importability inside the developer
repository does not mean the same modules are mounted for an Agent. Read the
closest `AGENTS.md` and local README before changing a package.

