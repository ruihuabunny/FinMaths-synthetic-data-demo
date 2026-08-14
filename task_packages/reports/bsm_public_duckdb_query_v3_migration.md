# BSM public DuckDB query v3 migration report

Date: 2026-08-14

## Outcome

The `bsm_market_implied_metric_suite_v1` delivery now has a breaking v3
trusted-tool protocol in a new immutable 6-target by 4-assignment suite:

```text
task_packages/deliveries/bsm_market_implied_metric_suite_v1/
  20260814_metric_6x4_db_query_v3
```

The suite contains 24 independently bound tasks and has status
`PORTABLE_SUITE_VERIFIED`. Each task has one public market-input source of
truth, `task.duckdb`; the v3 leaves contain no static JSON query payloads.

This migration does not change the BSM/IV/Greek mathematics, method IDs,
units, canonicalization, frozen market content, or source assignment. It does
not migrate the 100-task combined-Greeks family, regenerate market data, add
deployment infrastructure, commit, or push.

## Frozen v3 protocol

The public ABI is frozen as follows:

- Host protocol: `read-only-duckdb-query-schema-submit-v3`.
- Query tool: `query_public_duckdb_v3`, with 1 to 10 calls.
- Submission tool: `submit_greeks_submission_v3`, with exactly one call and no
  later query.
- Accepted statements: one restricted `SELECT`, native `SHOW TABLES`, or
  restricted `DESCRIBE` statement.
- SQL/result limits: 20,000 SQL characters, 1,000 result rows, 1 MiB encoded
  result, 5 seconds, and 256 MiB DuckDB memory.
- Query response: exactly `columns`, `rows`, `row_count`, and `truncated`, with
  typed deterministic JSON serialization.
- Solver limits: 1 GiB memory, 600 seconds, 1 vCPU, and a 5 MiB submission.
- Submission rows: unordered `row_id` identity set; duplicates, missing IDs,
  and extra IDs are rejected; verification key-aligns before exact comparison.

The package, runtime-contract, and portable-toolset schemas are v3. The six
task interfaces, structural submission schemas, task databases, and verifiers
are v2 because those are the first breaking versions of the single-metric task
ABI. The financial variant and oracle method identities remain v1 because the
mathematical object is unchanged.

## Implementation

The migration adds:

- an AST-validated, binder-checked DuckDB query host in
  `duckdb_query_tools.py`;
- v3 toolset generation and runtime adapter in `portable_tools_v3.py`;
- parent-hosted trusted-tool RPC, so solver code receives a narrow proxy and
  never receives a database or package path;
- structural v2 submission schemas for IV, delta, gamma, vega, theta, and rho;
- v3 runtime/package/toolset schemas and capability profiles;
- v3 prompt rendering that publishes the financial and numerical contract but
  not table, column, join-key, formula, answer, or fixed row-count hints;
- an order-insensitive v3 exact verifier runtime;
- a v3 reference solver and observable reference trajectories;
- explicit v2/v3 package and runner dispatch, including rejection of a legacy
  static-JSON metric suite by the v3 runner path.

The SQL boundary does not rely on a regex-only filter. It validates the parsed
statement and CTE scope, referenced schemas/relations/table functions, DuckDB
binder plan, database digest, frozen public relation set, extension/macro
catalogue, resource accounting, and result encoding. Tests reject writes,
multiple statements, `ATTACH`, `COPY`, `INSTALL`, `LOAD`, external readers,
filesystem paths, URLs, non-allowlisted relations, budget overflow, duplicate
submission, and post-submission queries.

## Frozen source and legacy immutability

The 24 tasks were repackaged from the existing completed run only:

```text
runs/bsm_market_implied_greeks/
  20260814_24_tasks_prompt_v2_parent_seed_20260806_selector_seed_0
```

Its frozen `run_summary.json` SHA-256 is:

```text
bcc67c31640bc6f0719eeea003da78204e7d6093f35b83aac05c73dd3b25aa09
```

No underlying or option generator was run. All 24 v3 assignments have the same
source-task assignment and market-content digest as the legacy 6x4 suite. After
matching each assignment and aligning by `row_id`, all 24 v3 metric values and
per-row statuses are exactly equal to their v2 counterparts.

The legacy delivery directories have no Git diff. Their frozen Git tree IDs
and repeated aggregate audits are:

```text
20260813_prompt_v2_100
  tree:   691b57334a4a44c0ff327c4d5f338695ee1f7702
  audit:  ff3b43517afb098a12b70c3b1026169b9e5d0f797dd67a5bc7a11fc27c86fe08

20260814_metric_6x4_unique_db
  tree:   8d847ba6c548b8fa461917d1ec6267de7b557e9c
  audit:  13e71ee6a759bb77d0d902148c02eed5874cb126f73c08a8723e75f36cd8d3b7
```

