# Agent task packages

Each task directory is an immutable accepted package with four source areas:

- `public/`: the Agent-visible DuckDB, minimal routing prompt, effective runtime
  contract, and submission schema;
- `verifier/`: package-local trusted pytest, its focused runtime, pinned Python
  dependencies, and method configuration, hidden during an Agent run;
- `reference/`: an observable train/dev replay and its standard-library artifact;
- `authoring_private/`: parent/sample identity, canonical answer, and build/leakage
  reports plus a complete source-artifact digest manifest, never copied into an
  evaluation view.

`views/evaluation` contains only `manifest.json` plus `public/`.
`views/train_dev` additionally contains `reference/`; `views/authoring` contains
all four source views. Package verification checks both each source digest and
byte identity between every release-view copy and its source.

For the minimized BSM-greeks interface, `public/prompt.md` is deliberately only
a router to four public sources of truth:

- `query_greeks_task_contract_v1` owns all mathematical and numerical semantics;
- `query_greeks_task_inputs_v1` owns the complete canonical input rows and order;
- `public/submission.schema.json` owns the submission shape and field syntax;
- `public/runtime_contract.json` owns tools, limits, permissions, and budgets.

The prompt does not repeat BSM formulas, the IV algorithm, Greek units, row
columns, serialization rules, or runtime policy. A package build must reject
drift or contradiction among those source contracts rather than resolve it with
extra prompt prose.

The package root and `views/authoring` are independently deliverable: their
verifier imports no project module and needs only Python plus the packages in
`verifier/requirements.lock`. From either directory, run it with an absolute
submission path:

```bash
BSM_GREEKS_SUBMISSION=/absolute/path/submission.json \
  python -B -m pytest -q verifier
```

The evaluation and train/dev views intentionally omit the trusted verifier;
the future sandbox may mount it separately without needing the authoring
repository.

The checked-in golden package is
`bsm_market_implied_greeks_v1/bsm-mig-v1-bde472c5cb0ca8a660314c9e`. It is an
`ACCEPTED` D4 task, not a batch release: 8 underlyings × 2 live expiries × 5
strikes × call/put = 160 ordered rows, with exactly three relations in
`public/task.duckdb`. Its public logical checksum is
`0630a0216e36d5f8785d9f1e0dfd8d2e2445038010895f0f07221eb6409aeec3`.

New materializations bind a `solver_interface_digest` into the stable task ID.
That digest covers the solver-interface contract version, rendered prompt,
method contract, submission schema, and effective runtime contract. The version
owns the trusted-adapter and input-mapping surface. Changing any of those creates
a new task directory; an `ACCEPTED` package is never edited in place. The
checked-in ID above was promoted only after the newly identified package
passed replay, verifier, leakage, release-view, and repository-isolation checks.

Rebuild and verify a single package with `scripts/package_bsm_greeks_task.py`;
the script refuses to overwrite an existing task identity. Phase F tooling in
`scripts/run_bsm_greeks_batch.py` parameterizes only the private nonnegative
selection seed, reuses one frozen parent, verifies every accepted package, and
exports a nine-field JSONL dataset. A Git-ignored local 100-task run dated
2026-08-10 was built with the predecessor verbose-prompt interface. Because the
current minimal prompt changes `solver_interface_digest` and task IDs, that run
is historical only; a current-interface 100-task rebuild, split audit, and
explicit `RELEASED` promotion have not been performed.

Use scratch output paths for a rebuild:

```bash
.venv/bin/python scripts/package_bsm_greeks_task.py \
  --base-parent-config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  --output-root /tmp/bsm-greeks-packages \
  --build-status ACCEPTED
```

A small Phase F smoke run is intentionally separate from a reviewed 100-task
run:

```bash
.venv/bin/python scripts/run_bsm_greeks_batch.py \
  --run-root /tmp/bsm-greeks-batch-smoke \
  --dataset-copy /tmp/bsm-greeks-dataset-smoke \
  --task-count 2
```
