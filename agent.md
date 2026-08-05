# Agent Guidelines

## Keep implementations simple

- Prefer the simplest implementation that satisfies the current requirement.
- Do not add speculative abstractions, fallback paths, or defensive checks without a concrete need.
- Trust established project invariants and upstream guarantees unless there is evidence that they are unreliable.

## Validate only what matters

- Validate the identifiers required by the workflow, such as file IDs and version IDs.
- Do not calculate or verify SHA-256 values, hashes, checksums, or file contents unless integrity or security verification is an explicit requirement.
- Avoid validating the same condition repeatedly across layers.

## Avoid improbable edge-case branches

- Do not add `if`/`else` branches for scenarios that are extremely unlikely and unsupported by an actual requirement, known failure mode, or test case.
- Let impossible states remain governed by existing invariants instead of adding code for every hypothetical condition.
- Add edge-case handling only when the case is realistic, has meaningful impact, and can be handled correctly.

## Scope discipline

- Make focused changes that directly serve the requested behavior.
- Do not introduce unrelated refactors or extra compatibility logic.
- When stronger validation or exceptional handling is genuinely necessary, keep it proportional and briefly document the concrete reason.
