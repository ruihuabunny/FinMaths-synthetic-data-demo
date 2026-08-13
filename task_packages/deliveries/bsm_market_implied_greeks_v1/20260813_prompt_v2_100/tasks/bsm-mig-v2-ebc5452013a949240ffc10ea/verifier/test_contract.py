from verifier.runtime import MarketGreeksSubmission


def test_submission_contract(agent_submission):
    MarketGreeksSubmission.from_mapping(agent_submission)
