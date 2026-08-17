# BSM curriculum documents

- [`synthetic_bsm_agent_task_complete_curriculum.md`](synthetic_bsm_agent_task_complete_curriculum.md)
  records the baseline progression.
- [`synthetic_bsm_agent_task_more_greeks_complete_curriculum.md`](synthetic_bsm_agent_task_more_greeks_complete_curriculum.md)
  extends the target Greek/task coverage.

These are target curricula. Analytic BSM, visible-price IV and market-implied unit
Greeks are implemented; L3 Monte Carlo, broader risk/hedging levels and additional
model families remain subject to their own versioned contracts and acceptance
tests.

The executable current-family scheduler is narrower than these target documents.
[`tdgbm_bsm_portable_v1.json`](../../configs/curricula/tdgbm_bsm_portable_v1.json)
contains only capability-verified combined/static/query-v3 BSM stages and is
described in the
[`curriculum` package README](../../src/synthetic_derivatives/curriculum/README.md).
A level appearing in either curriculum document is not selectable until the
exact semantic capability is registered as portable.
