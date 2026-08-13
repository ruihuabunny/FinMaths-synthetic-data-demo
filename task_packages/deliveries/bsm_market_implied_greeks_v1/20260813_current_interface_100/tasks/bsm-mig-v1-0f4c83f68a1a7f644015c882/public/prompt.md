# Market-implied BSM unit Greeks

Produce a complete submission for every public input row.

Call `query_greeks_task_contract_v1` and `query_greeks_task_inputs_v1` exactly
once each. Treat their returned data, `public/submission.schema.json`, and
`public/runtime_contract.json` as the complete authoritative specification.
Call `submit_greeks_submission_v1` exactly once.
