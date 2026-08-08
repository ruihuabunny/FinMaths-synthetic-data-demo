# F2A v5 Full-Trajectory Verifier Rework Handoff

**Revision date:** 2026-08-08  
**Target branch reviewed:** `f2a-2nd-revised`  
**Primary target:** `src/synthetic_derivatives/verifier` and all contracts that feed it

## 1. Purpose of this rework

The current v4 implementation is a correct executable-arbitrage scanner for a single option-chain slice. It should be preserved as the Stage-3 baseline, but it is not yet the complete F2A agent task now specified.

The complete F2A trajectory is:

\[
\boxed{
\text{node-based underlying regression}
\rightarrow
\text{BSM }d_1,d_2\text{ price-term regression}
\rightarrow
\text{mutation localisation}
\rightarrow
\text{X/U/T executable-arbitrage certification}
}
\]

This changes the public data, solver output, submission schema, oracle metadata and verifier semantics. It is therefore a new task version, not a small patch to the frozen v4 identity.

## 2. Non-negotiable design decisions

Codex must preserve the following decisions.

1. The physical dynamics generator remains node-based. Drift and diffusion are deterministic piecewise-linear functions between known event-node dates.
2. The public task exposes node dates or day offsets, interpolation/extrapolation conventions and fitting conventions, but never exposes node values.
3. The Agent only estimates node heights. It does not perform changepoint detection or choose arbitrary knots.
4. Option quotes are generated from BSM plus controlled quote noise before arbitrage mutations.
5. BSM identification uses a per-contract option time series and the constrained BSM \(d_1,d_2\) price terms. It is not defined as independent implied-volatility inversion of every quote row.
6. BSM coefficients are fixed by the pricing formula. Do not freely regress two arbitrary coefficients in front of the two price terms.
7. Implied-volatility inversion may exist as an optional diagnostic or derived output, but it is not the core Stage-2 estimator or verifier target.
8. A large BSM residual identifies a model inconsistency or suspected quote mutation. It is not, by itself, an arbitrage certificate.
9. Only the existing X/U/T cash-flow logic may certify executable arbitrage after bid/ask spreads, option fees, underlying execution costs, funding and dividends.
10. The hard verifier checks the output of a fully specified estimator and solver. It must not demand that a finite random path reproduce hidden generator parameters exactly.

## 3. Versioning decision

### 3.1 Preserve v4

Keep v4 as an immutable Stage-3 baseline:

\[
\text{single valuation-date slice}
\rightarrow
\text{X/U/T scan}
\rightarrow
(\texttt{arbitrage\_opportunity},\texttt{arbitrage\_type}).
\]

Do not silently redefine its fixtures, schema or task identity.

### 3.2 Add v5

Create v5 for the full F2A trajectory. A suggested variant name is:

```text
bsm_model_reconstruction_arbitrage_finding_f2a_v5
```

The exact name may follow repository conventions, but it must have a distinct variant ID, schema version, fixtures and documentation.

## 4. Current code that must be protected

The following v4 logic remains the mathematical core of Stage 3:

| Existing path | Required treatment |
|---|---|
| `src/synthetic_derivatives/verifier/f2a_contract.py` | Preserve transaction-cost and executable-cash-flow primitives. Extend only when a v5 contract requires an explicit new field. |
| `src/synthetic_derivatives/verifier/f2a_oracle.py` | Preserve the full X/U/T catalogue scan. Treat it as the v5 \(V_3\) oracle. Do not mix regression fitting into this module. |
| Existing X/U/T fixtures and tests | Keep as v4 regression tests and reuse them as Stage-3 tests under v5 where applicable. |

The new regression layers should wrap or precede these modules, not replace their cash-flow mathematics.

## 5. Target v5 verifier architecture

The complete verifier is:

\[
\boxed{V=V_1\land V_2\land V_3}
\]

where:

| Layer | Verification target |
|---|---|
| \(V_1\) | Underlying drift/diffusion node regression |
| \(V_2\) | Per-option time-series BSM \(d_1,d_2\) price-term regression and mutation localisation |
| \(V_3\) | Existing X/U/T executable-arbitrage rescan and certificate verification |

