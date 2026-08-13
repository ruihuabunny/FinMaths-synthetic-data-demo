# Solver runtime allowlist

This directory defines the dependency boundary for solver-visible BSM tasks.

Allowed runtime:

- the pinned Python interpreter and its standard library;
- task-declared trusted query and submission adapters with fixed call budgets;
- repository artifacts explicitly copied into the public solver bundle.

`duckdb==1.5.5` exists in the trusted query-adapter image, but the
`bsm_market_implied_greeks_v1` task overlay does not grant the Agent a raw
DuckDB connection. Effective capabilities are computed as the intersection of
`capabilities.global_v2.json` and `capabilities.bsm_greeks_v1.json`; the overlay
can only remove capabilities or reduce budgets.

Not allowed in the solver image:

- QuantLib, py_vollib, mibian, rateslib, or packaged pricing/Greek/IV APIs;
- authoring, verifier, mutation, private lineage, or hidden-oracle modules;
- dynamic package installation, network access, or undeclared filesystem reads.
- raw database connections, `ATTACH/COPY/INSTALL/LOAD`, and process spawning.

QuantLib remains available only to trusted authoring and verifier code. The
private verifier contract freezes conventions and canonicalization. Pricing and
Greek formulas and operation order are deliberately absent from every
solver-visible artifact; a private publication gate instead requires independent
standard-library and QuantLib implementations to canonicalize identically.

## Repository source versus Agent runtime

The maintained repository kernels for analytic BSM Greeks, visible-price IV,
and market-implied Greeks live under
[`src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/`](../../src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/README.md).
That path is a developer-facing source boundary, not an additional Agent
capability. A task may expose only the source artifact and imports declared by
its effective runtime contract; the accepted golden task uses a standalone
audited reference artifact rather than granting access to the whole repository
package. That reference artifact is observable in train/dev and authoring views,
but it is not mounted in the evaluation runtime. Trusted QuantLib verifier code
remains separately hidden.

The repository reference harness executes solver source in a separate `spawn`
process. It enforces the frozen import/builtin policy, counted trusted adapters,
submission call/size limits, one-CPU affinity, `RLIMIT_AS`, `RLIMIT_CPU`, and a
parent-side wall timeout. These controls make the checked-in reference replay an
executable contract; they are not a substitute for deployment isolation. A
production runner must independently enforce network, mount/filesystem, process,
user/namespace, and resource policy at the OS or container boundary.

The planned `solver/mc` package has no runnable estimator and inherits no
capability by directory placement. An L3 implementation that needs NumPy,
ordered draw access, or a different resource budget must introduce a new
versioned environment/profile/lock before it can be exposed to an Agent.
