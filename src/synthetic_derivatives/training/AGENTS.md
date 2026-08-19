# Training export

- Export only after the source package and submission pass the owning hard
  verifier and data-identity checks.
- Current exporter is BSM market-Greeks-specific. Do not generalize across model
  families until a versioned common record contract and tests exist.
- Preserve parent/snapshot grouping, stable task order, interface/method identity
  and public trajectory provenance.
- Reject private oracle, authoring seed/selector seed, verifier source/tests,
  hidden lineage, absolute paths and non-public artifacts recursively.
- Dataset files are rebuildable outputs; manifests and grouping evidence are the
  versioned records.
