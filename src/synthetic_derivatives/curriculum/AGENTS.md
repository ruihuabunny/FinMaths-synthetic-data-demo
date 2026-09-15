# Curriculum scheduler

- The scheduler maps stage/mastery diagnostics to deterministic sampling weights.
  It does not change task contents, task IDs, hard reward, verifier behavior or
  dataset grouping.
- Preserve configured replay/current/explore mixtures, tie-breaking and stable
  order. Validate weights and band boundaries without inventing new tasks.
- A level listed in a curriculum document is not selectable until the task family,
  contracts, runtime and verifier are implemented and registered.
