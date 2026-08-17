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

Do not confuse these Agent runtime capabilities with the repository's semantic
[`executable-capability sidecar`](../configs/task_space/README.md). The sidecar
proves that one exact model/task/method/interface combination has implementation
evidence and may enter a package or scheduler workflow. Environment profiles
then apply the narrower execution permission for a concrete task. Passing one
layer never bypasses the other, and neither layer derives capability from the
broad task-space design catalog.
