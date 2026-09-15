# Schema boundaries

This directory holds versioned structures shared across permission boundaries:

- market snapshot and manifest records;
- task coordinates, variants and package manifests;
- public submission and trajectory records;
- verification and dataset-export records.

`model-family-v1.schema.json` and `executable-capability-v1.schema.json` define
the new declarative identity/gate sidecars. `task-v3.schema.json` versions the
semantic task identity only; it is unrelated to package, runtime, toolset, or
DuckDB query protocols that also use a v3 label. Existing accepted schemas are
not modified by this addition. `family-mutation-v1.schema.json` records the
semantic child plus complete before/after lineage; it does not replace the
legacy mutation schemas.

The schema answers “what fields and shapes are legal.” Numerical methods, units,
runtime capabilities and hidden verification logic remain in their owning
contracts. Accepted packages bind exact schema identities/digests, so breaking
changes require a new version rather than an in-place edit.
