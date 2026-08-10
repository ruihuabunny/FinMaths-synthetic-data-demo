# Agent task packages

Each task directory is an immutable accepted package with four source areas:

- `public/`: the Agent-visible DuckDB, prompt, effective runtime contract, and
  submission schema;
- `verifier/`: trusted pytest and pinned method configuration, hidden during an
  Agent run;
- `reference/`: an observable train/dev replay and its standard-library artifact;
- `authoring_private/`: parent/sample identity, canonical answer, and build/leakage
  reports plus a complete source-artifact digest manifest, never copied into an
  evaluation view.

`views/evaluation` contains only `manifest.json` plus `public/`.
`views/train_dev` additionally contains `reference/`; `views/authoring` contains
all four source views. Package verification checks both each source digest and
byte identity between every release-view copy and its source.

The checked-in golden package is
`bsm_market_implied_greeks_v1/bsm-mig-v1-1f1fc1880b42253725b118eb`. It is an
`ACCEPTED` D4 task, not a batch release: 8 underlyings × 2 live expiries × 5
strikes × call/put = 160 ordered rows, with exactly three relations in
`public/task.duckdb`. Its public logical checksum is
`eda816dd104fad3682c11b452237ddafe1d0b08646c23ec39a25f032a3430758`.

Rebuild and verify a single package with `scripts/package_bsm_greeks_task.py`;
the script refuses to overwrite an existing task identity. Phase F tooling in
`scripts/run_bsm_greeks_batch.py` parameterizes only the private nonnegative
selection seed, reuses one frozen parent, verifies every accepted package, and
exports a nine-field JSONL dataset. The full 100-task run and explicit
`RELEASED` promotion have not been performed.

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
