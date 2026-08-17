# Configuration catalog

| Directory | Responsibility |
|:---|:---|
| `generators/` | Authoring model, market conventions, entities, P/Q dependence, RNG and engine versions |
| `task_space/` | Seven-axis task coordinates and compatibility registry |
| `mutations/` | Allowed deterministic operators and change limits |
| `curricula/` | Stage definitions, mastery bands and sampling mixtures |
| `variants/` | Task method, units, canonicalization and output contracts |
| `task_packages/` | Source-package selectors and effective runtime profiles |
| `deliveries/` | Portable-suite allocation and release profiles |

Configuration is versioned declarative input. Current executable capability is
determined jointly by code, schemas, environment profiles and tests; a catalog
entry alone does not mean that a task family is implemented.

