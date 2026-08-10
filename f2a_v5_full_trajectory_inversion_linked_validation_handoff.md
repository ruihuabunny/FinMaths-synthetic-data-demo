# F2A v5.1 Full-Trajectory Verifier Handoff

## Runtime identity

- Variant: `bsm_model_reconstruction_xut_signal_f2a_v5`
- Schema version: `5.1.0`
- Public DB schema: `f2a-public-duckdb-v5.1.0`
- Output contract: `model-reconstruction-xut-full-trajectory-v2`
- Submission schema: `schemas/submission-v5.1.schema.json`

The trusted verifier recomputes the complete public-only trajectory. It never reads generator node values,
clean quotes, seeds, requested signatures, mutation lineage, private truth, or FP/FN flags.

## Semantic layers

| Layer | Recomputed object |
|---|---|
| V0 | Frozen public identity, four contract rows, domains, ordering, and leakage boundary |
| V1 | Three-node `P`-measure drift/diffusion estimator and covariance |
| V2 | Fixed market inversion, linked BSM validation, residuals, and localisation |
| V3 | Post-cost X/U/T model signal over IV-eligible rows and linked prices |
| V_exec | Separate frozen-v4 executable catalogue audit |

V0 requires `physical_fitting_contract`, `bsm_inversion_contract`,
`linked_diffusion_validation_contract`, and `model_signal_contract`. Solver and verifier implementations
remain independent.

## V2 contract

Every visible bid/ask midpoint is passed to the fixed `[1e-6, 5.0]`, 80-step binary64 BSM bisection.
A valid root produces market IV and derived market `d1/d2`. An unattainable midpoint produces only
`invalid_bracket`; it never produces `NaN`, infinity, a bracket endpoint, or a last-iterate substitute.

All rows also receive a linked validation result from the exact integrated squared Stage-1 diffusion.
Linked price, linked `d1/d2`, parameter SE, price residual, and standardized residual do not use market-IV
repricing. `series_status=COMPLETE` means every row completed the fixed procedure and received an explicit
status.

## Invalid-IV eligibility

The public model-signal contract freezes `market_iv_eligibility_rule=converged_rows_only`. Rows marked
`invalid_bracket` remain visible and retain linked diagnostics, but they are not used to instantiate v5 X/U/T
model-signal candidates. This exclusion does not fail pilot authoring.

The frozen v4 execution audit is intentionally different: its cash-flow certificates depend on executable
bid/ask quotes and costs, not IV, so it continues to scan the full public quote set.

## Exact verification

The verifier rejects a wrong method ID, iteration count, row ordering, status, market or linked `d1/d2`,
linked price, residual, non-finite value, signal field, or output identity. V2 compares
`option_series_results` and `mutation_diagnosis` exactly after the frozen half-even canonicalization; the
verifier digest covers the entire v2 submission.

Authoring additionally checks that status keys cover every valuation row and that market IV/market `d1/d2`
map keys equal exactly the `converged` row set. Release remains blocked until the separate 2,000+ task-seed
cohort calibration gate passes.
