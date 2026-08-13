# BSM market-implied Greeks prompt v2 change report

Date: 2026-08-13

## Outcome

The `bsm_market_implied_greeks_v1` task family now uses a v2 solver interface
with a complete public prompt, two data-only trusted queries, one submission
tool, a three-relation task database, and a physically isolated evaluation
view. The formula-bearing public contract query, relation, and payload were
removed.

The new immutable delivery is:

```text
task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100
```

It contains 100 unique `bsm-mig-v2-*` tasks and has status
`PORTABLE_VERIFIED`. The existing
`20260813_current_interface_100` delivery was not modified.

## Changed files

Configuration, schemas, and solver capabilities:

```text
configs/task_packages/bsm_market_implied_greeks_v1.json
configs/variants/bsm_market_implied_greeks_v1.json
environments/solver/capabilities.bsm_greeks_v1.json
environments/solver/capabilities.global_v2.json
schemas/agent-task-package-v2.schema.json
schemas/agent-task-portable-toolset-v2.schema.json
schemas/agent-task-runtime-contract-v2.schema.json
schemas/bsm-greeks-submission-v2.schema.json
```

Task packaging, runtime, verifier, and export source:

```text
llm_solutions/run_bsm_market_implied_greeks.py
scripts/run_bsm_greeks_batch.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/bsm_market_greeks_verifier_runtime.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/contracts.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/database.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/leakage.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/package.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_delivery.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_tools.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/prompt_renderer.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/reference_solver.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/runtime.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/trajectory.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/views.py
src/synthetic_derivatives/tasks/bsm_market_greeks.py
src/synthetic_derivatives/training/bsm_market_greeks.py
```

Focused tests:

```text
tests/packaging_analytic_and_implied_greeks_iv/conftest.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_database_and_replay.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_dataset_export.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_negative_submissions.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_package_contract.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_prompt_runtime_drift.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_release_views.py
tests/packaging_analytic_and_implied_greeks_iv/test_portable_delivery.py
tests/unit/test_hy3_chat_runner.py
tests/unit/test_task_space.py
```

Task-package documentation was updated in `README.md`,
`task_packages/README.md`, `environments/solver/README.md`, and the relevant
authoring/export/LLM-runner READMEs. The v1 schemas and v1 global capability
profile remain unchanged so that the prior immutable delivery keeps its original
interface.

## Version identities

- Task version: `2.0.0`
- Task ID prefix: `bsm-mig-v2-`
- Solver interface: `bsm-market-implied-greeks-solver-interface-v2`
- Package schema: `agent-task-package-v2.0.0`
- Runtime schema: `agent-task-runtime-contract-v2.0.0`
- Submission schema: `bsm-market-implied-greeks-submission-v2.0.0`
- Task database schema: `bsm-greeks-task-duckdb-v2.0.0`
- Portable toolset: `bsm-greeks-portable-toolset-v2.0.0`
- Tool-host protocol: `static-json-query-schema-submit-v2`
- Delivery schemas: `bsm-greeks-portable-delivery-{batch,task}-v2.0.0`
- Delivery ID: `20260813_prompt_v2_100`

The private numerical method and QuantLib oracle identities remain v1 because
their mathematical semantics did not change.

## Validation commands and results

Focused source/unit/integration regression:

```text
.venv/bin/python -m pytest -q \
  tests/packaging_analytic_and_implied_greeks_iv \
  tests/integration/test_bsm_greeks_verifier.py \
  tests/unit/test_bsm_greeks_contract.py \
  tests/unit/test_hy3_chat_runner.py
```

Result: `121 passed in 65.70s`.

Whole-repository regression:

```text
.venv/bin/python -m pytest -q
```

Result: `300 passed in 115.55s`.

One-task production-parent smoke:

```text
.venv/bin/python scripts/package_bsm_greeks_task.py \
  --base-parent-config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  --output-root /tmp/bsm-greeks-v2-smoke1 \
  --build-status ACCEPTED
```

Result: accepted task `bsm-mig-v2-99dc8771b992b694550227b7`.
The batch selector rejected seeds 0 through 6 at the private rounding-boundary
publication gate and selected seed 7 without weakening the guard.

Two-task source and portable smoke:

```text
.venv/bin/python scripts/run_bsm_greeks_batch.py \
  --run-root /tmp/bsm-greeks-v2-run2 \
  --dataset-copy /tmp/bsm-greeks-v2-dataset2 \
  --task-count 2 --starting-sampling-seed 0

.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root /tmp/bsm-greeks-v2-run2 \
  --output-root /tmp/bsm-greeks-v2-deliveries \
  --delivery-id prompt-v2-smoke2 --expected-task-count 2
```

Result: two unique tasks, `PORTABLE_VERIFIED`; both reference solvers replayed
twice with byte-identical submissions; both package-local verifier suites passed
`3/3` tests.

Full 100-task build and delivery:

```text
.venv/bin/python scripts/run_bsm_greeks_batch.py \
  --run-root /tmp/bsm-greeks-v2-run100 \
  --dataset-copy /tmp/bsm-greeks-v2-dataset100 \
  --task-count 100 --starting-sampling-seed 0

.venv/bin/python scripts/package_bsm_greeks_delivery.py \
  --run-root /tmp/bsm-greeks-v2-run100 \
  --output-root task_packages/deliveries \
  --delivery-id 20260813_prompt_v2_100 \
  --expected-task-count 100
```

Result: 100 accepted unique tasks from 698 deterministic candidates; 598
candidates were rejected only by the private half-quantum guard. All 100 source
packages passed byte-identical replay and exact QuantLib canonical comparison.
The resulting delivery is `PORTABLE_VERIFIED`, and all 100 copied package-local
verifier suites passed.

## Leakage and isolation result

- All 100 task databases contain exactly
  `metadata.public_task`, `solver_visible.underlying_market_inputs`, and
  `solver_visible.option_quote_inputs`.
- Every task has exactly 8 underlying rows and 160 option rows.
- No formula-contract relation or `contract.json` payload exists in the new
  delivery.
- Semantic leakage scans passed for all public files and all actual underlying
  and option query payloads.
- Formula-token, answer-field, hidden-path, symlink, and label-only fake-view
  mutation tests passed.
- All 100 evaluation views passed the exact physical file allowlist.
- Prompt, runtime, and submission schema each have one shared content digest
  across the 100 tasks; all 100 selected underlying subsets are unique.
- The batch has one shared parent snapshot and uses
  `unsplit_shared_parent_snapshot`.
- The per-package frozen-parent before/after byte-digest guard passed throughout
  the 100-task build.

Representative evaluation-view listing:

```text
manifest.json
public/prompt.md
public/runtime_contract.json
public/submission.schema.json
```

The raw task database, trusted-tool payloads, verifier, reference output, and
authoring-private artifacts are physically outside this solver-mounted tree.
