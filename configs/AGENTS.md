# Versioned configuration contracts

- JSON files here declare reproducible behavior; they do not contain Python
  implementation or hidden reference answers.
- Never repurpose an accepted version. Observable changes to a generator, task
  interface, metric, runtime, submission shape, allocation, or release protocol
  require a new versioned file and, where applicable, a new task identity.
- Keep probability measure, numeraire/rate-path identity, units, day count,
  algorithm/method ID, RNG ordering, canonicalization, and compatibility explicit.
- Validate configuration through the owning loader and tests. Do not duplicate
  parser rules in ad hoc scripts.
- `task_space/` describes coordinates and compatibility; it does not claim that a
  family is implemented. `curricula/` controls sampling weights and stages; it
  must not mutate frozen tasks or rewards.
- `deliveries/` and `task_packages/` select already valid source tasks and runtime
  contracts. Allocation must preserve the declared uniqueness and grouping
  constraints.

