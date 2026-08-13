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
`unsplit_shared_parent_snapshot` evaluation group. The prior immutable
`20260813_current_interface_100` delivery remains in place as the v1-interface
baseline and is not modified.

Convert a completed source run with:

```bash
.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root /tmp/<completed-run> \
  --output-root task_packages/deliveries \
  --delivery-id 20260813_prompt_v2_100 \
  --expected-task-count 100
```
