# Agent task packages

A source-package task produced under `runs/` is an immutable accepted package
with four source areas:

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

The maintained task-data delivery is
`deliveries/bsm_market_implied_greeks_v1/20260813_current_interface_100`.
It contains 100 unique portable D4 tasks. Each task has 8 underlyings × 2 live
expiries × 5 strikes × call/put = 160 ordered rows, with exactly three
relations in `public/task.duckdb`. All 100 tasks share one parent snapshot, so
the batch is declared as one unsplit evaluation group rather than randomly
dividing related children across train/validation/test.

New materializations bind a `solver_interface_digest` into the stable task ID.
That digest covers the solver-interface contract version, rendered prompt,
method contract, submission schema, and effective runtime contract. The version
owns the trusted-adapter and input-mapping surface. Changing any of those creates
a new task directory; an `ACCEPTED` source package is never edited in place.
Moving an observably identical adapter binding into a portable delivery changes
only delivery identity and does not change the mathematical task ID.

Rebuild and verify a single source package with
`scripts/package_bsm_greeks_task.py`; the script refuses to overwrite an
existing task identity. Phase F tooling in `scripts/run_bsm_greeks_batch.py`
parameterizes only the private nonnegative selection seed, reuses one frozen
parent, verifies every accepted package, and exports a nine-field JSONL dataset.
The current-interface 100-task source run completed on 2026-08-13; its portable
envelope is `PORTABLE_VERIFIED`, while explicit `RELEASED` promotion remains a
separate release-policy decision.

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

## Portable data-only delivery

A completed batch can be converted into agent-task deliveries that do not
depend on this repository's Python source. Each delivered task contains the
four unchanged `public/` artifacts, package-local `verifier/`, and a
host-only declarative `trusted_tools/` directory. The latter binds the two
query tools to canonical JSON payloads exported from `public/task.duckdb` and
binds the submission tool to the public schema. A receiving platform only
needs to implement the declared generic
`static-json-query-schema-submit-v1` host protocol; the sandbox itself is not
part of the delivery.

The converter excludes reference answers, trajectories, authoring-private
files, selector configuration and readable seed/run names, generated datasets,
and release views. It preserves the existing task IDs because the observable
tool interface and returned values do not change. The source packages remain
immutable `ACCEPTED` artifacts; the outer delivery has its own
`PORTABLE_VERIFIED` status and complete file-digest and visibility manifests.

```bash
.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root runs/bsm_market_implied_greeks/<completed-run> \
  --output-root task_packages/deliveries \
  --delivery-id <delivery-id> \
  --expected-task-count 100
```
