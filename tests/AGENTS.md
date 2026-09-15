# Test suites

- Put tests at the owning boundary: mathematical primitives in `unit/`, cross-
  implementation/data handoffs in `integration/`, snapshot/public surface in
  `public/`, and package/release isolation in the dedicated packaging suite.
- Use temporary parents, databases and package roots. Tests must not mutate
  checked-in frozen snapshots, accepted source packages or portable deliveries.
- Solver/verifier agreement tests require independent implementations and exact
  canonical comparison. Do not weaken failures with tolerances or warning
  suppression unless the public contract itself changes through a new version.
- Negative tests should cover realistic boundary attacks: units, last decimal,
  method/interface ID, key set/count/order policy, private-field leakage, imports,
  tool budgets, filesystem/view allowlists and artifact identity.
- `verifier_robustness/` is reserved for family-level hard-verifier attacks;
  current BSM package attacks remain in the existing package suite until a new
  family owns a distinct suite.
- A scaffold or plan is not tested as implemented capability. L3 tests belong in
  new files/suites only when its contracts and code exist.

