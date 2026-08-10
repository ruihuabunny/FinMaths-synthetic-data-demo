# Agent task packages

Each task directory is an immutable accepted package with four source views:

- `public/`: the Agent-visible DuckDB, prompt, effective runtime contract, and
  submission schema;
- `verifier/`: trusted pytest and pinned method configuration, hidden during an
  Agent run;
- `reference/`: an observable train/dev replay and its standard-library artifact;
- `authoring_private/`: parent/sample identity, canonical answer, and build/leakage
  reports plus a complete source-artifact digest manifest, never copied into an
  evaluation view.

`views/evaluation` contains only `manifest.json` plus `public/`.
`views/train_dev` additionally contains `reference/`; `views/authoring` contains
all four source views. Package verification checks both each source digest and
byte identity between every release-view copy and its source. The checked-in
golden package is `ACCEPTED`, not a batch release. Rebuild it with
`scripts/package_bsm_greeks_task.py`; the script refuses to overwrite an existing
task identity.
