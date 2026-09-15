# Task-space design catalog

This package owns the seven coordinates `(L, P, M, A, D, R, F)`, deterministic
coordinate ordering, legacy six-axis migration, and structural compatibility
rules. [`derivatives_v2.json`](../../../configs/task_space/derivatives_v2.json)
is a broad design catalog: a matching rule means that a combination is
well-formed for planning, not that generator, solver, verifier, package, or
runtime support exists.

`TaskSpec` remains the accepted legacy identity. `TaskSpecV3` is an additive
semantic identity that separates:

- `model_family_id` — probability-model family;
- `task_family_id` — type of work;
- `task_kind_id` — requested target;
- `solver_interface_id` — Agent-facing ABI;
- `method_id` and `output_contract_id` — numerical and serialization contracts;
- coordinates and frozen snapshot identity.

Semantic TaskSpec v3 is unrelated to package/runtime/toolset or DuckDB-query
protocols that also use a `v3` version label. Existing tasks are mapped only by
explicit adapters; this package never guesses missing identities or rewrites an
accepted artifact.

Executable capability is deliberately outside this boundary. The fail-closed
registry and BSM legacy adapters live in
[`model_families/`](../model_families/README.md). A task must pass both design
compatibility and the exact capability key before any runtime-facing workflow
can admit it.

Relevant contracts and tests:

- [`task-v3.schema.json`](../../../schemas/task-v3.schema.json)
- [`test_task_space.py`](../../../tests/unit/test_task_space.py)
- [`test_task_v3_identity.py`](../../../tests/unit/test_task_v3_identity.py)
- [`test_catalog_runtime_separation.py`](../../../tests/integration/test_catalog_runtime_separation.py)
