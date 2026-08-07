-- Trusted authoring/verifier query. Do not expose this result to the Solver.
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2',
        DATE '2026-08-03',
        'SYNTH-METAL-GOLD'
    )
)
SELECT
    audit.snapshot_id,
    audit.date AS valuation_date,
    contract.underlying_id,
    audit.option_id,
    contract.call_put,
    contract.strike,
    contract.expiry,
    audit.risk_neutral_measure_id,
    audit.numeraire_id,
    audit.rate_path_id,
    audit.measure_change,
    audit.volatility_mapping,
    audit.q_effective_volatility,
    audit.theoretical_price,
    audit.canonical_mid,
    quote.mid AS solver_visible_mid,
    audit.implied_volatility,
    audit.iv_status,
    audit.iv_error,
    audit.iv_solver
FROM market.option_pricing_audit AS audit
JOIN market.option_contracts AS contract
  ON contract.snapshot_id = audit.snapshot_id
 AND contract.option_id = audit.option_id
JOIN market.option_daily AS quote
  ON quote.snapshot_id = audit.snapshot_id
 AND quote.date = audit.date
 AND quote.option_id = audit.option_id
JOIN parameters
  ON parameters.snapshot_id = audit.snapshot_id
 AND parameters.market_date = audit.date
 AND parameters.underlying_id = contract.underlying_id
ORDER BY contract.expiry, contract.call_put, contract.strike, audit.option_id;
