-- Authoring-only query: inspect the candidate grid, liquidity rule and quote model.
WITH parameters(snapshot_id) AS (
    VALUES ('DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3')
),
contract_stats AS (
    SELECT
        snapshot_id,
        chain_id,
        count(*) AS materialized_contract_count,
        count(DISTINCT underlying_id) AS underlying_count,
        count(DISTINCT expiry) AS selected_expiry_count,
        count(DISTINCT strike_moneyness) AS selected_moneyness_count,
        min(strike_moneyness) AS selected_min_moneyness,
        max(strike_moneyness) AS selected_max_moneyness
    FROM market.option_contracts
    WHERE chain_id IS NOT NULL
    GROUP BY snapshot_id, chain_id
)
SELECT
    chain.snapshot_id,
    chain.chain_id,
    chain.listing_rule,
    chain.listing_date,
    chain.roll_rule,
    chain.grid_type,
    chain.expiry_days AS candidate_expiry_days,
    chain.moneyness_grid AS candidate_moneyness_grid,
    chain.strike_grid AS candidate_strike_grid,
    chain.call_put,
    chain.strike_increment,
    chain.strike_rounding,
    chain.exercise_style,
    chain.settlement_type,
    chain.contract_multiplier,
    chain.liquidity_filter,
    chain.quote_model,
    stats.underlying_count,
    stats.selected_expiry_count,
    stats.selected_moneyness_count,
    stats.selected_min_moneyness,
    stats.selected_max_moneyness,
    stats.materialized_contract_count,
    chain.generator_config_id
FROM market.option_chain_specs AS chain
JOIN parameters
  ON parameters.snapshot_id = chain.snapshot_id
LEFT JOIN contract_stats AS stats
  ON stats.snapshot_id = chain.snapshot_id
 AND stats.chain_id = chain.chain_id
ORDER BY chain.chain_id;
