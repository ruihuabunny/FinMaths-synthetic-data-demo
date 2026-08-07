-- Authoring-only: inspect the distinct deterministic P/Q functions by underlying.
WITH parameters(snapshot_id, market_date) AS (
    VALUES (
        'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v1',
        DATE '2026-08-03'
    )
)
SELECT
    metadata.snapshot_id,
    metadata.valuation_date,
    metadata.underlying_id,
    underlying.physical_drift AS master_day0_physical_drift,
    CAST(json_extract(
        metadata.physical_dynamics,
        '$.drift_function.nodes[0].value'
    ) AS DOUBLE) AS function_day0_physical_drift,
    underlying.physical_drift = CAST(json_extract(
        metadata.physical_dynamics,
        '$.drift_function.nodes[0].value'
    ) AS DOUBLE) AS drift_day0_matches_master,
    underlying.physical_volatility AS master_day0_physical_volatility,
    CAST(json_extract(
        metadata.physical_dynamics,
        '$.volatility_function.nodes[0].value'
    ) AS DOUBLE) AS function_day0_physical_volatility,
    underlying.physical_volatility = CAST(json_extract(
        metadata.physical_dynamics,
        '$.volatility_function.nodes[0].value'
    ) AS DOUBLE) AS volatility_day0_matches_master,
    json_extract_string(metadata.physical_dynamics, '$.measure')
        AS physical_measure,
    json_extract_string(metadata.physical_dynamics, '$.state_role')
        AS physical_state_role,
    json_array_length(
        json_extract(metadata.physical_dynamics, '$.drift_function.nodes')
    ) AS drift_node_count,
    json_extract(metadata.physical_dynamics, '$.drift_function')
        AS physical_drift_function,
    json_array_length(
        json_extract(metadata.physical_dynamics, '$.volatility_function.nodes')
    ) AS volatility_node_count,
    json_extract(metadata.physical_dynamics, '$.volatility_function')
        AS physical_volatility_function,
    json_extract_string(metadata.pricing_dynamics, '$.measure')
        AS pricing_measure,
    json_extract_string(
        metadata.pricing_dynamics,
        '$.q_pricing.risk_neutral_measure_id'
    ) AS risk_neutral_measure_id,
    json_extract_string(
        metadata.pricing_dynamics,
        '$.q_pricing.numeraire_id'
    ) AS numeraire_id,
    json_extract_string(
        metadata.pricing_dynamics,
        '$.q_pricing.volatility_mapping'
    ) AS volatility_mapping,
    json_extract(metadata.pricing_dynamics, '$.volatility_function')
        AS q_volatility_function
FROM market.pricing_metadata AS metadata
JOIN market.underlyings AS underlying
  ON underlying.snapshot_id = metadata.snapshot_id
 AND underlying.underlying_id = metadata.underlying_id
JOIN parameters
  ON parameters.snapshot_id = metadata.snapshot_id
 AND parameters.market_date = metadata.valuation_date
ORDER BY metadata.underlying_id;
