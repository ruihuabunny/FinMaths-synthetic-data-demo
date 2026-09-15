# Implementation plans

| Document | Scope | Current interpretation |
|:---|:---|:---|
| [`finished_synthetic_bsm_multi_asset_psd_duckdb_greeks_plan.md`](finished_synthetic_bsm_multi_asset_psd_duckdb_greeks_plan.md) | Multi-asset/P-Q/PSD/DuckDB/Greeks migration | Historical implementation plan; baseline is implemented |
| [`finished_synthetic_bsm_multi_asset_psd_duckdb_greeks_codex_checklist.md`](finished_synthetic_bsm_multi_asset_psd_duckdb_greeks_codex_checklist.md) | Executable migration checklist | Historical checklist; do not rerun completed phases blindly |
| [`synthetic_bsm_greeks_agent_task_packaging_plan.md`](synthetic_bsm_greeks_agent_task_packaging_plan.md) | Source package and portable-delivery architecture | Implemented through current BSM packaging line; retain as design history |
| [`synthetic_bsm_l3_mc_greeks_implementation_plan.md`](synthetic_bsm_l3_mc_greeks_implementation_plan.md) | L3 Monte Carlo Greeks | Pending; only `solver/mc` scaffold exists |
| [`synthetic_derivatives_model_family_refactor_plan.md`](synthetic_derivatives_model_family_refactor_plan.md) | Model-family identity and registry refactor | Phase 1–4 implemented for existing `tdgbm_bsm`; L3/second family remain out of scope |
| [`finished_raw_fin_data_generation_authoring_pipeline_refactor_plan.md`](finished_raw_fin_data_generation_authoring_pipeline_refactor_plan.md) | Authoring config/schema/pipeline/underlying structural refactor | Implemented 2026-08-19; compatibility façades and exact logical contracts retained |
| [`bsm_metric_leaf_verifier_runtime_refactor_plan.md`](bsm_metric_leaf_verifier_runtime_refactor_plan.md) | BSM metric leaf verifier runtime modularization | Phases 0–7 completed 2026-08-19; new delivery identities and audit report are frozen |
| [`duckdb_agent_task_plan.md`](duckdb_agent_task_plan.md) | DuckDB-backed task architecture | Partly realized by current packaging/export implementation |
| [`inactive_f2a_arbitrage_finding_agent_task_plan.md`](inactive_f2a_arbitrage_finding_agent_task_plan.md) | F2A arbitrage tasks | Inactive and not implemented |

Current behavior is defined by source code, versioned contracts, frozen manifests
and tests—not by plan completion prose.
