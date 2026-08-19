from .runtime import (
    load_bsm_market_metric_inputs,
    verify_market_metric_submission,
)


def test_exact_market_metric(package_root, agent_submission, oracle_config):
    inputs = load_bsm_market_metric_inputs(
        package_root / "task.duckdb", oracle_config
    )
    verify_market_metric_submission(inputs, agent_submission, oracle_config)
