# Agent Guidelines

## Active development database

- Unless a task explicitly selects another snapshot, base ongoing development,
  data inspection, examples, and end-to-end verification on
  `snapshots/generated/quantlib_bsm_metals_option_chain_smoke_v1_20260807.duckdb`.
- Use `configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json` as the
  legacy generator contract for that database and
  `snapshots/generated/quantlib_bsm_metals_option_chain_smoke_v1_20260807.manifest.json`
  as its materialized snapshot metadata.
- Do not silently fall back to
  `snapshots/public/quantlib_bsm_smoke_v1.duckdb` or another convenient database
  when the active development database is expected. If the active database is
  missing, report it; the current authoring pipeline must not regenerate that
  legacy identity because authoring-time IV solving has been retired.
- The active database is snapshot
  `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3`, revision `1`, currently in
  `DRAFT` state. Do not describe it as frozen or use it where a frozen parent is
  mathematically required. Treat it as read-only under the current pipeline.
- New authoring uses
  `configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json`, config
  schema `1.6.0`, generator `0.8.0`, and snapshot
  `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v4`. It preserves the market
  model but does not solve or persist IV answers. Materialize it to a new file;
  never append those rows under the legacy v3 identity.

## Follow mathematical facts

- Treat mathematical facts as hard constraints. Implementations, tests, fixtures,
  schemas, and backward-compatibility concerns must conform to the mathematics;
  never alter the mathematics to preserve an existing implementation or test.
- Before implementing a stochastic model, state its probability measure, state
  variables, filtration/conditioning information, time axis, units, discretization
  or exact-transition law, and parameter semantics.
- Do not silently substitute a convenient model for the declared model. If a
  requirement conflicts with a mathematical fact or leaves the stochastic model
  materially under-specified, stop and make the conflict explicit before coding.
- Distinguish an exact transition from an approximation, and document any chosen
  approximation together with the assumptions under which it is valid.

## Financial-market mathematical contract

### Measures, numeraires, and information

- Every stochastic process must identify its probability measure. Historical
  underlying paths used for returns, historical VaR, or ES live under the physical
  measure `P`. Arbitrage-free derivative prices live under a declared pricing
  measure `Q` associated with a declared numeraire. A measure label is part of a
  parameter's identity, not optional metadata.
- Under `P`, conditional expectations describe real-world evolution and include
  risk premia. Under `Q`, correctly discounted gains processes are local
  martingales. The change of measure can alter drift, volatility-state dynamics,
  jump compensators, correlations, and other model parameters; none may be copied
  from `P` to `Q` without an explicit model mapping.
- State exactly what is known at each transition. A Markov model may condition on
  the current spot and declared latent state. A stochastic-volatility, stochastic-
  rate, regime-switching, or jump model must persist and evolve every state needed
  for the next transition. Random state must never be replaced by a deterministic
  date function merely because the latter is easier to store.
- For a same-currency joint option market, all assets priced together must use one
  common `Q`, numeraire, currency, valuation timestamp, and shared rate-path
  context. Cross-currency, FX, and quanto claims require explicit numeraires and
  measure changes and must not be treated as same-currency claims.

### Physical underlying dynamics

- An authored underlying history is a `P`-measure process. A geometric diffusion
  baseline must be declared as

  `dS_t / S_t = mu_P(t, X_t) dt + sigma_P(t, X_t) dW_t^P`, with `S_t > 0`,

  where `X_t` denotes every additional state variable. State whether `S_t` is an
  ex-dividend price, total-return index, futures price, or another economic object.
  The meaning of drift and cash distributions depends on that choice.
- `physical_drift` is an annualized `P`-measure instantaneous expected return with
  units of inverse time. `physical_volatility` is an annualized `P`-measure
  instantaneous return standard deviation with units of inverse square-root time;
  its square is the instantaneous variance rate. Both units must use the declared
  clock/day-count convention.
- Physical volatility is a conditional model parameter, not a realized return and
  not the sample standard deviation of one observation. Realized squared returns
  are noisy observations whose conditional scale is governed by integrated
  variance.
- `initial_spot` is the initial condition at `start_date`:
  `S(start_date) = initial_spot`. Materialize it at that timestamp without applying
  a random transition from an invented earlier date. For each later observation,
  evolve conditionally from the preceding materialized state over the actual
  elapsed interval.
