# Agent-task artifacts

- Treat `deliveries/` as generated, immutable release artifacts. Do not hand-edit
  a leaf prompt, manifest, runtime, DB, verifier or payload; change the builder or
  versioned source contract and generate a new delivery ID.
- Preserve physical separation between Agent-visible evaluation files, host-side
  task DB/trusted tools, hidden verifier, reference/train-dev material and
  authoring-private provenance.
- A portable task includes only the files and host protocol required by its
  package contract. It does not implicitly include repository source, sandbox or
  deployment infrastructure.
- Validate exact tree allowlists, content digests, no absolute paths, recursive
  leakage, trusted-tool bindings, submission schema, independent verifier and
  relocatability before changing status.
- Preserve source-database uniqueness, task-to-source one-to-one mappings and
  shared-parent dataset grouping. `PORTABLE_VERIFIED` is not `RELEASED`; promotion
  must be explicit.
- `reports/` records migration intent and completed evidence. Reports do not alter
  frozen artifact identity.

