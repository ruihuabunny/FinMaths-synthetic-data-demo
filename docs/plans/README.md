# Implementation plans

| Document | Scope | Current interpretation |
|:---|:---|:---|
| [`synthetic_bsm_multi_asset_psd_duckdb_greeks_plan.md`](synthetic_bsm_multi_asset_psd_duckdb_greeks_plan.md) | Multi-asset/P-Q/PSD/DuckDB/Greeks migration | Historical implementation plan; baseline is implemented |
| [`synthetic_bsm_multi_asset_psd_duckdb_greeks_codex_checklist.md`](synthetic_bsm_multi_asset_psd_duckdb_greeks_codex_checklist.md) | Executable migration checklist | Historical checklist; do not rerun completed phases blindly |
| [`synthetic_bsm_greeks_agent_task_packaging_plan.md`](synthetic_bsm_greeks_agent_task_packaging_plan.md) | Source package and portable-delivery architecture | Implemented through current BSM packaging line; retain as design history |
| [`synthetic_bsm_l3_mc_greeks_implementation_plan.md`](synthetic_bsm_l3_mc_greeks_implementation_plan.md) | L3 Monte Carlo Greeks | Pending; only `solver/mc` scaffold exists |
| [`synthetic_derivatives_model_family_refactor_plan.md`](synthetic_derivatives_model_family_refactor_plan.md) | Model-family identity and registry refactor | Pending |
| [`duckdb_agent_task_plan.md`](duckdb_agent_task_plan.md) | DuckDB-backed task architecture | Partly realized by current packaging/export implementation |
| [`inactive_f2a_arbitrage_finding_agent_task_plan.md`](inactive_f2a_arbitrage_finding_agent_task_plan.md) | F2A arbitrage tasks | Inactive and not implemented |

Current behavior is defined by source code, versioned contracts, frozen manifests
and tests—not by plan completion prose.

