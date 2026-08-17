# Script entry points

| Script | Purpose |
|:---|:---|
| `edit_snapshot.py` | Create, append, inspect and freeze authoring snapshots |
| `replay_snapshot.py` | Deterministically replay and compare snapshot databases |
| `sample_physical_dynamics.py` | Materialize deterministic P-dynamics configuration samples |
| `package_bsm_greeks_task.py` | Build and verify one BSM market-Greeks source package |
| `run_bsm_greeks_batch.py` | Build a deterministic batch of source packages |
| `package_bsm_greeks_delivery.py` | Convert accepted source runs into portable deliveries/suites |
| `package_bsm_metric_v3_reference_trajectories.py` | Build authoring-side v3 metric reference trajectories |

Scripts are orchestration façades. Mathematical and security logic belongs in
`src/synthetic_derivatives/`; output should normally target `/tmp` until a
versioned artifact is explicitly being published.

