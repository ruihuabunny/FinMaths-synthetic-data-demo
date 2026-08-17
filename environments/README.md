# Runtime boundaries

| Directory | Role |
|:---|:---|
| `authoring/` | Pinned trusted authoring dependencies, including QuantLib and DuckDB |
| `solver/` | Global and task-specific capability profiles for Agent-visible execution |
| `verifier/` | Pinned hidden verifier dependencies used for independent reconstruction |

Read the [Solver allowlist](solver/README.md) and
[trusted verifier contract](verifier/README.md) before changing runtime or
package visibility. Capability JSON, dependency locks, mounted views and trusted
tool budgets jointly define the runtime; source placement alone grants nothing.

