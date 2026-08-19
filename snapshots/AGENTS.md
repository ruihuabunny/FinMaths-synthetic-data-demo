# Snapshot artifacts

- Authoring parents and checked-in public snapshots are immutable once frozen.
  Append or rebuild into a new database/revision; never edit a frozen file in
  place.
- `public/` may contain only reviewed solver-visible market data, manifests and
  safe read-only queries. It must not contain latent volatility, authoring seed,
  oracle answer, mutation lineage, hidden tests, or verifier implementation.
- Stable IDs, declared row ordering, logical checksum and schema allowlist are
  part of snapshot identity. Preserve them across relocation and replay.
- Queries under `public/sql_query/` must be read-only, parameterized at the top,
  and explicitly ordered.