`src/synthetic_derivatives/verifier/f2a.py` should orchestrate these three independent layers and return structured failure information for each layer.

## 6. Stage 1: node-based underlying regression

### 6.1 Public model contract

For underlying \(u\), publish the ordered event-node dates or day offsets:

\[
\tau_{u,0}<\tau_{u,1}<\cdots<\tau_{u,J}.
\]

Using the deterministic piecewise-linear hat basis \(\phi_{u,j}(t)\):

\[
\mu_u(t)=\sum_{j=0}^{J}\alpha_{u,j}\phi_{u,j}(t),
\qquad
\sigma_u(t)=\sum_{j=0}^{J}\beta_{u,j}\phi_{u,j}(t),
\qquad \beta_{u,j}>0.
\]

The Agent estimates only:

\[
\alpha_u=(\alpha_{u,0},\ldots,\alpha_{u,J}),
\qquad
\beta_u=(\beta_{u,0},\ldots,\beta_{u,J}).
\]

### 6.2 Exact interval quantities

For each observed close-to-close interval \([t_k,t_{k+1}]\), precompute from the public node grid:

\[
A_{k,j}=\int_{t_k}^{t_{k+1}}\phi_j(t)\,dt,
\qquad
Q_{k,j\ell}=\int_{t_k}^{t_{k+1}}\phi_j(t)\phi_\ell(t)\,dt.
\]

For log return

\[
y_k=\log\frac{S_{t_{k+1}}}{S_{t_k}},
\]

the exact conditional mean and variance under the generator parameterisation are:

\[
M_k(\alpha,\beta)
=A_k^\top\alpha-\frac12\beta^\top Q_k\beta,
\qquad
V_k(\beta)=\beta^\top Q_k\beta.
\]

### 6.3 Canonical fitting loss

Use one specified regression-style heteroskedastic loss, for example:

\[
\mathcal L_u(\alpha,\beta)
=
\sum_k
\left[
\log V_k(\beta)
+
\frac{\left(y_k-M_k(\alpha,\beta)\right)^2}{V_k(\beta)}
\right].
\]

The v5 configuration must freeze:

- time origin and day-count convention;
- node dates/day offsets;
- interpolation and flat-extrapolation rules;
- drift and volatility parameter bounds;
- loss weights, if any;
- solver, initialization and stopping rule;
- tie-breaking and rejection rules;
- output dtype, decimal quantization and serialization order.

The verifier recomputes this canonical estimator from public data. It compares the submitted fitted node values and canonical loss to the verifier result. It does not compare them directly with the hidden generator node values.

## 7. Stage 2: BSM \(d_1,d_2\) price-term regression

### 7.1 Unit of fitting

The fitting unit is one fixed option contract observed across multiple valuation dates. A contract key must be stable, for example:

```text
(underlying_id, option_type, strike, expiry, settlement_style)
```

Each contract time series must contain the contemporaneous underlying price, rate/dividend inputs, time to maturity and observable quote used by the estimator.

### 7.2 Call price terms

For a call option:

\[
d_{1,t}
=
\frac{
\log(S_t/K)+(r_t-q_t+\tfrac12\sigma_t^2)\tau_t
}{
\sigma_t\sqrt{\tau_t}
},
\qquad
d_{2,t}=d_{1,t}-\sigma_t\sqrt{\tau_t}.
\]

Define the two discounted price terms:

\[
X_{1,t}=S_te^{-q_t\tau_t}\Phi(d_{1,t}),
\qquad
X_{2,t}=Ke^{-r_t\tau_t}\Phi(d_{2,t}).
\]

The BSM restriction is:

\[
C_t^{\mathrm{obs}}
=X_{1,t}(\theta)-X_{2,t}(\theta)+\varepsilon_t.
\]

Equivalently, the intercept and coefficients are fixed:

\[
\text{intercept}=0,
\qquad
(\gamma_1,\gamma_2)=(1,-1).
\]