- For deterministic time-varying `mu(t)` and `sigma(t)`, the exact conditional
  transition over `[t_i, t_{i+1}]` is

  `log(S_{i+1}/S_i)
   = integral[mu(t) - 0.5 sigma(t)^2] dt
   + integral sigma(t) dW_t^P`.

  Its conditional distribution is normal with mean
  `integral[mu(t) - 0.5 sigma(t)^2] dt` and variance
  `integral sigma(t)^2 dt`. It can therefore be sampled with one independent
  standard normal `Z_i` as

  `S_{i+1} = S_i * exp(
      integral[mu(t) - 0.5 sigma(t)^2] dt
      + sqrt(integral sigma(t)^2 dt) * Z_i
  )`.
- A flat-coefficient GBM engine is distributionally exact for that deterministic
  interval only when

  `mu_eff = integral mu(t) dt / Delta t`, and
  `sigma_eff = sqrt(integral sigma(t)^2 dt / Delta t)`.

  Endpoint volatility, arithmetic-average volatility, or a parameter merely
  labelled "daily" does not give the same integrated variance. This reduction is
  exact only for the declared deterministic time-inhomogeneous GBM; it does not
  justify GBM as the market's universal physical model.
- If `mu(t)` or `sigma(t)` is piecewise linear, interpolate the instantaneous
  annualized parameter itself on the declared time axis. Integrate drift and
  variance across every interior node exactly. Flat extrapolation, if used, is a
  model assumption and must be recorded rather than inferred.
- A business-daily observation grid does not turn elapsed time into business-day
  units. A Friday-to-Monday transition spans the full calendar interval under the
  declared day count. It is one observed transition, not one calendar day and not
  three independent latent closes unless those latent states are explicitly
  simulated.
- For Brownian GBM, shocks on disjoint time intervals are independent conditional
  on the model state. Reusing a daily shock, drawing shocks according to row order,
  or changing the random partition when rows are appended changes the stochastic
  law and is forbidden unless declared by a different model.
- Quantizing each generated close before using it as the next state creates a
  different, rounded-state Markov chain. If exact continuous-state dynamics are
  claimed, retain unrounded internal state and quantize only published outputs. If
  rounded restart state is intentionally required for append invariance, declare
  that discretized path law explicitly.

### Stochastic volatility, local volatility, rates, and jumps

- A deterministic volatility curve, local volatility, stochastic volatility, and
  implied-volatility surface are different mathematical objects:

  - deterministic volatility is a known function of time;
  - local volatility is a function such as `sigma_loc(t, S_t)`;
  - stochastic volatility introduces a random state such as `V_t`;
  - implied volatility is an inverse-pricing quote indexed by strike and maturity.

  Never substitute one for another without an explicit theorem or calibration
  contract.
- A stochastic-volatility model must jointly evolve spot and variance, for example
  under `P`,

  `dS_t/S_t = mu_P(t, X_t) dt + sqrt(V_t) dW_t^{S,P}`,

  together with a declared equation for `V_t` and a declared instantaneous
  spot-variance correlation. The `P` and `Q` variance-process parameters and risk
  premia must be distinguished.
- A local-volatility path must evaluate the declared local-volatility function at
  the model's required state and time. It cannot be simulated by reading one IV at
  the same strike or by applying a piecewise deterministic volatility curve.
- A jump model must declare jump intensity, jump-size law, compensator, measure,
  and discretization or exact simulation rule. A diffusion shock cannot silently
  stand in for jumps.
- A stochastic-rate or hybrid model must jointly evolve rate state and its
  dependence with spot/volatility drivers. A flat rate is permitted only as an
  explicit simplified model, not as an accidental fallback.

### Multi-underlying dependence

- Dependence acts on underlying/model risk drivers, never on option identifiers,
  Greeks, contracts, or already-generated derivative prices. Correlating final
  derivative quotes does not construct a coherent joint market.
- A correlation matrix `R_t` must be symmetric, have unit diagonal, and be
  positive semidefinite. If ordinary Cholesky is the declared factorization, the
  matrix must be positive definite; a semidefinite matrix requires a deterministic
  factorization that supports it.
- For the factor-loading construction, freeze the driver order and use

  `D_t = diag(1 - ||lambda_i,t||^2) >= 0`,

  `R_t = Lambda_t Lambda_t^T + D_t`, and

  `Z_t = Lambda_t eta_t + sqrt(D_t) epsilon_t`,

  with independent standard-normal factor shocks `eta_t` and idiosyncratic shocks
  `epsilon_t`. This gives `Cov(Z_t) = R_t`. Matrix dtype, factorization, order, and
  time/regime policy are part of the model.
