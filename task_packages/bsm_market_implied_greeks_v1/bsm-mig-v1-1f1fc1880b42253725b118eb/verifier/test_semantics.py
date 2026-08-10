import json

from synthetic_derivatives.packaging.database import load_bsm_greeks_inputs
from synthetic_derivatives.verifier.bsm_market_greeks import verify_market_greeks_submission


def test_exact_market_greeks(package_root, agent_submission):
    inputs = load_bsm_greeks_inputs(package_root / "public/task.duckdb")
    config = json.loads((package_root / "verifier/oracle_config.json").read_text(encoding="utf-8"))
    verify_market_greeks_submission(inputs, agent_submission, config)
