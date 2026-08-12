# Packaging tests

This suite exercises the accepted `bsm_market_implied_greeks_v1` agent package
boundary. It builds a distinct short-lived P/Q parent and complete package under
`tmp_path`; tests do not require or modify a checked-in private parent.

Run it from the repository root:

```bash
.venv/bin/pytest -q tests/packaging
```

Coverage includes:

- strict package/runtime/trajectory/submission/oracle interfaces;
- global-allowlist ∩ task-overlay composition and solver-source policy attacks;
- deterministic 8-underlying selection and the exact three-relation, 160-row DB;
- two byte-identical reference replays through counted trusted adapters;
- independent pinned-QuantLib canonical exact verification;
- last-decimal, units, conventions, row identity/order/count and private-field
  negative submissions;
- recursive public leakage scans and exact authoring/train-dev/evaluation views;
- source artifact digests and byte identity of every release-view copy.
- spawn-process runtime enforcement for import/builtin/tool/submission/resource
  limits, with the production OS/container boundary documented separately;
- verified source package → self-contained nine-field JSONL export with no
  private oracle or sampling seed.

The frozen method requires 80 bisection updates. Because 79/80/81 roots normally
collapse to the same 8-decimal output, schedule changes are checked by source/runtime
policy; semantic verification makes only observable exact-output claims.

The suite contract-tests Phase F export with one temporary package. It does not
execute the full default 100-task batch; that run is an explicit release-stage
operation and must preserve its run summary and split provenance for review.
