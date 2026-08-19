# Cross-boundary schemas

- Schemas define syntax and structural invariants, not pricing formulas, hidden
  oracle logic, runtime permissions, or database discovery answers.
- Preserve the distinction between required, optional, and nullable fields.
  Prefer `additionalProperties: false` for closed public contracts where the
  existing version already uses that rule.
- Adding a required field, renaming/removing a field, changing units/meaning, or
  tightening accepted values is breaking and requires a new schema/interface
  version plus migration and negative tests.
- Put units and economic semantics in the owning public semantic contract; keep
  private lineage and verifier-only identity out of Solver-visible schemas.
- Validate examples and generated packages against the exact referenced schema
  digest. Never edit a schema used by an accepted package in place.