- `P`-measure and `Q`-measure dependence specifications require measure-qualified
  identities. Historical return correlation is not automatically risk-neutral
  dependence. For stochastic-volatility or hybrid models, preserve each asset's
  internal spot-volatility-rate driver block when coupling assets.
- Basket, index, spread, and other multi-asset payoffs must be priced from one
  joint underlying process, one dependence snapshot, and one simultaneous state.
  Never price independent marginals first and add correlation afterward.

### Daily observations and OHLC semantics

- One diffusion transition produces an endpoint, not a valid intraperiod OHLC
  path. `open`, `high`, `low`, and `close` are joint path observations and require
  timestamp and session semantics.
- `open = previous close` is valid only under an explicit no-gap/session-boundary
  convention. Otherwise overnight and intraday moves must be represented
  separately.
- For a continuous-path model, valid high/low generation must come from the same
  intraperiod path or a mathematically consistent conditional construction such as
  an appropriate bridge/range law. The inequalities
  `low <= min(open, close) <= max(open, close) <= high` and `low > 0` are necessary
  but do not make an arbitrary range heuristic distributionally correct.
- Volume, open interest, bid/ask spread, dividends, and corporate actions are not
  outputs of a spot GBM. Give each a separate model or an explicit null/constant
  rule. Define whether and how dividends and splits connect spot, adjusted close,
  and total returns.

### Risk-neutral pricing dynamics

- Under a pricing measure `Q` associated with the money-market numeraire, the
  ex-dividend spot with continuous carry in the simple BSM setting follows

  `dS_t/S_t = (r_t - q_t) dt + sigma_Q(t, ...) dW_t^Q`.

  More generally, the numeraire-discounted gains process must be a local
  martingale. Never use physical drift to price options, and never use `r - q` as
  physical drift unless a physical model explicitly asserts that equality.
- All options on one underlying within a market snapshot must be generated by one
  coherent marginal pricing model and state, not independently fitted point-price
  formulas. A legal marginal model must produce a jointly consistent strike and
  maturity surface under its declared curves, dividends, exercise, and settlement
  conventions.
- Same-currency assets priced jointly must share the declared `Q`, numeraire,
  valuation timestamp, and rate path. Correlation changes joint payoffs but must
  not alter each asset's frozen marginal model or vanilla surface.
- Discount curves, dividend/borrow/carry curves, calendars, business-day
  adjustments, day count, compounding, settlement, and time-to-expiry are part of
  the price definition. A scalar `r` or `q` is shorthand only when a flat,
  continuously compounded curve is explicitly declared.

### Physical volatility, pricing volatility, and implied volatility

- Physical volatility is a `P`-measure return-dynamics parameter. Pricing
  volatility or variance state belongs to a `Q`-measure marginal pricing model.
  Implied volatility is neither of those states: it is the value `sigma_IV` that
  solves a declared inverse problem such as

  `BSMPrice(S, K, T, r, q, sigma_IV, contract_conventions) = visible_option_price`.
- Physical volatility and implied volatility are neither equal by definition nor
  separated by a universal constant. Any relationship requires an explicit
  volatility-risk-premium or joint `P/Q` model. A rule such as
  `base_implied_volatility = physical_volatility + 0.02` is a generator assumption,
  not a mathematical fact.
- IV is indexed by valuation time, strike, maturity, option convention, curves,
  and observed price. A single scalar "IV for the underlying" is only a declared
  flat-surface special case.
- The canonical IV truth for a solver task must be recovered from the actual
  solver-visible, canonicalized option price using the declared pricing formula,
  root bracket, algorithm, iteration/stopping rule, dtype, and failure behavior.
  Never publish a hidden latent pricing volatility as the answer when quote
  rounding, spreads, or noise make the visible inverse problem different.
- In the standard European BSM domain with positive spot, strike, maturity, and
  volatility, option value is increasing in volatility. IV is unique only when
  the selected quote lies in the declared model's attainable price interval. At a
  boundary, the valid result may be zero volatility, an infinite-limit case, or no
  finite IV according to the task contract.
- Bid and ask imply an IV interval when both are valid model prices. Mid implies
  one model-dependent IV. Noise applied to a spread must not be described as a
  change in latent volatility unless the quote model explicitly couples them.
