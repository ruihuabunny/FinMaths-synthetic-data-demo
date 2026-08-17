# Model-family identity boundary

This package owns declarative, non-numerical model-family identity and the
fail-closed executable-capability boundary. The current registry contains only
`tdgbm_bsm`, with `M=0`, and validates the separate IDs for P dynamics, Q
pricing, measure mapping, persisted state, probability measure, numeraire,
time/day count, units, transition, dtype, RNG ordering, and canonicalization.
It also maps the existing BSM variant contracts into semantic task identity and
parses the independent executable-capability sidecar. The task-space registry
remains a design catalog and never grants execution by itself.

It does not import authoring generators, solver numerics, verifier numerics, or
package builders. Trusted authoring dispatch lives in
`synthetic_derivatives.authoring.backends`; task execution is authorized only by
sidecar entries backed by explicit implementation and test evidence.