Do not fit arbitrary free \(\gamma_1,\gamma_2\) and call that BSM identification.

### 7.3 Put price terms

For a put:

\[
P_t^{\mathrm{obs}}
=
Ke^{-r_t\tau_t}\Phi(-d_{2,t})
-
S_te^{-q_t\tau_t}\Phi(-d_{1,t})
+\varepsilon_t.
\]

The implementation must use the option-type-specific formula rather than deriving puts from a mutated call quote.

### 7.4 Time-varying pricing volatility

If the risk-neutral volatility is node-based and deterministic, use integrated variance:

\[
W_{t,T}=\int_t^T\sigma_{\mathbb Q}^2(s)\,ds.
\]

Then:

\[
d_1
=
\frac{
\log(S_t/K)+\int_t^T(r_s-q_s)\,ds+\tfrac12W_{t,T}
}{
\sqrt{W_{t,T}}
},
\qquad
d_2=d_1-\sqrt{W_{t,T}}.
\]

Fit the public parameterisation of \(\theta\), such as pricing-volatility node heights, by minimizing the fixed BSM price residual loss:

\[
\mathcal L_{u,c}^{\mathrm{BSM}}(\theta)
=
\sum_t w_t
\left(P_{u,c,t}^{\mathrm{obs}}-P_{u,c,t}^{\mathrm{BSM}}(\theta)\right)^2.
\]

The exact loss, robustification rule, bounds, optimizer, initialization, weights, rounding and threshold must be frozen in the v5 task configuration.

### 7.5 Meaning of the result

The Stage-2 output should include:

- `model_family: BSM`;
- fitted pricing parameters or volatility node values;
- canonical loss and residual sequence;
- dates/rows whose residuals exceed the specified quote-noise criterion;
- suspected mutation row IDs or mutation-group IDs;
- recovered counterfactual clean quote computed from the fitted BSM model.

Independent per-row implied-volatility inversion is optional. It may be reported for diagnostics, but it must not replace the time-series regression, because allowing an independent volatility for every quote can absorb nearly any quote that lies within static price bounds.

## 8. Stage 2.5: mutation localisation

Mutation localisation must separate normal quote noise from injected structural mutations.

Let:

\[
r_{u,c,t}=P_{u,c,t}^{\mathrm{obs}}-widehat P_{u,c,t}^{\mathrm{BSM}}.
\]

Use the frozen quote-noise scale and grouping rule to identify abnormal rows. The canonical output should record:

```yaml
mutation_groups:
  - mutation_group_id:
    affected_row_ids:
    affected_dates:
    observed_quotes:
    recovered_clean_quotes:
    residuals:
    localisation_score:
    proposed_mutation_family:
```

Do not equate every model residual with arbitrage. The localised rows are inputs to the Stage-3 market-arbitrage rescan.

## 9. Stage 3: X/U/T executable-arbitrage verifier

The existing public-child rescan remains authoritative for executable arbitrage.

The signature is:

\[
(X,U,T)\in\{0,1\}^3,
\]

so valid signatures remain:

```text
000, 001, 010, 011, 100, 101, 110, 111
```

Stage 3 must recompute opportunities from public mutated quotes and include all existing execution economics:

- bid/ask side used by each leg;
- option fees;
- underlying transaction costs;
- funding and discounting;
- dividends or carry inputs;
- initial net cash flow;
- terminal payoff certificate;
- any family-specific feasibility condition.

The Agent-submitted residual or mutation label cannot override the Stage-3 rescan. A submitted arbitrage exists only if the verifier reconstructs the same valid executable certificate.

## 10. Public/private data contract

### 10.1 Public fields required for Stage 1

Publish:

```yaml
physical_fitting_contract:
  time_origin:
  time_axis:
  day_count:
  drift_function_type: piecewise_linear
  diffusion_function_type: piecewise_linear
  drift_node_dates_or_offsets:
  diffusion_node_dates_or_offsets:
  interpolation: linear
  extrapolation: flat
  drift_bounds:
  diffusion_bounds:
  canonical_loss:
  canonical_solver:
  output_precision:
```