- Smile and surface coordinates must be explicit: spot or forward moneyness,
  log-moneyness, strike, delta convention, maturity clock, interpolation,
  extrapolation, weights, and calibration objective all affect the mathematical
  object.

### Arbitrage and no-arbitrage definitions

#### Mandatory classification protocol

- Before any arbitrage conclusion, separate the following claims and report every
  applicable one; they are not synonyms and they may coexist:
  1. **Model-relative mispricing or inconsistency:** an observed quote differs from
     the value, calibration, or surface restriction of one selected pricing model
     or one selected pricing measure.
  2. **Executable static or semi-static arbitrage:** a declared portfolio of traded
     instruments has executable entry cashflows and statewise future cashflows that
     satisfy the task's arbitrage definition after bid/ask, option fees, underlying
     transaction costs, funding, carry, settlement, and position constraints.
  3. **Catalogue-scoped F2A arbitrage:** at least one canonical candidate in the
     frozen F2A strategy catalogue has a positive exact net certificate when
     recomputed from the public child and public variant.
  4. **Full-market dynamic or replication arbitrage:** an admissible self-financing
     strategy exists in the complete declared strategy class, possibly using
     dynamic trading.
- A disagreement with BSM, stochastic-volatility, stochastic-rate, or any other
  chosen model establishes item 1 only. It establishes item 4 only when the stated
  market assumptions actually give the relevant unique replication price and the
  replicating strategy is executable under the declared frictions. In particular,
  a frictionless complete-BSM conclusion must not be carried unchanged into an
  incomplete or transaction-cost market.
- Conversely, stochastic volatility, stochastic rates, model incompleteness, or
  non-uniqueness of an equivalent martingale measure do not invalidate an explicit
  item-2 certificate. A different model or pricing measure cannot rescue a jointly
  quoted panel whose executable portfolio already has non-positive initial cost
  and non-negative statewise payoff with the required strict gain.
- “Noisy market” describes how a quote was observed or generated; it is not an
  exemption from no-arbitrage. If the noisy mutation becomes an executable bid or
  ask, test the actual panel with all declared costs. If only a non-executable mid,
  latent value, or noisy signal is supplied, report model/surface inconsistency;
  do not promote it to executable arbitrage without executable sides.
- Bid/ask spreads, option fees, and underlying transaction costs create economic
  no-arbitrage bands. They are not floating-point tolerances. Charge each cost only
  on the legs to which the public execution contract applies, and evaluate the
  resulting exact net cashflow or profit with the task's declared arithmetic.
- In F2A, a one-quote mutation may activate any subset of the enabled
  `(cross-sectional, cross-asset, calendar)` families, in that canonical order.
  Always rescan every enabled family and derive the family-hit vector from
  public-child certificates; never
  copy the mutation author's intended family as truth. Changing fees or transaction
  costs may suppress some certificates and leave others, but only the recomputed
  net certificates determine whether exactly one, two, or all three families hit.
- Here a positive exact net certificate means the candidate-specific predicate
  `(s_j > 0 and g_j >= 0 P-a.s.) or (s_j == 0 and g_j >= 0 P-a.s. and
  P(g_j > 0) > 0)`; it is not a uniform `candidate_spread > 0` rule.
- An F2A negative result means only “no candidate satisfies that predicate in the declared
  finite catalogue under this public variant.” It does not prove global market
  no-arbitrage. If only model mismatch was tested, say “model inconsistency” or
  “executable arbitrage not established,” not “no arbitrage.”
- Before writing “arbitrage” or “no arbitrage,” identify the traded universe,
  executable prices, cost rules, strategy class, cashflow dates, and the explicit
  portfolio certificate or violated invariant. For calendar claims, the existing
  prohibition on raw same-strike maturity price ordering still applies.

- The current F2A arbitrage-finding phase is not a zero-transaction-cost market.
  Option trades execute directionally at the declared bid or ask and incur the
  versioned nonzero per-contract, per-side fee in the public execution contract.
  Apply the option multiplier and fee to every leg using `abs(position)`. The
  underlying and cash account may remain frictionless only when that simplifying
  assumption is declared explicitly and is required by the frozen BSM replication
  contract; it must not be described as zero transaction costs for the task as a
  whole.
