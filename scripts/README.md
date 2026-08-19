# Script entry points

| Script | Purpose |
|:---|:---|
| `edit_snapshot.py` | Create, append, inspect and freeze authoring snapshots |
| `replay_snapshot.py` | Deterministically replay and compare snapshot databases |
| `sample_physical_dynamics.py` | Materialize deterministic P-dynamics configuration samples |
| `package_bsm_greeks_task.py` | Build and verify one BSM market-Greeks source package |
| `run_bsm_greeks_batch.py` | Build a deterministic batch of source packages |
| `package_bsm_greeks_delivery.py` | Convert accepted source runs into non-overwriting portable combined deliveries or capability-gated metric suites, including modular leaf verifiers |
| `package_bsm_metric_v3_reference_trajectories.py` | Build authoring-side v3 metric reference trajectories |

Scripts are orchestration façades. Mathematical and security logic belongs in
`src/synthetic_derivatives/`; output should normally target `/tmp` until a
versioned artifact is explicitly being published.

For an accepted metric suite, always choose a new `--delivery-id`; the script
stages and validates the complete tree before one atomic publish and never
updates a frozen delivery in place. The modular-verifier release commands and
identities are documented in [`task_packages/README.md`](../task_packages/README.md).