Hide:

- drift and diffusion node values;
- generator seeds;
- sampled random shocks;
- sampling hyperparameters that reveal the answer;
- private joint-dependence/correlation provenance unless explicitly made part of the task.

### 10.2 Public fields required for Stage 2

Publish:

```yaml
pricing_fitting_contract:
  candidate_model_family: BSM
  option_contract_keys:
  rate_curve_inputs:
  dividend_or_carry_inputs:
  pricing_volatility_node_dates_or_offsets:
  interpolation:
  extrapolation:
  parameter_bounds:
  canonical_loss:
  canonical_solver:
  quote_observation_rule:
  quote_noise_rule:
  mutation_localisation_threshold:
  output_precision:
```

Hide:

- pricing-volatility node values;
- clean pre-noise quotes;
- clean pre-mutation quotes;
- mutation targets, types and sizes;
- pricing seeds and RNG provenance;
- any metadata field containing oracle physical or pricing dynamics.

### 10.3 Remove current answer leakage

The v5 public view must not expose complete `physical_dynamics` or `pricing_dynamics` objects when those objects include node values. Audit every nested field under `solver_visible.pricing_metadata`; renaming a private object is insufficient if the values remain serialised.

Add a negative visibility test that recursively searches the public artifact for hidden node values, seeds, clean quotes and mutation metadata.

## 11. Public-child materialisation changes

The current single-underlying, single-date option slice cannot support Stages 1 and 2.

The v5 child must materialise, for each selected bootstrap sample:

1. the underlying histories needed for all selected underlyings;
2. stable option contract histories across multiple valuation dates;
3. the public rate/dividend inputs used for each observation;
4. node dates or offsets and the fitting contract;
5. mutated observable bid/ask quotes;
6. stable row IDs and mutation-group-compatible keys;
7. the Stage-3 valuation slice or a clear deterministic rule selecting it.

If the task uses 8 underlyings per bootstrap sample, all eight must be represented consistently across the public manifest, solver trajectory and verifier. Do not silently collapse the child back to one underlying.

## 12. Node-horizon identifiability gate

The observed underlying history must identify every scored node value.

The current reviewed setup used a 65-business-date history while some seven-node terminal offsets lay far beyond the observed horizon. Those future nodes cannot be estimated from the public path.

Use one explicit v5 rule:

1. preferably sample all scored event nodes inside the observable history; or
2. score only active nodes whose basis support intersects the observed transitions, with the boundary rule precisely defined; or
3. lengthen the history to cover the last scored node.

Recommended default: create an F2A-specific parent in which 4–7 event nodes fall within the observation period. Add an authoring rejection gate for any scored node without sufficient support.

## 13. Quote-field consistency

The observable price used by Stage 2 must be explicit and must reflect mutation.

Recommended canonical rule:

\[
P_t^{\mathrm{obs}}=\frac{\mathrm{bid}_t+\mathrm{ask}_t}{2}.
\]

Choose exactly one implementation:

- compute the fitting price from mutated `bid` and `ask` and omit public `mid`; or
- mutate `bid`, `ask` and `mid` consistently and verify `mid = (bid + ask)/2` under the chosen rounding rule.

Never expose an unchanged clean `mid` next to mutated bid/ask quotes. That would leak the counterfactual clean price and defeat Stage 2.

## 14. Suggested v5 submission schema

```yaml
task_id:
variant_id:

underlying_regressions:
  - underlying_id:
    node_dates_or_offsets:
    fitted_drift_node_values:
    fitted_diffusion_node_values:
    canonical_loss:
    solver_status:

option_model_regressions:
  - contract_id:
    underlying_id:
    option_type:
    strike:
    expiry:
    model_family: BSM
    fitted_pricing_parameters:
    canonical_loss:
    residuals_by_row_id:
    abnormal_row_ids:

mutation_groups:
  - mutation_group_id:
    affected_row_ids:
    affected_dates:
    observed_quotes:
    recovered_clean_quotes:
    residuals:
    proposed_mutation_family:

arbitrage:
  signature: XUT
  opportunities:
    - family:
      legs:
      gross_initial_cashflow:
      option_fees:
      underlying_costs:
      funding_and_dividend:
      net_initial_cashflow:
      terminal_payoff_certificate:

verifier_digest:
```

