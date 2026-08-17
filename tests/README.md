# Test map

| Suite | Responsibility |
|:---|:---|
| `unit/` | Config validation, exact P-dynamics, dependence, option chain, task contracts, stdlib BSM/IV, task-space/mutation/curriculum |
| `integration/` | Solver versus independent verifier, frozen parent → public child replay and authoring boundary |
| `public/` | Checked-in snapshot, public schema/template and read-only SQL behavior |
| `packaging_analytic_and_implied_greeks_iv/` | BSM source packages, v2/v3 tools, negative submissions, runtime isolation, leakage, views and portable suites |
| `verifier_robustness/` | Reserved family-level adversarial verifier cases |

Run the full suite from the repository root:

```bash
.venv/bin/python -m pytest
```

For focused work, start with the nearest unit or package suite and then run the
integration boundary it affects. See [`unit/README.md`](unit/README.md),
[`public/README.md`](public/README.md), and the
[packaging suite README](packaging_analytic_and_implied_greeks_iv/README.md).
