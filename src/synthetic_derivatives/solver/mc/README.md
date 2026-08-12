# Solver-side L3 Monte Carlo

This package is the planned numerical implementation boundary for the L3
Monte Carlo BSM task family. It is currently a scaffold: no estimator API is
implemented or exported yet.

Current repository state (2026-08-12): this directory contains only
`__init__.py` and this README. There is no L3 task contract, draw bank,
authoring module, solver entry point, verifier, runtime profile, schema,
package materializer, or L3-specific test suite. Importability must not be
interpreted as a usable Monte Carlo implementation.

## Mathematical boundary

Before an estimator is added, its versioned task contract must freeze the
pricing measure and numeraire identity, currency, valuation timestamp,
day-count/time axis, units, dtype, ordered reduction law, bumps, draw semantics,
and canonicalization checkpoints. Within the first L3 release:

- the known state at valuation is the positive ex-dividend spot plus the
  declared European option and constant task inputs `r`, `q`, market-implied
  `sigma`, and positive maturity `T`;
- evolution is under the task-declared risk-neutral measure `Q`, conditional on
  those public inputs and the ordered frozen base-normal draws;
- the terminal transition is the exact constant-coefficient BSM lognormal law,
  not Euler time stepping;
- Solver code must not read or accept `P`-measure drift, diffusion nodes,
  dependence, calibration state, or random streams;
- fixed-draw Monte Carlo realizations are the task target; the sibling
  [analytic BSM kernel](../analytic_and_implied_greeks_iv/README.md) is
  development QA only;
- raw estimator values remain unrounded until the shared output-contract layer
  applies unit scaling and canonical serialization.

The frozen draw bank belongs to the public task input. Authoring will create it;
Solver will consume it in explicit `draw_index` order through the declared
runtime interface and will not resample it.

## Planned module mapping

The implementation plan's shared `mc/` suggestion is mapped inside the Solver
boundary so the independent trusted verifier never imports Solver numerics.
Files are added with their corresponding acceptance gate:

| Planned responsibility | Repository path | Gate |
|:---|:---|:---|
| Inputs, units, bumps, draw/order and output contract | `synthetic_derivatives/tasks/l3_mc_greeks.py` | Step 1 |
| Ordered draw handling | `synthetic_derivatives/solver/mc/draws.py` | Step 2 |
| Exact terminal BSM transition | `synthetic_derivatives/solver/mc/terminal_bsm.py` | Step 2 |
| European payoff contributions | `synthetic_derivatives/solver/mc/payoffs.py` | Step 2 |
| Plain/antithetic reduction and pair-level standard error | `synthetic_derivatives/solver/mc/statistics.py` | Step 2 |
| Monte Carlo price orchestration | `synthetic_derivatives/solver/mc/pricing.py` | Step 2 |
| CRN bump-and-revalue Greeks | `synthetic_derivatives/solver/mc/finite_difference.py` | Step 3 |
| Pathwise estimators | `synthetic_derivatives/solver/mc/pathwise.py` | Step 4 |
| Likelihood-ratio estimators | `synthetic_derivatives/solver/mc/likelihood_ratio.py` | Step 4 |
| Rho/Theta and additive portfolio aggregation | `synthetic_derivatives/solver/mc/portfolio.py` | Step 7 |

Authoring draw-bank/task-record code will live in
`synthetic_derivatives/authoring/l3_mc_greeks.py`; the Solver entry point in
`synthetic_derivatives/solver/l3_mc_greeks.py`; and the independently implemented
trusted reference in `synthetic_derivatives/verifier/l3_mc_greeks.py`.
An L3 package must use a separately versioned materializer such as
`synthetic_derivatives/packaging_l3_mc_greeks/`; it must not silently reuse the
analytic/visible-IV package contract.

## Repository compatibility decisions

- `L3a` through `L3g` are curriculum labels, not values of the repository's
  seven-dimensional reasoning axis `L`. A concrete Monte Carlo variant must be
  registered through the compatibility registry with numerical-method axis
  `A6`.
- Unit and integration tests remain in `tests/unit/` and `tests/integration/`.
  Hard-verifier rejection tests use the existing `tests/verifier_robustness/`.
  When an L3 package exists, its package tests belong in a distinct
  `tests/packaging_l3_mc_greeks/`; the existing
  `tests/packaging_analytic_and_implied_greeks_iv/` remains scoped to its
  current method family.
- Versioned task and package declarations remain flat JSON files under
  `configs/variants/` and `configs/task_packages/`; cross-boundary output
  contracts remain under `schemas/`.
- The implementation plan proposes NumPy and a DuckDB client, while the current
  Solver capability profiles deny both direct imports. Numerical implementation
  must first choose and version a compatible runtime profile and dependency lock;
  this scaffold does not change that security contract.
