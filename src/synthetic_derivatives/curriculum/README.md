# Curriculum schedulers

This package contains two additive scheduling paths:

- the legacy coordinate scheduler reads
  [`adaptive_v2.json`](../../../configs/curricula/adaptive_v2.json) unchanged;
- `FamilyAwareCurriculumScheduler` reads
  [`tdgbm_bsm_portable_v1.json`](../../../configs/curricula/tdgbm_bsm_portable_v1.json)
  and operates on semantic `TaskSpecV3` tasks.

The family-aware path first applies the exact executable-capability gate with
`portable_verified` status, then maps admitted tasks into family-local stages.
Catalog compatibility alone cannot put a task into the pool. The current config
contains only the implemented `tdgbm_bsm` portable bundle, six static metrics,
and six DuckDB-query v3 metrics; it does not activate L3 Monte Carlo or another
model family.

Both paths preserve the declared 20% replay / 60% current / 20% explore mixture
and mastery-band multipliers. Scheduler diagnostics may change sampling weights,
but never task content, task identity, dataset grouping, verifier behavior, or
the binary hard reward.

See [`test_family_curriculum.py`](../../../tests/unit/test_family_curriculum.py)
for capability-before-stage ordering, local stage mapping, stable sampling, and
legacy-semantics coverage.
