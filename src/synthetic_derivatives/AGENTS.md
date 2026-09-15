# Python package architecture

Keep permission boundaries as real module boundaries. Shared code is allowed only
for neutral contracts and serialization—not for numerical implementations that
would collapse Solver/verifier independence.

- `authoring/`: private generation, QuantLib, RNG and snapshot lifecycle.
- `export/`: frozen parent to independent public child.
- `task_space/`: coordinates and compatibility only.
- `tasks/`: public input/unit/method/canonicalization contracts only.
- `mutation/`: deterministic child specification and private lineage.
- `curriculum/`: sampling weights only; no task or reward mutation.
- `solver/`: restricted numerical implementations; no authoring/verifier imports.
- `verifier/`: independent trusted calculations; no Solver numerical imports.
- `packaging_analytic_and_implied_greeks_iv/`: source-package and portable-view
  materialization, trusted tools, leakage and release checks.
- `training/`: verified package to public dataset records.

Do not add a generic abstraction merely because future model families are planned.
The executable code is currently GBM–BSM-centered; new families require explicit
model identity, contracts, runtime, verifier and tests rather than flags threaded
through existing BSM code.