## Regenerated delivery

The final suite identity is:

```text
suite schema:       bsm-market-metric-suite-v2.0.0
suite id:           bsm-market-metric-suite-v2-8c67c3c32a4307e14dd3e24f
delivery status:    PORTABLE_SUITE_VERIFIED
assignment digest:  93b0134ed0931d6c860451a29127201cdb8ed359d9aac0e7a8b03063d7eb1a7b
suite manifest:     3bfc244975c0534ceda1841c73df311058c5761db9329df767180ff7eab08e44
tree audit:         a5ee0c839fb0c39b059a68dd5a3eb204d990cf427e667ba32e17c07a64da5be1
```

The target order is IV, delta, gamma, vega per 1 vol point, theta per calendar
day, and rho per 1 percentage point, with four tasks for each target. Source
task, source database, market content, derived task, and derived database
unique counts are all 24.

The tree has 415 files, including exactly 24 `task.duckdb` files, 24 leaf
manifests, and 24 toolsets. It contains no `payloads/` directory,
`options.json`, `underlyings.json`, `payload_path`, or legacy v2 query-tool
name.

## Reference trajectories

Six representative reference trajectories are published in:

```text
examples/trajectories/bsm_market_implied_metric_db_query_v3
```

There is one JSONL trajectory for every target. Each was replayed through the
parent-hosted v3 tool proxy and exact verifier, uses three query calls followed
by one submission, and contains only observable events. The trajectory manifest
SHA-256 is:

```text
64cd96ecc49217129b70159a451d082081f1ef78e3a6c17f3bba689bfca29ee9
```

## Public-only LLM rollout

An independent Codex agent was provided staged copies of only one final task's
public prompt, public runtime contract, and public submission schema, and was
explicitly prohibited from reading the repository, database file, delivery
manifest, verifier, reference solver, or reference output. During execution,
the normal audited solver runtime and parent-hosted trusted-tool proxy enforced
the same production boundary.

The solver dynamically queried `information_schema`, inferred the public
relations and join from returned names, types, and values, retrieved the public
rows, and passed exact trusted verification:

```text
status:             accepted
target:             delta
result rows:        160
query calls:        2
submission calls:   1
submission digest:  0a9a2644cf2aaabb88922a9bb2f2881415d43b87b31d8aa3a0dadd25b40c7b46
```

The digest is identical to the generated reference trajectory for that task.
The configured external Hy3/TokenHub endpoint was not called because
`TOKENHUB_API_KEY` was absent; the acceptance above is the real public-only
Codex rollout required by this migration, not a reference-solver replay.

## Validation

Final suite validation:

```text
verify_portable_bsm_metric_suite_v3(...)
```

Result:

```text
PORTABLE_SUITE_VERIFIED bsm-market-metric-suite-v2-8c67c3c32a4307e14dd3e24f 24
```

The final all-24 audit validated every suite/target/leaf manifest and schema,
database digest and logical checksum, toolset binding, artifact visibility,
market-content equality, exact v2/v3 answer equality, unordered-row acceptance,
and 160-row public query result.

An independent read-only release audit found no blocker: all 17 Definition of
Done items had evidence, 384 leaf artifact digests and 24 database identities
matched, all 66 events across the six trajectories matched and replayed
byte-identically, all 24 v2/v3 canonical row sets were equal, and all 18
duplicate/missing/extra negative cases were rejected.

Whole-repository regression:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
```

Result: `506 passed in 213.55s`.

The final post-cleanup v3 public-contract and portable-suite focused regression
passed `29 tests in 19.76s`.

The regression includes v2 replay/validator coverage, bidirectional v2/v3
dispatch rejection, all six v3 reference chains, SQL positive/negative and
security cases, prompt leakage gates, parent-hosted path isolation, exact
verifier tests, portable packaging, runner discovery, and generated-tree
validation.

## Change inventory

The focused source changes are under:

```text
llm_solutions/run_bsm_market_implied_greeks.py
scripts/package_bsm_greeks_delivery.py
scripts/package_bsm_metric_v3_reference_trajectories.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/
```

New public contracts are under:

```text
environments/solver/capabilities.global_v3.json
environments/solver/capabilities.bsm_greeks_v3.json
schemas/agent-task-package-v3.schema.json
schemas/agent-task-portable-toolset-v3.schema.json
schemas/agent-task-runtime-contract-v3.schema.json
schemas/bsm-market-implied-*-submission-v2.schema.json
```

Focused tests are under
`tests/packaging_analytic_and_implied_greeks_iv/` and
`tests/unit/test_hy3_chat_runner.py`. Package documentation was updated in
`task_packages/README.md`.
