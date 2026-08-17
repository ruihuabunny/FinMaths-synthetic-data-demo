# Model-family identity boundary

This package owns declarative, non-numerical model-family identity and the
fail-closed executable-capability boundary. The current registry contains only
`tdgbm_bsm`, with `M=0`, and validates the separate IDs for P dynamics, Q
pricing, measure mapping, persisted state, probability measure, numeraire,
time/day count, units, transition, dtype, RNG ordering, and canonicalization.
It also maps the existing BSM variant contracts into semantic task identity and
parses the independent executable-capability sidecar. The task-space registry
remains a design catalog and never grants execution by itself.

## Current family

[`tdgbm_bsm_v1.json`](../../../configs/model_families/tdgbm_bsm_v1.json) is the
authoritative identity document for the only implemented family:

- historical underlying evolution is deterministic time-inhomogeneous GBM under
  the declared physical measure `P`;
- European option pricing is BSM under the declared USD money-market `Q` and
  money-market-account numeraire;
- the current Girsanov mapping changes drift while preserving the explicitly
  declared diffusion/covariance baseline;
- the observation clock and pricing maturity use the declared calendar axis and
  Actual/365 Fixed day count;
- published rounded closes form the persisted restart state;
- physical volatility, pricing volatility, and quote-implied volatility remain
  distinct semantic objects.

The family name is deliberately narrower than generic “GBM/BSM.” Stochastic
volatility, local volatility, stochastic rates, hybrid state, or a different
measure mapping require a new model-family identity and real implementation
evidence; they are not flags on `tdgbm_bsm`.

## Package responsibilities

| Module | Responsibility |
|:---|:---|
| `contracts.py` | Closed stochastic-identity and model-family records |
| `registry.py` | Fail-closed family lookup and `model_family_id` ↔ `M` validation |
| `capabilities.py` | Exact capability key, status/evidence validation, task-pool gating and deterministic diagnostics |
| `adapters.py` | Explicit mappings from accepted BSM variant identities to semantic TaskSpec v3 |

The capability key is
`(model_family_id, task_family_id, task_kind_id, method_id,
solver_interface_id)` plus an exact output contract. The checked-in sidecar
registers the current analytic-Greeks and scalar-IV library implementations, and
the combined/static/query-v3 portable BSM paths. Missing keys, missing required
evidence, wrong output contracts, family/`M` disagreement, or design-catalog-only
model entries are denied rather than inferred.

`library_implemented` and `portable_verified` describe repository evidence; they
do not replace Agent runtime profiles. A portable task must still satisfy its
versioned environment, mounted-view, dependency, trusted-tool, and budget
contracts.

It does not import authoring generators, solver numerics, verifier numerics, or
package builders. Trusted authoring dispatch lives in
`synthetic_derivatives.authoring.backends`; task execution is authorized only by
sidecar entries backed by explicit implementation and test evidence.

Acceptance coverage lives in
[`test_model_family_registry.py`](../../../tests/unit/test_model_family_registry.py),
[`test_executable_capability_registry.py`](../../../tests/unit/test_executable_capability_registry.py),
and
[`test_catalog_runtime_separation.py`](../../../tests/integration/test_catalog_runtime_separation.py).
