# Deterministic task mutation

This package keeps the accepted legacy mutation engine and adds a separate
family-aware path for semantic `TaskSpecV3` tasks. The legacy
[`deterministic_v2.json`](../../../configs/mutations/deterministic_v2.json) is not
reinterpreted or migrated in place.

`FamilyMutationEngine` reads
[`tdgbm_bsm_deterministic_v1.json`](../../../configs/mutations/tdgbm_bsm_deterministic_v1.json).
For ordinary mutations, `model_family_id` and coordinate `M` are immutable. A
child must remain design-compatible and match an exact registered executable
capability; design-catalog-only Heston, local-vol, Monte Carlo, or other planned
paths therefore fail closed.

Deterministic child identity and private lineage cover the complete semantic
change surface: family, task family/kind, method, solver interface, output
contract, coordinates, snapshot ID, and snapshot revision. In particular:

- an algorithm change requires a new `method_id`;
- an Agent ABI change requires a new `solver_interface_id`;
- an output change requires a new `output_contract_id`;
- a data-regime change requires new snapshot identity or explicit child lineage.

This engine does not author a new stochastic model and does not permit an
ordinary mutation to cross model families. Such a transition requires a newly
authored task with its real state, backend, solver, verifier, runtime, and test
evidence.

See [`test_family_mutation_guards.py`](../../../tests/unit/test_family_mutation_guards.py)
for family/`M` immutability, capability admission, and full-lineage coverage.
