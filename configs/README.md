# Configuration catalog

| Directory | Responsibility |
|:---|:---|
| `model_families/` | Implemented model-family identity, stochastic contract evidence IDs and derived `M` coordinate |
| `generators/` | Authoring model, market conventions, entities, P/Q dependence, RNG and engine versions |
| `task_space/` | Broad design catalog plus a separate exact executable-capability sidecar |
| `mutations/` | Allowed deterministic operators and change limits |
| `curricula/` | Stage definitions, mastery bands and sampling mixtures |
| `variants/` | Task method, units, canonicalization and output contracts |
| `task_packages/` | Source-package selectors and effective runtime profiles |
| `deliveries/` | Portable-suite allocation and release profiles |

Configuration is versioned declarative input. Current executable capability is
determined jointly by code, schemas, environment profiles and tests; a catalog
entry alone does not mean that a task family is implemented.

The family-aware curriculum and mutation configs are additive new paths.
`adaptive_v2.json` and `deterministic_v2.json` remain unchanged legacy
contracts; they are not silently reinterpreted as semantic TaskSpec v3.
