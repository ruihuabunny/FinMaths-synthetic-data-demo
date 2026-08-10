from synthetic_derivatives.tasks.bsm_market_greeks import MarketGreeksSubmission


def test_submission_contract(agent_submission):
    MarketGreeksSubmission.from_mapping(agent_submission)
