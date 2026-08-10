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

The frozen method requires 80 bisection updates. Because 79/80/81 roots normally
collapse to the same 8-decimal output, schedule changes are checked by source/runtime
policy; semantic verification makes only observable exact-output claims.
