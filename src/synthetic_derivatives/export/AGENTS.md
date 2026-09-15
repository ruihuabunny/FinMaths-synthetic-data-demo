# Public Solver database export

- Open the authoring parent read-only and verify its frozen identity before
  selecting rows. Never mutate, attach writable state to, or normalize the parent.
- Materialize a physically independent public child with the exact table/column
  allowlist. Recursive leakage scans must reject private names and nested values.
- Stable sample/task IDs derive from declared public identity and selection
  inputs. Preserve deterministic row order, schema, logical checksum and subset
  manifest.
- Do not export authoring volatility, drift nodes when not public, RNG/selector
  seeds, mutation lineage, oracle values, audits or verifier configuration.
- Publish only after the complete child passes schema, content and read-only-open
  validation.