Do not retain an `orm_answer` field as the only semantic output for v5.

## 15. Required code changes

| Path or component | Required change |
|---|---|
| `src/synthetic_derivatives/verifier/f2a.py` | Turn into a v5 orchestrator for \(V_1\), \(V_2\) and \(V_3\), while keeping a v4-compatible entry point if required. |
| `src/synthetic_derivatives/verifier/f2a_contract.py` | Preserve existing execution primitives; extend schema validation only when necessary. |
| `src/synthetic_derivatives/verifier/f2a_oracle.py` | Preserve as the Stage-3 X/U/T oracle. Keep regression logic out. |
| New Stage-1 verifier module | Recompute the canonical physical node regression and exact interval integrals. |
| New Stage-2 verifier module | Recompute BSM \(d_1,d_2\) price-term fitting, residuals and mutation localisation. |
| `src/synthetic_derivatives/solver/f2a.py` | Expand from an arbitrage-only scan to the complete three-stage trajectory. Prefer versioned solver entry points over silently changing v4. |
| `src/synthetic_derivatives/authoring/f2a_child_materializer.py` | Publish underlying and stable option time series, fitting contracts and a deterministic Stage-3 slice. |
| `src/synthetic_derivatives/authoring/schema.py` | Split solver-visible fitting conventions from private DGP/oracle provenance. Remove answer leakage. |
| `schemas/trajectory.schema.json` | Add typed Stage-1, Stage-2, mutation and Stage-3 outputs. Version the schema if frozen. |
| `schemas/submission-v4.schema.json` | Leave v4 frozen; add a v5 submission schema rather than changing v4 in place. |
| `configs/variants/bsm_arbitrage_finding_f2a_v4.json` | Preserve v4; add a separate v5 configuration declaring all fitting and verifier conventions. |
| Authoring/oracle metadata | Store hidden generator parameters, clean quotes, noise and mutation provenance for auditing, but do not publish them to the Agent. |
| Documentation | State clearly that residual detection and executable arbitrage are distinct tasks. |

## 16. Hard-verification semantics

Exact verification means exact agreement after both sides run the same frozen algorithm and canonical serializer. It does not mean raw floating-point equality at every internal optimization step.

For every scored numeric output, define:

1. input dtype;
2. deterministic preprocessing order;
3. solver and solver version/contract;
4. convergence and failure rule;
5. canonical decimal quantization;
6. ordering of underlyings, contracts, dates, rows and mutation groups;
7. canonical JSON representation;
8. exact comparison after canonicalization.

Reject or regenerate an authored sample when:

- the design is rank deficient;
- a scored node has no observational support;
- the canonical solver fails or produces ambiguous minima under the frozen rule;
- a parameter is unintentionally pinned to a bound;
- the loss Hessian/conditioning violates the configured identifiability gate;
- pre-mutation quote noise already creates an unintended X/U/T signature;
- the requested post-mutation signature is not reproduced by the authoritative Stage-3 oracle.

## 17. Required tests

### 17.1 Stage-1 tests

- Exact basis and interval-integral tests, including intervals crossing weekends and event nodes.
- Flat-extrapolation tests.
- Synthetic recovery tests against the canonical estimator output.
- Node-order, bounds, rounding and serializer tests.
- Node-horizon rejection tests.
- Multi-underlying bootstrap isolation tests.

### 17.2 Stage-2 tests

- Call and put \(d_1,d_2\) price-term formula tests.
- Constant-volatility and node-based integrated-variance tests.
- Fixed BSM coefficient tests: intercept \(0\), coefficients \((1,-1)\) for calls and the correct put form.
- Tests proving that per-row IV inversion is not used as the core estimator.
- Quote-noise-only cases that remain below the mutation threshold.
- Known injected mutations that localise to the correct rows/groups.
- Counterfactual clean-quote reconstruction tests.
- Expiry, near-zero maturity, extreme moneyness and invalid-input gates.

