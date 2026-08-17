# Task-space catalog and executable capabilities

`derivatives_v2.json` is the broad design catalog. Its coordinate compatibility
rules describe structurally meaningful combinations, including planned model
and product families; they do not authorize generation, scheduling, packaging,
or runtime execution.

`executable_capabilities_v1.json` is the fail-closed sidecar for the one model
family currently implemented in this repository, `tdgbm_bsm`. Runtime-facing
entrypoints require an exact five-field key plus the declared output contract.
An absent key is unavailable even when the design catalog says its coordinates
are compatible.
