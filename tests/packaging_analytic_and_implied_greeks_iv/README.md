# Packaging tests

This suite exercises the accepted `bsm_market_implied_greeks_v1` source-package
boundary and its portable data-only delivery. Most tests build a distinct
short-lived P/Q parent and complete package under `tmp_path`; tests do not
require or modify a checked-in private parent. The maintained 100-task portable
delivery uses the minimal four-source routing prompt and preserves the source
`solver_interface_digest` identities.

Run it from the repository root:

```bash
.venv/bin/pytest -q tests/packaging_analytic_and_implied_greeks_iv
```

Coverage includes:

- semantic `tdgbm_bsm` capability preflight before output creation, including
  exact static-v2/query-v3 interface and output-contract mappings;
- strict package/runtime/trajectory/submission/oracle interfaces;
- global-allowlist ∩ task-overlay composition and solver-source policy attacks;
- deterministic 8-underlying selection and the exact three-relation, 160-row DB;
- two byte-identical reference replays through counted trusted adapters;
- independent pinned-QuantLib canonical exact verification;
- last-decimal, units, conventions, row identity/order/count and private-field
  negative submissions;
- recursive public leakage scans and exact authoring/train-dev/evaluation views;
- source artifact digests and byte identity of every release-view copy;
- spawn-process runtime enforcement for import/builtin/tool/submission/resource
  limits, with the production OS/container boundary documented separately;
- verified source package → self-contained nine-field JSONL export with no
  private oracle or sampling seed;
- source batch → relocatable portable tasks with declarative static query tools,
  schema-bound submission, strict visibility/digest manifests, and no reference,
  private, dataset, view, absolute-path, or readable selector-seed leakage.

The frozen method requires 80 bisection updates. Because 79/80/81 roots normally
collapse to the same 8-decimal output, schedule changes are checked by source/runtime
policy; semantic verification makes only observable exact-output claims.

The suite contract-tests Phase F export and portable conversion with temporary
packages, and checks the maintained 2026-08-13 delivery manifest. The full
current-interface run remains in `runs/`; the delivery is
`PORTABLE_VERIFIED`, not implicitly `RELEASED`.

The suite also freezes Phase 0 of the BSM metric leaf-verifier refactor against
the accepted 2026-08-19 static-v2 and DuckDB-query-v3 deliveries. The
machine-readable fixture binds both complete trees and all 48 assignment
identities; focused tests recompute every checksum and canonical submission,
compare paired protocol outputs, freeze representative rejection behavior for
all six metrics, and run copied leaves under `python -I` without modifying the
accepted artifacts.

Phase 1 tests compare the extracted shared model, database, and QuantLib oracle
modules with the frozen leaf runtimes on every one of those 48 databases. They
require exact parsed records, logical checksums, canonical submissions,
exception types/messages, dependency-pin failures, and an acyclic source-module
dependency direction. The production renderer stayed on the accepted
monolithic implementation through this extraction phase.

Phase 2 tests compare the explicit static-v2 and DuckDB-query-v3 submission
modules with their frozen runtimes across all 48 assignments. They preserve
ordered versus unordered row behavior and exact malformed-submission outcomes,
cross-check typed profiles against every frozen oracle config, require profile
validation to fail closed, and reject source shadowing, dynamic execution,
cross-protocol parser dispatch, or selection of more than one parser.

Phase 3 tests render the complete modular verifier tree for both protocols and
all six metrics in temporary directories. They require byte-identical repeated
renders, a stable public facade, relative internal imports, recursive leakage
and AST checks over every generated Python file, exact metric semantics, one
active parser, module size limits, safe destination handling, relocation, and
successful `python -I` execution without the repository source path.

Phase 4 tests exercise newly built static-v2 and DuckDB-query-v3 suites through
the production packaging path. They require the renderer-owned 15-file
inventory to drive exact leaf and suite trees, compare every written verifier
byte with the selected modular render, bind every nested module in both artifact
and visibility maps, preserve `verifier_only` classification, and reject a
tampered nested module or an extra unbound verifier file.

Phase 5 imports production-rendered modular runtimes beside copied frozen
`20260819` runtimes and compares all 48 assignments. The matrix requires exact
input records, logical checksums, canonical submissions, valid verification,
and complete validator/verifier outcomes—including exception types, messages,
and causes—for malformed, missing, extra, duplicated, reordered,
non-canonical, NaN, infinity, boolean, and last-decimal cases. It separately
proves static-v2 ordering and v3 unordered semantics and compares fail-closed
DuckDB and QuantLib pin failures.

Phase 6 freezes the two newly materialized `20260819_*_modular` deliveries in a
separate machine-readable identity fixture. The comparison test runs each full
suite validator, compares all 24 old/new assignments per protocol, proves byte
identity for databases and every non-verifier contract, recomputes exact
expected answers, audits the six changed and six added paths in every leaf,
checks every nested verifier digest/visibility binding, and executes one real
leaf per protocol under `python -B -I`. Phase 7 tests no longer import the
deleted monolithic source templates; frozen copies are loaded only from the
immutable baseline deliveries when historical equivalence is under test.