- Fix a finite horizon, a filtered probability space
  `(Omega, F, (F_t), P)`, a strictly positive numeraire, the traded assets and
  their cash distributions, and a class of admissible predictable self-financing
  strategies. Wealth and cashflows at different dates must be accumulated or
  discounted with the declared funding instruments before they are compared.
- An admissible self-financing strategy is an arbitrage when it can be entered
  from zero initial endowment, its terminal liquidation wealth is non-negative
  `P`-almost surely, and its terminal liquidation wealth is strictly positive
  with positive `P` probability. Equivalently, a task may use non-positive initial
  cost and non-negative terminal wealth, provided it requires either a strictly
  negative initial cost or a strictly positive terminal payoff with positive
  probability and accounts for the initial surplus using the declared numeraire.
- Admissibility must exclude doubling strategies. In a dynamic model this normally
  requires discounted wealth to be bounded below by a declared constant or an
  equivalent model-appropriate condition. A strategy is not an arbitrage merely
  because an unconstrained numerical optimization can create unbounded positions.
- A market satisfies no-arbitrage `NA` when no strategy in the declared admissible
  self-financing class is an arbitrage. The strategy class is part of the claim:
  changing short-sale constraints, dynamic trading dates, exercise rights,
  funding access, or available instruments changes the market and can change the
  answer.
- Static arbitrage restricts trading in the non-cash claims to the initial time;
  declared intermediate cashflows may only be carried according to the frozen
  funding contract. Semi-static arbitrage holds options statically while allowing
  declared dynamic trading in the underlying and cash account. Dynamic arbitrage
  allows the full declared predictable self-financing strategy class. Never use a
  static price inequality as proof for a dynamic claim, or vice versa, without a
  theorem connecting them.
- Arbitrage is defined under `P` up to its null sets; a pricing measure `Q` is
  relevant only when it is equivalent to `P`. In a finite discrete frictionless
  market, the applicable fundamental theorem relates `NA` to an equivalent
  martingale measure under its required technical assumptions. In a general
  semimartingale market, the standard continuous-time condition is no free lunch
  with vanishing risk `NFLVR`, which under the theorem's assumptions is equivalent
  to an equivalent local-martingale measure for correctly discounted gains.
  Do not claim this equivalence without the required market and admissibility
  hypotheses.
- Existence of a pricing measure does not make it unique and does not select BSM,
  Heston, or another convenient model. Conversely, disagreement between two model
  prices, a calibration residual, a non-PSD parameter matrix, or a quote that
  differs from one model's theoretical value is not by itself an arbitrage. It is
  an arbitrage only if it rules out every admissible pricing system for the
  declared traded market or yields an admissible self-financing strategy with the
  required payoff property.
- For a finite family of traded claims, no-arbitrage can be expressed as existence
  of a positive linear pricing rule consistent with their cashflows and the
  declared numeraire. When a task uses a finite template catalogue or a linear
  program instead of the full admissible strategy set, its truth label is scoped
  to that declared catalogue or optimization problem; it must not be described as
  global market no-arbitrage.
- Calendar arbitrage concerns jointly traded claims with different cashflow dates.
  Raw option prices at two maturities cannot generally be ordered merely because
  their strikes are equal. Rates, dividends or carry, forward levels, discounting,
  exercise, settlement, admissible dynamic trading, and the chosen numeraire all
  affect the valid cross-maturity restriction. A calendar test must derive its
  comparison or optimization problem from the declared market and must produce,
  or invoke a stated theorem guaranteeing, an admissible self-financing arbitrage.
- Monotonic total implied variance, non-crossing normalized call-price slices, or
  another surface criterion may be used only with its exact forward/moneyness
  coordinates, interpolation and extrapolation policy, curve assumptions, and
  theorem. None is a universal substitute for the arbitrage definition. In
  particular, never label `longer maturity price < shorter maturity price` as an
  arbitrage without proving that implication under the task's actual contracts.
- Current F2A work must preserve the existing nonzero rate and dividend/carry
  inputs and the declared P/Q dynamics, but its `0.01 USD` minimum price increments
  require a new tick-aligned parent generator config and a new snapshot identity.
  Do not overwrite or relabel an existing snapshot, and do not generate a zero-rate
  or zero-dividend replacement merely to make a convenient calendar inequality
  true. Once the new parent is materialized and frozen it remains immutable; each
  mutated child receives a new snapshot identity and records its complete private
  mutation lineage.
