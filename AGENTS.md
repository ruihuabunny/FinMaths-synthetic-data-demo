# Repository-wide agent instructions

These instructions apply everywhere in the repository. Read the closest nested
`AGENTS.md` before changing a path; nested files add directory-specific rules.
Read only the local README and design documents needed for the task instead of
loading every historical plan.

## Scope and deliverables

- Unless the user explicitly asks for a wider deliverable, an agent-task request
  means one self-contained portable task package, not the authoring repository,
  complete dataset, sandbox platform, deployment system, or production service.
- State the exact package or repository surface being changed and the major
  exclusions before implementation. Ask one concise question when the data,
  runtime, verifier, portability boundary, or output contract is materially
  ambiguous; do not silently choose the larger scope.
- Reuse the existing pipeline and contracts. Do not redesign unrelated
  infrastructure or add speculative compatibility layers, fallbacks, or checks.

## Non-negotiable mathematical identity

- Mathematical facts override existing code, fixtures, schemas, and backward
  compatibility. If they conflict, surface the conflict instead of changing the
  mathematics to make a test pass.
- A stochastic model is not identified until its probability measure, numeraire,
  state and conditioning information, time axis/day count, units, transition or
  approximation, parameter semantics, dtype, random ordering, and
  canonicalization are declared.
- Historical underlying evolution is under `P`; arbitrage-free derivative
  pricing is under the declared `Q`. Never use physical drift to price options or
  copy `P` parameters into `Q` without an explicit mapping.
- Physical volatility, pricing volatility, and implied volatility are distinct.
  Canonical IV for a solver task is recovered from the actual solver-visible
  quote using the frozen inverse problem, not copied from a hidden authoring
  volatility.
- Multi-asset dependence acts on declared underlying/model drivers. Correlation
  matrices must be symmetric, unit-diagonal, and PSD; derivative quotes or Greeks
  are never coupled after generation to simulate a joint market.
- Do not call a pricing residual, model disagreement, non-PSD parameter proposal,
  or raw cross-maturity price comparison an arbitrage. An arbitrage task must
  freeze the traded claims, strategy class, funding/cashflow rules, constraints,
  information set, horizon, and exact decision procedure.

## Permission and implementation boundaries

- `src/synthetic_derivatives/authoring/` owns private market generation and may
  use pinned QuantLib. It must not publish hidden state or canonical answers.
- `export/` creates an independent public child from an immutable frozen parent.
- `tasks/` owns shared input, unit, method, ordering, and serialization contracts;
  it contains no solver or verifier numerics.
- `solver/` contains developer-side restricted implementations. Effective Agent
  capabilities come from the versioned runtime profiles, not from repository
  importability.
- `verifier/` independently recomputes truth with pinned trusted dependencies and
  must not import Solver numerical implementations.
- `packaging_analytic_and_implied_greeks_iv/` owns package materialization,
  visibility views, trusted adapters, leakage gates, and portable delivery.
- `training/` exports only verified packages and must not emit private oracle,
  selector seed, hidden tests, or authoring lineage.

## Frozen identities and generated artifacts

- Treat accepted source packages, portable deliveries, public snapshots, task
  IDs, interface digests, manifests, and checksums as immutable. Observable
  interface changes require a new version and identity; never patch an accepted
  artifact in place.
- Files under `task_packages/deliveries/` are generated release artifacts. Change
  their builders or contracts first and regenerate only when the user explicitly
  asks for a new delivery.
- Keep unrounded internal state until the declared canonicalization boundary.
  Preserve deterministic draw order, row identity/order policy, and atomic
  publication behavior.

## Engineering workflow

- Make focused changes and preserve existing public/private boundaries.
- Use the repository-local `.venv` and pinned locks. The standard full check is
  `.venv/bin/python -m pytest`; run the smallest relevant suite first.
- Add or update tests at the boundary that owns the behavior. Solver/verifier
  independence, leakage, exact canonical output, and frozen-artifact identity are
  security or correctness requirements and should be tested explicitly.
- Validate identifiers, content digests, and files only where the relevant
  contract requires them. Avoid repeating the same validation across layers.

## Local instruction map

| Area | Additional instructions |
|:---|:---|
| Authoring inputs and templates | `authoring/AGENTS.md`, `configs/AGENTS.md` |
| Documentation and plans | `docs/AGENTS.md` |
| Runtime and schema contracts | `environments/AGENTS.md`, `schemas/AGENTS.md` |
| Python implementation | `src/synthetic_derivatives/AGENTS.md` plus the closest package file |
| Portable task artifacts | `task_packages/AGENTS.md` |
| Tests | `tests/AGENTS.md` |

