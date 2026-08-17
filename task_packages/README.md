# Agent task packages

The maintained `bsm_market_implied_greeks_v1` package uses the v2 solver
interface while preserving the original European BSM mathematical task. A
source package contains four authoring areas:

- `public/`: the hidden-host task DuckDB, complete solver prompt, runtime
  contract, and submission schema;
- `verifier/`: the self-contained trusted QuantLib 1.39 verifier;
- `reference/`: observable train/dev replay artifacts;
- `authoring_private/`: selection provenance, oracle answer, and build reports.

Only `views/evaluation` is mountable as the solver filesystem. Its exact tree
is:

```text
manifest.json
public/prompt.md
public/runtime_contract.json
public/submission.schema.json
```

The raw DuckDB, trusted payloads, verifier, reference solver, trajectory, and
authoring-private files are physically absent from that view. The solver gets
data only through these single-call tools:

- `query_greeks_underlying_market_v2` returns 8 public spot/pricing-context
  rows;
- `query_greeks_option_quotes_v2` returns 160 canonically ordered public option
  rows;
- `submit_greeks_submission_v2` accepts one complete canonical submission.

`public/prompt.md` owns the task objective, BSM/IV conventions, Greek units,
join keys, allowed resources, output rules, and tool schedule. Runtime JSON owns
only capabilities and budgets; the schema owns only output syntax; query
responses own only public values and order. No solver-visible surface contains
pricing or Greek formulas.

The task DuckDB has exactly these relations:

```text
metadata.public_task
solver_visible.underlying_market_inputs
solver_visible.option_quote_inputs
```

The trusted verifier reconstructs Decimal bid/ask midpoints from those public
relations, casts once to binary64, performs exactly 80 bisection updates on
`[1e-6, 5.0]`, evaluates unit Greeks at the unrounded root with
`QuantLib.AnalyticEuropeanEngine`, canonicalizes to 8 decimals with
`ROUND_HALF_EVEN`, and requires exact string equality. Publication separately
requires the standard-library reference implementation to agree canonically and
rejects unrounded values within `1e-11` of a half-quantum rounding boundary.

From a source-package root (or its authoring view), run the self-contained
verifier with:

```bash
BSM_GREEKS_SUBMISSION=/absolute/path/submission.json \
  python -B -m pytest -q verifier
```

Build one source package in scratch space with:

```bash
.venv/bin/python scripts/package_bsm_greeks_task.py \
  --base-parent-config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  --output-root /tmp/bsm-greeks-packages \
  --build-status ACCEPTED
```

Build a two-task source smoke batch with:

```bash
.venv/bin/python scripts/run_bsm_greeks_batch.py \
  --run-root /tmp/bsm-greeks-batch-smoke \
  --dataset-copy /tmp/bsm-greeks-dataset-smoke \
  --task-count 2
```

## Portable delivery

Each portable task keeps the four-file `evaluation_view/` physically separate
from the host-only `task.duckdb` and `trusted_tools/`, and from `verifier/`.
`trusted_tools/payloads/underlyings.json` and `options.json` are canonical
exports of the two database relations. The generic host protocol is
`static-json-query-schema-submit-v2`.

The current v2 delivery is:

```text
deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100
```

It contains 100 unique `bsm-mig-v2-*` tasks in one
`unsplit_shared_parent_snapshot` evaluation group and remains the immutable
legacy combined IV-and-Greeks delivery.

Convert a completed source run with:

```bash
.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root /tmp/<completed-run> \
  --output-root task_packages/deliveries \
  --delivery-id 20260813_prompt_v2_100 \
  --expected-task-count 100
```

## Single-metric 6×4 suite

The single-metric delivery builder projects 24 distinct accepted v2 source
databases into six targets (`iv`, `delta`, `gamma`, `vega_1volpt`,
`theta_1calendar_day`, and `rho_1pct`), with four independent tasks per target.
Each source database is used once across the whole suite. The projection changes
only task/snapshot interface identity; the public market, contract, and quote
content is preserved.

Build the frozen suite with:

```bash
.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root runs/bsm_market_implied_greeks/20260814_24_tasks_prompt_v2_parent_seed_20260806_selector_seed_0 \
  --output-root task_packages/deliveries \
  --delivery-id 20260814_metric_6x4_unique_db \
  --profile configs/deliveries/bsm_market_implied_metric_suite_6x4_v1.json \
  --allocation-id 20260814_metric_6x4_v1 \
  --expected-source-task-count 24
```

The builder validates all 24 leaves and all cross-target uniqueness constraints
in a sibling staging directory, then publishes the complete suite with one
non-overwriting rename.

### DuckDB-query v3 migration

The breaking v3 solver protocol is published as a new delivery and does not
modify the static-JSON suite above. Each v3 leaf keeps `task.duckdb` as its only
market-input source, exposes it only through `query_public_duckdb_v3`, and
contains only `trusted_tools/toolset.json` under `trusted_tools/`. The public
submission is sent once through `submit_greeks_submission_v3`; row order has no
meaning, while the verifier requires the exact public `row_id` set and compares
rows after key alignment.

Repackage the same frozen 24 source markets with:

```bash
.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root runs/bsm_market_implied_greeks/20260814_24_tasks_prompt_v2_parent_seed_20260806_selector_seed_0 \
  --output-root task_packages/deliveries \
  --delivery-id 20260814_metric_6x4_db_query_v3 \
  --profile configs/deliveries/bsm_market_implied_metric_suite_6x4_v1.json \
  --allocation-id 20260814_metric_6x4_v1 \
  --expected-source-task-count 24 \
  --metric-protocol v3-duckdb-query
```

The v3 builder validates all SQL-tool, runtime, database, submission, verifier,
manifest, uniqueness, and no-payload bindings before the atomic publish.

Generate one authoring-side reference trajectory for each metric without adding
reference files to the portable leaves:

```bash
.venv/bin/python scripts/package_bsm_metric_v3_reference_trajectories.py \
  --suite-root task_packages/deliveries/bsm_market_implied_metric_suite_v1/20260814_metric_6x4_db_query_v3 \
  --output-directory examples/trajectories/bsm_market_implied_metric_db_query_v3
```

The frozen contract, provenance, validation evidence, and release digests are
recorded in `reports/bsm_public_duckdb_query_v3_migration.md`.

## Reports

Migration handoffs and completed validation evidence are indexed in
[`reports/README.md`](reports/README.md).
