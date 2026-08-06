-- Authoring-only query: inspect the P-measure underlying-driver dependence contract.
WITH parameters(snapshot_id) AS (
    VALUES ('DERIVATIVES-METALS-LIQUID-BSM-v1')
)
SELECT
    dependence.snapshot_id,
    dependence.dependence_spec_id,
    dependence.measure,
    json_array_length(dependence.driver_order) AS driver_count,
    dependence.driver_order,
    dependence.formulation,
    dependence.factor_loading_matrix,
    dependence.idiosyncratic_diagonal,
    dependence.correlation_matrix,
    dependence.matrix_dtype,
    dependence.factorization_method,
    dependence.factorization_order,
    dependence.time_grid,
    dependence.regime_id,
    dependence.generator_config_id
FROM market.underlying_dependence AS dependence
JOIN parameters
  ON parameters.snapshot_id = dependence.snapshot_id
ORDER BY dependence.dependence_spec_id;
