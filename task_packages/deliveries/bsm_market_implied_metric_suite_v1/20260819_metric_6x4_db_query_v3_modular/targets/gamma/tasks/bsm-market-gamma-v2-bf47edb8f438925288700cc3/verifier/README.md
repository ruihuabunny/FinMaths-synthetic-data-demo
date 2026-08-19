# Package-local trusted verifier

This directory is self-contained project code. It imports only the Python
standard library plus the pinned third-party packages in `requirements.lock`;
it does not require the `synthetic_derivatives` source tree or wheel.

`runtime.py` is the stable public facade. The `_runtime/` package contains the
private database, input-model, independent-oracle, frozen-profile, and selected
submission-protocol modules. All implementation imports are package-relative,
and each generated leaf contains exactly one active submission protocol. This
intentional duplication keeps every task relocatable and independently
verifiable instead of depending on repository-side authoring code.

From the leaf task root, verify one submission with:

```bash
python -m pip install -r verifier/requirements.lock
BSM_GREEKS_SUBMISSION=/absolute/path/submission.json \
  python -B -m pytest -q verifier
```

The verifier reconstructs the one declared IV/Greek result from `task.duckdb`
and `verifier/oracle_config.json`. It does not read a packaged reference answer.
