# Trusted verifier environment

This image receives the frozen public task package and the submitted JSON after
the solver run. It contains pinned `QuantLib==1.39` and `duckdb==1.5.5` so it can
reconstruct the declared 80-step BSM inverse and five unit Greeks independently.

The verifier image is not solver-visible. It does not expose its Python modules,
oracle configuration, tests, reference artifacts, or filesystem to the Agent.
All outcome comparisons use canonical strings and exact equality; no tolerance
parameter is part of this environment contract.