### 17.3 Visibility tests

- Public artifacts contain node dates but not node values.
- Public artifacts contain fitting conventions but not generator seeds.
- Public artifacts do not contain clean pre-mutation quotes.
- Public `mid`, if present, cannot reveal the clean quote.
- Private metadata remains available to authoring/audit code but cannot enter the solver-visible projection.

### 17.4 Stage-3 preservation tests

- All existing X/U/T catalogue tests continue to pass unchanged for v4.
- v5 Stage 3 reproduces the same certificate when given the same public slice.
- BSM residuals without executable arbitrage do not pass \(V_3\).
- Executable arbitrage with an imperfect model label is still decided from the authoritative public-quote cash flows according to the submission contract.
- Transaction costs can switch intended gross violations off exactly as specified.

### 17.5 End-to-end tests

- One fixture for each of the eight `XUT` signatures.
- Full v5 solver output passes \(V_1\land V_2\land V_3\).
- Perturbing any scored drift/diffusion node result fails \(V_1\).
- Perturbing a fitted BSM parameter, residual or mutation row fails \(V_2\).
- Perturbing an arbitrage leg, execution side or net cash flow fails \(V_3\).
- Reordering semantically ordered arrays or changing precision fails or canonicalizes according to the documented rule.
- Re-running the same task produces byte-identical canonical submissions and verifier outcomes.

## 18. Recommended implementation sequence

1. Freeze and tag the current v4 behavior and tests.
2. Add the versioned v5 config and schemas without changing v4 fixtures.
3. Split public fitting conventions from private DGP metadata.
4. Create an F2A-specific parent/child horizon supporting all scored nodes.
5. Materialise underlying histories and stable option contract histories.
6. Implement the canonical Stage-1 estimator and verifier.
7. Implement the canonical Stage-2 BSM \(d_1,d_2\) estimator, residuals and mutation localisation.
8. Compose the existing Stage-3 X/U/T oracle as \(V_3\).
9. Expand the solver trajectory and submission output.
10. Add visibility, identifiability, determinism and end-to-end tests.
11. Regenerate v5 fixtures only after all authoring gates pass.
12. Update documentation and AGENTS guidance to distinguish model residuals from executable arbitrage.

## 19. Codex completion checklist

- [ ] v4 remains runnable and its frozen X/U/T tests still pass.
- [ ] v5 has a distinct task, config and submission-schema identity.
- [ ] Public node dates are visible; physical and pricing node values are hidden.
- [ ] Every scored node is identifiable from the public observation horizon.
- [ ] The public child contains the required underlying and option time series.
- [ ] Stage 1 fits only node heights using the exact generator-consistent interval contract.
- [ ] Stage 2 uses constrained BSM \(d_1,d_2\) price-term regression across each contract time series.
- [ ] Per-row IV inversion is optional only, not the core identification method.
- [ ] Mutated quotes cannot leak an unchanged clean `mid`.
- [ ] Residual-based mutation localisation is separate from arbitrage certification.
- [ ] Existing cash-flow and X/U/T logic is reused as Stage 3.
- [ ] The v5 verifier recomputes all three stages from public data.
- [ ] Numeric comparison is exact after documented canonicalization.
- [ ] Visibility and anti-leakage tests pass recursively.
- [ ] One deterministic end-to-end fixture exists for every X/U/T signature.

## 20. Final acceptance criterion

The rework is complete only when a solver that merely reads leaked node values, performs independent IV inversion row by row, or reports a BSM residual as an arbitrage opportunity can no longer pass.

A valid v5 submission must independently demonstrate:

\[
\boxed{
\begin{aligned}
&\text{public-path node regression is correct;}\\
&\text{BSM }d_1,d_2\text{ time-series fitting and mutation localisation are correct;}\\
&\text{the submitted X/U/T opportunity is executable after all costs.}
\end{aligned}
}
\]
