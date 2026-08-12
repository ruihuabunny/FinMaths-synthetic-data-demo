# Trusted verifier environment

This image receives the frozen public task package and the submitted JSON after
the solver run. It contains pinned `QuantLib==1.39` and `duckdb==1.5.5` so it can
reconstruct the declared 80-step BSM inverse and five unit Greeks independently.

The verifier image is not solver-visible. It does not expose its Python modules,
oracle configuration, tests, reference artifacts, or filesystem to the Agent.
All outcome comparisons use canonical strings and exact equality; no tolerance
parameter is part of this environment contract.

For the accepted golden task, the hidden suite lives under the source package's
`verifier/` directory and reconstructs truth from `public/task.duckdb` plus the
frozen `oracle_config.json`; it never reads the authoring latent volatility or
legacy IV audit. Package-level integration and rejection coverage is maintained
in `tests/packaging/`. The verifier directory is included only in the authoring
view, never in the train/dev or evaluation view presented to the Agent.
