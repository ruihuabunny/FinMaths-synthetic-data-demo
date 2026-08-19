# Dataset outputs

- Generated JSONL/Parquet is rebuildable and normally remains untracked;
  versioned manifests belong under `manifests/`.
- Export only from verified packages. Group splits by frozen parent/scenario so
  related children do not leak across train, validation and test.
- Never include private oracle, hidden verifier/test material, selector seed,
  authoring lineage or absolute local paths.

