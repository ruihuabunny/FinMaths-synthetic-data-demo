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