- An `arbitrage_opportunity` task must freeze the traded universe, observation and
  trading times, information available at each time, strategy class, position and
  shorting constraints, funding and cash-distribution rules, exercise and
  settlement conventions, state space, and exact decision procedure. A positive
  or negative label must be recomputed from the solver-visible mutated child, not
  copied from a hidden mutation intention.
- `maximal_spread` or maximal arbitrage profit is undefined without a normalization
  and an optimization domain: an arbitrage can otherwise be scaled without bound.
  Any task requesting it must freeze notional or capital normalization, position
  constraints, objective, units, candidate strategy space, dtype, reduction order,
  and canonicalization. A non-spread task such as F2A must not require this value.

### No-arbitrage and validity

- Require the domain conditions needed by the selected model, including positive
  spot and strike, positive maturity for live options, and admissible variance,
  rate, correlation, and model parameters.
- In flat continuous-rate European BSM, use discounted quantities consistently:

  `C - P = S exp(-qT) - K exp(-rT)`,

  `max(0, S exp(-qT) - K exp(-rT)) <= C <= S exp(-qT)`, and

  `max(0, K exp(-rT) - S exp(-qT)) <= P <= K exp(-rT)`.

  Different curve, dividend, settlement, or exercise conventions require their
  corresponding bounds and parity relation.
- Strike monotonicity, strike convexity, put-call parity, and appropriate calendar
  consistency should be inherited from one coherent marginal model. Use these as
  implementation sanity checks; do not repair an incoherent quote grid by clipping
  individual prices and then claim it came from a coherent model.
- A deliberately invalid arbitrage sample must record the exact violated
  invariant and belong to a task that asks for detection. It must not leak into an
  ordinary pricing, IV, Greek, or risk task as accidental data corruption.
- A positive-semidefinite correlation matrix is necessary for a joint Gaussian
  driver law but does not repair an invalid marginal pricing model, create a common
  `Q`, or guarantee coherent pricing of multi-asset claims.

### Reproducibility, snapshots, and numerical identity

- A generated market snapshot is a function of the complete model configuration,
  generator/backend version, seed, RNG, random-stream partition, and numerical
  conventions. Freeze snapshot id and revision after materialization. Changing any
  economic parameter, time function, state law, driver order, or random law creates
  a new snapshot identity; do not append transitions from a different law.
- Persist enough provenance to replay the market: process and engine classes,
  `P/Q` dynamics, numeraire and curve identities, state variables, dependence
  specification, calendar/day count, time grid, seed/RNG, dtype, operation order,
  working precision, quantization checkpoints, and canonicalization.
- Determinism does not make an invalid model correct. Fixed seeds and byte-identical
  outputs verify replayability only; separately verify mathematical validity and
  model semantics.
- Solver and verifier must use the same declared mathematical object and numerical
  method. Analytic, finite-difference, PDE, tree, Monte Carlo, calibration, and root
  methods are not interchangeable merely because their outputs are close.
- Numerical equality rules belong to the task contract. Keep model-state precision,
  published quote precision, inverse-problem inputs, and final canonical output as
  distinct layers. Record every rounding/cast checkpoint that can change results.
- At minimum, authoring checks must cover model domain, quote bounds, IV root
  existence/uniqueness under the declared bracket, correlation construction,
  common-measure identities, joint-process provenance, snapshot immutability, and
  fixed-seed replay.

## Keep implementations simple

- Prefer the simplest implementation that satisfies the current requirement.
- Do not add speculative abstractions, fallback paths, or defensive checks without a concrete need.
- Trust established project invariants and upstream guarantees unless there is evidence that they are unreliable.

## Validate only what matters

- Validate the identifiers required by the workflow, such as file IDs and version IDs.
- Do not calculate or verify SHA-256 values, hashes, checksums, or file contents unless integrity or security verification is an explicit requirement.
- Avoid validating the same condition repeatedly across layers.

## Avoid improbable edge-case branches

- Do not add `if`/`else` branches for scenarios that are extremely unlikely and unsupported by an actual requirement, known failure mode, or test case.
- Let impossible states remain governed by existing invariants instead of adding code for every hypothetical condition.
- Add edge-case handling only when the case is realistic, has meaningful impact, and can be handled correctly.

## Scope discipline

- Make focused changes that directly serve the requested behavior.
- Do not introduce unrelated refactors or extra compatibility logic.
- When stronger validation or exceptional handling is genuinely necessary, keep it proportional and briefly document the concrete reason.
