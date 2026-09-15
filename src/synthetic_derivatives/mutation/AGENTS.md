# Deterministic mutation

- Treat the parent task/snapshot as immutable. A mutation produces a new child ID
  and complete private lineage; it never edits or relabels the parent.
- Operators may change only declared axes, within the configured direction and
  count limits. Recompute public task content and truth from the mutated child.
- Do not use model performance to choose a mutation; curriculum/adaptive sampling
  lives elsewhere.
- F2A arbitrage is inactive. Do not add price-inequality labels or revive that
  plan without an explicit traded-market, strategy, funding, constraint and
  independent-oracle contract.

