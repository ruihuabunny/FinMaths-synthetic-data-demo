# Runtime environments

- Dependency locks and capability profiles are security contracts. Pin versions
  and change them through a new reviewed profile/version; do not broaden a task
  overlay beyond the global profile.
- Repository importability does not grant Agent access. Effective capabilities
  are the declared intersection of the global profile, task overlay, trusted
  adapters, call budgets, and mounted files.
- Solver environments exclude QuantLib, packaged pricing/Greek/IV APIs, network,
  dynamic installation, undeclared filesystem access, raw private databases and
  verifier modules unless a new task version explicitly says otherwise.
- Trusted verifier dependencies and source are never solver-visible. Repository
  process guards are executable tests, not a substitute for production OS or
  container isolation.
- An L3 Monte Carlo implementation that needs NumPy, draw-bank access, or a new
  resource budget must introduce a new environment profile and lock before use.

