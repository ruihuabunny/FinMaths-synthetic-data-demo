from verifier.runtime import validate_market_metric_submission_contract


def test_submission_contract(agent_submission, oracle_config):
    validate_market_metric_submission_contract(agent_submission, oracle_config)
