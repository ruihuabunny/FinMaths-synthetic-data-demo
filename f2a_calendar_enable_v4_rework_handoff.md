# F2A Calendar Arbitrage Enablement v4 — Mandatory Rework Handoff

## 0. Executive instruction

Repository:

```text
ruihuabunny/FinMaths-synthetic-data-demo
```

Baseline branch reviewed:

```text
f2a-2nd-revised
```

This rework has one non-negotiable outcome:

> Implement and enable an executable, transaction-cost-aware calendar-arbitrage family. Do not return another blocked review contract.

The current v1/v2/v3 identities are historical or blocked records. Preserve them byte-for-byte unless a test fixture or historical note must be added. Create a fresh executable successor identity. The completed successor must have:

```text
runtime_enabled = true
calendar_family != null
status = EXECUTABLE
publication_task_count > 0 after the real reachability audit
```

It is not acceptable to finish with any of the following:

```text
runtime_enabled = false
calendar_family = null
BLOCKED_CALENDAR_FAMILY_NULL
calendar_terminal_guard = null
"calendar is specified but not implemented"
tests whose purpose is to preserve the blocked calendar state
```

If an implementation attempt exposes a defect, fix the executable catalogue, proof, schema, or runtime. Do not convert the defect into another permanent blocker.

---

## 1. What is wrong with `f2a-2nd-revised`

The branch improved the mathematics but did not complete the task:

1. `bsm_arbitrage_finding_f2a_v3.json` still has `runtime_enabled=false` and `calendar_family=null`.
2. `f2a_contract.py` explicitly excludes the calendar family.
3. The intended runtime modules do not exist:

   ```text
   src/synthetic_derivatives/verifier/f2a_oracle.py
   src/synthetic_derivatives/verifier/f2a.py
   src/synthetic_derivatives/mutation/f2a.py
   src/synthetic_derivatives/authoring/f2a_child_materializer.py
   src/synthetic_derivatives/solver/f2a.py
   scripts/materialize_f2a.py
   ```

4. Repository tests assert that calendar remains disabled. Those assertions protect incompleteness rather than correctness.
5. The v3 calendar target is computationally unusable. With four expiries, seven strikes, two option types, up to six nonzero option legs, and an independent seven-value `Delta_1` choice on every state cell, raw enumeration is at least on the order of `10^17` candidates for one complete chain.
6. The v3 lineage schema accepts calendar evidence whose boundary/ray slacks are all `null`; therefore it does not actually validate a calendar certificate.

The v3 cashflow primitives and the separation of `s_j` from `g_j` must be preserved. The enormous arbitrary-grid catalogue must not be preserved.

---

## 2. Required identity migration

Do not silently enable a blocked v2/v3 identity. Allocate a fresh executable line, for example:

```text
variant_id             = bsm-arbitrage-finding-f2a-v4
candidate_catalogue_id = bsm-f2a-candidate-catalogue-v5
mutation_engine_id     = f2a-complete-mutation-v4
dataset_config_id      = f2a-dataset-v4
lineage_schema         = schemas/f2a-lineage-v4.schema.json
submission_schema      = schemas/submission-v4.schema.json
execution_contract_id  = us-options-underlying-5bps-options-flat-050-v4
output_contract_id     = arbitrage-opportunity-type-trajectory-v5
```

Exact names may differ only if the repository already has a later collision. The new identities must be internally consistent across variant, mutation, authoring, lineage, submission, task manifest, oracle, Solver, and tests.

V4 is executable, not another “review target”:

```json
{
  "status": "EXECUTABLE",
  "runtime_enabled": true,
  "blocking_reasons": [],
  "candidate_catalogue": {
    "calendar_family": "transaction-cost-aware-two-expiry-call-stock-flip-v1"
  }
}
```

Do not claim that an executable identity exists until its implementation and required tests pass. However, the final submitted branch must contain that implementation and enabled identity.

---

## 3. Preserve these mathematical contracts

The following content from v3 is correct and must not regress:

- canonical family order:

  ```text
  (cross-sectional, cross-asset, calendar)
  ```

- candidate-specific arbitrage predicate:

  \[
  \bigl(s_j>0\ \land\ g_j\ge 0\ P\text{-a.s.}\bigr)
  \quad\lor\quad
  \bigl(s_j=0\ \land\ g_j\ge0\ P\text{-a.s.}
        \ \land\ P(g_j>0)>0\bigr);
  \]

- option execution at directional bid/ask;
- `0.50 USD` option fee per contract per side for the frozen profile;
- option contract multiplier on every option price and payoff;
- `5 bps` underlying transaction cost on every actual underlying trade;
- signed cash account accumulated with the declared money-market numeraire;
- actual discount and dividend curves;
- terminal-spot execution primitives `A_S`, `B_S`, and `Phi`;
- `g_j` is the terminal certificate payoff and excludes the separately deposited initial surplus;
- `W_T2 >= 0` is not a substitute for checking `g_j >= 0`;
- raw same-strike maturity price ordering is not a calendar certificate;
- BSM model-price disagreement is not by itself executable arbitrage;
- the oracle recomputes truth from the public child and public variant and never trusts mutation intention or stored labels;
- exact canonical arithmetic has no verifier tolerance; authoring guards are sample-selection rules only.

---

## 4. Replace the infeasible calendar grid with a minimal executable catalogue

### 4.1 Family ID and scope

Enable the following first executable calendar family:

```text
transaction-cost-aware-two-expiry-call-stock-flip-v1
```

This is deliberately a small catalogue-scoped family. It is not a claim to characterize every possible multi-maturity arbitrage.

For each valuation slice and every tuple

```text
(underlying_id, T1, T2, K, contract_multiplier M)
```

enumerate exactly one candidate when all of the following hold:

```text
t < T1 < T2
the T1 and T2 options are European cash-settled calls
the two calls have the same strike K
the two calls have the same positive multiplier M
the two calls have the same currency, underlying, exercise, and settlement conventions
the required discount and dividend curves cover [t,T2]
```

With four expiries and seven common strikes this produces only

\[
\binom{4}{2}\times 7=42
\]

calendar candidates per underlying/date slice. This must replace the combinatorial v3 option-position and cell-rule grid.

### 4.2 Existing terminal-spot primitives

For proportional underlying cost `kappa` and integrated cash-dividend yield

\[
Q_q(u,v)=\int_u^v q(s)\,ds,
\]

preserve the v3 executable terminal-spot primitives:

\[
A_S(u,v;S_u)
=S_u\frac{1+\kappa}{1-\kappa}
  \exp\!\left(-\frac{Q_q(u,v)}{1+\kappa}\right),
\]

\[
B_S(u,v;S_u)
=S_u\frac{1-\kappa}{1+\kappa}
  \exp\!\left(-\frac{Q_q(u,v)}{1-\kappa}\right),
\]

and

\[
\Phi_{u,v}(\Delta;S_u)
=\Delta^+ A_S(u,v;S_u)-\Delta^-B_S(u,v;S_u).
\]

`Phi` is already the complete segment cash outflow. Do not charge another outer underlying fee.

### 4.3 Canonical stock-flip candidate

For a candidate `(T1,T2,K,M)`, define

\[
\beta_{12}
=\frac{B_S(T_1,T_2;x)}{x}
=\frac{1-\kappa}{1+\kappa}
 \exp\!\left(-\frac{Q_q(T_1,T_2)}{1-\kappa}\right).
\]

Because the v4 profile has nonnegative cash-dividend yield and `0 <= kappa < 1`, it must satisfy

\[
0<\beta_{12}\le1.
\]

Freeze a public economic over-hedge buffer

```text
calendar_bridge_exposure_buffer_ratio = 1e-8
```

and denote it by `eta`. This is an actual additional underlying exposure that is charged through `Phi`; it is not a verifier tolerance.

At valuation time `t`, the candidate performs:

```text
short 1 T1 call at executable bid
long  1 T2 call at executable ask
establish first-segment terminal spot exposure
Delta_0 = M * (1 - beta_12 + eta)
```

The underlying contract must explicitly allow real-valued share quantities. Rounding `Delta_0` to an integer would destroy the exact hedge and is forbidden.

At `T1`, after observing `x=S_T1`, use the predictable right-continuous stock-flip rule

\[
\Delta_1(x)=
\begin{cases}
0, & 0<x<K,\\
-M, & x\ge K.
\end{cases}
\]

The boundary `x=K` belongs to the high-state branch.

### 4.4 Initial executable surplus

Let

\[
C_1^b=M\,bid(T_1,K)-f,
\qquad
C_2^a=M\,ask(T_2,K)+f,
\]

where `f` is the per-contract per-side option fee. The option cash outflow is

\[
O_t=C_2^a-C_1^b.
\]

The first-segment underlying cash outflow is

\[
\Phi_{t,T_1}(\Delta_0;S_t).
\]

Therefore the candidate's initial surplus is exactly

\[
\boxed{
s_j=C_1^b-C_2^a-\Phi_{t,T_1}(\Delta_0;S_t).
}
\]

This formula charges both option fees, the option multiplier, the directional sides, and every underlying cost contained in the first segment.

### 4.5 `T1` cash ledger

The short `T1` call payoff is

\[
V_1(x)=-M(x-K)^+.
\]

After the first segment settles and the second segment is established, signed `T1` cash is

\[
C_{T_1}(x)
=V_1(x)+\Delta_0x
-\Phi_{T_1,T_2}(\Delta_1(x);x).
\]

Using the declared `Delta_0`, `Delta_1`, and `beta_12`, this simplifies to

\[
C_{T_1}(x)=
\begin{cases}
M(1-\beta_{12}+\eta)x, & 0<x<K,\\
MK+M\eta x, & x\ge K.
\end{cases}
\]

The implementation must replay the unsimplified cash ledger and independently check that it agrees with the simplified certificate form under the frozen canonical arithmetic.

### 4.6 Terminal certificate payoff

Let

\[
F_{12}=\frac{D(t,T_1)}{D(t,T_2)}.
\]

The v4 family is enabled only for expiry pairs satisfying

\[
F_{12}\ge1.
\]

This is a candidate-domain condition derived from the frozen discount curve, not a relabeling knob. The current positive-rate parent is expected to satisfy it. If a future parent has a negative-rate interval, this specific family skips that expiry pair; a different theorem requires a new family version.

The long `T2` call payoff is

\[
V_2(y)=M(y-K)^+.
\]

The terminal certificate payoff excluding deposited initial surplus is

\[
g_j(x,y)
=F_{12}C_{T_1}(x)+V_2(y)+\Delta_1(x)y.
\]

Its canonical piecewise form is

\[
g_j(x,y)=
\begin{cases}
F_{12}M(1-\beta_{12}+\eta)x+M(y-K)^+,
  & 0<x<K,\\[4pt]
F_{12}(MK+M\eta x)-My,
  & x\ge K,\ 0<y<K,\\[4pt]
MK(F_{12}-1)+F_{12}M\eta x,
  & x\ge K,\ y\ge K.
\end{cases}
\]

Under

```text
M > 0
K > 0
0 < beta_12 <= 1
eta > 0
F_12 >= 1
x > 0
y > 0
```

every branch is nonnegative. In particular:

- the low-`x` branch is a sum of nonnegative terms;
- in the high-`x`, low-`y` branch, the infimum occurs as `x` approaches `K` and `y` approaches `K`, giving at least

  \[
  MK\bigl(F_{12}-1+F_{12}\eta\bigr)>0;
  \]

- the high-`x`, high-`y` branch is at least the same strictly positive quantity.

The left limit as `x` approaches `K` and the actual right-cell value at `x=K` must both be checked. `y=K` is continuous. The public support contract implies every nonempty open rectangle in `(0,infinity)^2` has positive probability; hence `P(g_j>0)>0`.

### 4.7 Calendar decision boundary

For every eligible calendar candidate above, `g_j` is nonnegative and nonconstant with a strict-gain open set. Therefore its canonical setup boundary is closed:

\[
\boxed{
candidate\_is\_calendar\_arbitrage
\iff s_j\ge0.
}
\]

Do not replace this with a universal family threshold copied from another payoff class. The equality case is accepted because the terminal payoff is strictly positive with positive probability.

### 4.8 Admissibility convention for v4

V3 blocked itself on a continuous-time uniform-interim-wealth proof even though the catalogue permits only finitely many trades. V4 must freeze the following finite-date semi-static convention instead:

```text
trading dates: t, T1, T2 only
option positions: exactly -1 early call and +1 later call
state-dependent rebalance count: exactly one, at T1
underlying exposures: real-valued and bounded by the public template
cash account: signed, unlimited borrowing/lending at the same declared curve
margin requirement: none
terminal requirement: pathwise nonnegative g_j with the strict-gain rule
```

The continuous dividend reinvestment/financing inside `A_S/B_S` is the deterministic finite-variation implementation of each terminal-exposure segment; it does not introduce another adaptive rebalance.

Because positions are bounded, the number of trading dates is finite, and only one predictable state-contingent rule is allowed, a doubling strategy is structurally impossible. V4 does not impose an additional uniform lower bound on intermediate cash across all possible spot states. This is a deliberate public market-contract assumption, supported by the signed cash account and absence of margin.

The resulting label must be described as:

> catalogue-scoped executable two-expiry calendar arbitrage under the v4 finite-date semi-static contract.

It must not be described as full-market continuous-time `NFLVR` or global calendar no-arbitrage.

If a later benchmark wants margin, bounded borrowing, or a stronger continuous-time admissibility class, implement that under a separate candidate/variant identity. Do not use that future extension to block v4.

---

## 5. Required executable oracle

### 5.1 Oracle output

The trusted oracle must scan all enabled families and return:

```text
X = any enabled cross-sectional candidate is arbitrage
U = any enabled cross-asset candidate is arbitrage
T = any enabled calendar stock-flip candidate has s_j >= 0

realized_signature = XYZ in canonical X,U,T order
arbitrage_opportunity = X or U or T
arbitrage_type = ordered list from [cross-sectional, cross-asset, calendar]
```

Calendar is never inferred from the mutation operator. It is recomputed from the public child.

### 5.2 Deterministic enumeration

Freeze the calendar order as:

```text
valuation_date
underlying_id
T1 ascending
T2 ascending
strike ascending
call option_id at T1
call option_id at T2
```

Candidate ID must be deterministic, for example:

```text
calendar-call-stock-flip-v1|<underlying>|<date>|<T1>|<T2>|<K>|<M>
```

Do not use random candidate generation, nonlinear optimization, Monte Carlo, or a spot grid.

### 5.3 Canonical arithmetic

Freeze and test one operation order. At minimum:

1. cast public DECIMAL market inputs once;
2. compute `Q_q(T1,T2)`;
3. compute `beta_12`;
4. compute `Delta_0 = M * (1 - beta_12 + eta)`;
5. compute executable option amounts including fees;
6. compute first-segment `Phi`;
7. compute `s_j`;
8. compute `F_12`;
9. validate the structural terminal certificate;
10. apply the candidate-specific predicate.

Canonical comparisons use exact signs/equality in the declared arithmetic. No verifier tolerance is allowed. The public `eta` exposure buffer is a paid hedge position, not a numerical tolerance.

### 5.4 Independent implementations

Implement two independent paths:

- authoring-side candidate scan used only for deterministic tick selection and guard evidence;
- trusted verifier oracle used for the canonical ORM truth.

They may share immutable public data structures and primitive definitions, but must not share the final family-bit decision routine or stored expected labels.

The Solver must implement the public formula without importing either authoring or trusted-verifier code.

---

## 6. Required code changes

At minimum, the rework must add or complete:

```text
src/synthetic_derivatives/verifier/f2a_oracle.py
  - complete X/U/T scanner
  - calendar candidate enumeration
  - beta/Delta_0/s/g calculation
  - canonical family bitmask and ordered type list

src/synthetic_derivatives/verifier/f2a.py
  - submission validation
  - public-child loading
  - independent oracle invocation
  - exact ORM projection comparison

src/synthetic_derivatives/authoring/f2a_child_materializer.py
  - read-only parent selection
  - public child projection
  - atomic single/grouped/spot mutation materialization
  - authoring-side X/U/T rescan
  - active and inactive guards
  - freeze and private lineage

src/synthetic_derivatives/mutation/f2a.py
  - immutable mutation specs
  - deterministic selectors and identities
  - no DuckDB writes and no oracle truth

src/synthetic_derivatives/solver/f2a.py
  - Solver-visible implementation of all public formulas
  - no QuantLib, prebuilt option scanner, authoring import, or verifier import

scripts/materialize_f2a.py
  - non-interactive end-to-end materialization and verification
```

The existing `f2a_contract.py` may retain low-level primitives, but its module description must no longer say it represents only blocked v2, and its complete scanner must not exclude calendar in the executable v4 path.

The single-expiry scanner must also validate or bucket by common contract multiplier before cross-strike monotonicity/convexity candidates are formed.

---

## 7. V4 lineage and guard schema

Do not reuse the v3 family-level `candidate_ids + one shared guard` shape. It cannot represent candidate-specific boundaries.

V4 must store candidate evidence as an array. A calendar entry must contain at least:

```json
{
  "candidate_id": "...",
  "family": "calendar",
  "template_id": "transaction-cost-aware-two-expiry-call-stock-flip-v1",
  "underlying_id": "...",
  "valuation_date": "...",
  "T1": "...",
  "T2": "...",
  "strike": 100.0,
  "contract_multiplier": 100.0,
  "early_option_id": "...",
  "late_option_id": "...",
  "early_position": -1,
  "late_position": 1,
  "beta_12": 0.0,
  "funding_factor_12": 0.0,
  "exposure_buffer_ratio": 1e-8,
  "delta_0": 0.0,
  "delta_1_low": 0.0,
  "delta_1_high": -100.0,
  "initial_surplus_usd": 0.0,
  "setup_boundary_kind": "closed",
  "terminal_certificate": {
    "beta_positive": true,
    "beta_at_most_one": true,
    "funding_factor_at_least_one": true,
    "left_boundary_nonnegative": true,
    "actual_boundary_nonnegative": true,
    "low_x_cell_nonnegative": true,
    "high_x_low_y_cell_nonnegative": true,
    "high_x_high_y_cell_nonnegative": true,
    "strict_gain_open_set": true
  },
  "is_arbitrage": true
}
```

Exact numeric values must be populated; the zeroes above are only schema-shape placeholders.

For active families, record the canonical first active candidate and the number of active candidates. For inactive families, record the nearest candidate to its own candidate-specific boundary. Do not merge candidates with different payoff classes into one shared `setup_boundary` object.

Schema tests must reject:

- active calendar evidence with `null` terminal fields;
- calendar evidence with an open setup boundary;
- `delta_1_high != -M`;
- `delta_1_low != 0`;
- mismatched strikes, multipliers, or underlying IDs;
- `T1 >= T2`;
- missing fee-adjusted option amounts;
- requested signature unequal to realized signature in an accepted record;
- a calendar bit copied from mutation intention without a verified candidate.

---

## 8. Mutation grammar and real signature reachability

Preserve the three v3 logical operators, under the fresh executable identity:

```text
mutate_option_price_point
mutate_call_put_pair_equal_shift
mutate_underlying_spot_point
```

The grouped call+put operator remains one logical group and two physical quote points. It preserves the same-pair put-call parity surplus but not all cross-asset bounds.

The calendar call bridge reacts to mutations through its exact initial surplus:

\[
s_j=C_1^b-C_2^a-\Phi_{t,T_1}(\Delta_0;S_t).
\]

Consequently:

- raising the earlier call quote increases calendar surplus;
- lowering the later call quote increases calendar surplus;
- a same-strike early-expiry equal call+put shift changes the calendar call surplus while preserving same-pair parity;
- a spot mutation changes the first-segment hedge cost and therefore the calendar threshold;
- every operator must still trigger a complete X/U/T rescan.

Run a real deterministic integer-tick reachability audit against the frozen parent or an explicitly versioned replacement parent. For every signature in

```text
000 100 010 001 110 101 011 111
```

record:

```text
reachable true/false
operator
target IDs
sign
exact integer tick interval(s)
active candidate IDs and guard distances
nearest inactive candidate IDs and guard distances
domain-gate interval
deterministic diagnostic if unreachable
```

At minimum, v4 acceptance must demonstrate with real executable scans:

```text
000 clean control
001 calendar only
at least one mixed signature containing calendar
```

Do not synthesize reachability with an abstract `_AffineTrigger` test. Tests must mutate actual option quotes, run the full oracle, and observe the realized signature.

If some of the remaining signatures are genuinely unreachable under the fixed parent/profile/grid, publish the proved reachable subset and record the others as unreachable. Calendar itself may not remain disabled.

---

## 9. Mandatory tests

### 9.1 Calendar algebra

Add exact unit tests for:

1. `beta_12 = B_S(T1,T2;x)/x` is independent of `x`.
2. `Delta_0 = M*(1-beta_12+eta)` is charged through the first-segment executable ask.
3. `Delta_1(x)` uses `0` below `K` and `-M` at and above `K`.
4. The unsimplified `T1` ledger matches the declared piecewise `C_T1(x)`.
5. The unsimplified terminal ledger matches the declared piecewise `g_j(x,y)`.
6. Both `x -> K-` and the actual `x=K` right-cell value are nonnegative.
7. `y=K` is evaluated consistently.
8. All unbounded-cell ray slopes are nonnegative.
9. A nonempty open strict-gain region exists.
10. `s_j=0` is accepted for this nonconstant nonnegative payoff class.
11. `s_j<0` is rejected even if total wealth would be rescued by adding a hypothetical endowment.
12. Every option fee and every underlying transaction cost is charged exactly once.

### 9.2 Classification regression

Create small executable fixtures for:

```text
000: no family active
001: only the calendar bridge active
101 or 011: calendar plus exactly one other family
111: all families active, if the reachability audit proves it
```

For the `001` fixture, use an early-expiry equal call+put quote shift so that:

- the calendar call bridge has `s_j >= 0`;
- same-pair parity remains unchanged;
- every cross-asset bound remains inactive;
- all cross-sectional candidates remain inactive;
- the full oracle returns exactly `001`.

The fixture must be found and verified by the real tick selector, not by hand-copying the intended label.

### 9.3 Negative controls

Tests must reject:

- `longer-maturity raw price < shorter-maturity raw price` without a positive executable bridge surplus;
- BSM theoretical-price mismatch without an executable certificate;
- finite spot-grid sampling in place of the pathwise proof;
- using `W_T2 >= 0` when `g_j < 0`;
- missing option fees;
- frictionless `S*exp(-qT)` in place of `A_S/B_S`;
- wrong T1 boundary branch;
- double-charged underlying cost;
- different multipliers across the two calls;
- a candidate with `F_12 < 1` under this family version;
- NaN or infinite quote, curve, fee, spot, multiplier, surplus, or certificate values.

### 9.4 End-to-end

Add an executable smoke path:

```text
frozen parent or tracked F2A fixture
  -> atomic child mutation
  -> frozen public child
  -> Solver submission
  -> independent trusted verifier
  -> exact ORM result containing calendar
```

The smoke test must fail if `calendar_family` is `null`, `runtime_enabled` is false, the calendar candidate count is zero, or the output schema rejects `calendar`.

### 9.5 Dependency and reproducibility repair

Update the locked test environment. The current branch imports `jsonschema` and `referencing` but does not install compatible locked versions. Ensure a clean clone can run:

```bash
make install
make test
```

The GitHub branch currently omits the claimed F2A `parent.duckdb` and `parent.manifest.json`. Provide one reproducible path:

- a small tracked F2A fixture for CI plus a committed parent manifest/checksum and deterministic materialization command; or
- a versioned release/LFS/download mechanism with checksum verification.

A fresh clone must not depend on an unexplained local-only frozen parent.

---

## 10. Documentation and configuration updates

Update all active documentation to distinguish:

```text
v1: legacy replay
v2/v3: historical blocked review records
v4: executable calendar-enabled successor
```

Required files include at least:

```text
AGENTS.md
README.md
docs/authoring_pipeline.md
src/synthetic_derivatives/authoring/README.md
src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md
src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md
tests/unit/README.md
```

The active docs must no longer say that the current F2A successor has `calendar_family=null` or that calendar implementation is future work. Historical v2/v3 audit files may retain those statements when clearly marked superseded.

Remove or replace tests whose success condition is merely:

```python
assert variant["runtime_enabled"] is False
assert variant["candidate_catalogue"]["calendar_family"] is None
```

V4 tests must assert the opposite and must also prove that non-null configuration corresponds to real executable code.

---

## 11. Forbidden shortcuts

The following are explicit rejection conditions:

1. Changing only JSON/Markdown from `null/false` to a family name/`true`.
2. Enabling calendar without an executable oracle and end-to-end test.
3. Restoring raw same-strike maturity ordering.
4. Calling model mismatch calendar arbitrage.
5. Using Monte Carlo paths or a finite `(x,y)` grid as the canonical certificate.
6. Using `W_T2` instead of the separated `s_j/g_j` predicate.
7. Omitting bid/ask, option fees, funding, dividends, or underlying costs.
8. Treating transaction costs as numerical tolerance.
9. Copying requested signature or mutation intention into oracle truth.
10. Keeping the v3 combinatorial `Delta_1` cell grid and declaring runtime complete without a feasible exhaustive algorithm.
11. Disabling calendar because not all seven positive signatures are reachable.
12. Requiring a stronger future margin/continuous-time admissibility extension before enabling the explicitly scoped v4 finite-date family.
13. Editing `main` or overwriting the frozen v1/v2/v3 identities.

---

## 12. Definition of done

Calendar rework is complete only when all of the following are true:

- [ ] A fresh executable v4 identity exists.
- [ ] `runtime_enabled=true`.
- [ ] `calendar_family="transaction-cost-aware-two-expiry-call-stock-flip-v1"`.
- [ ] The public candidate definition contains the exact `beta_12`, `eta`, `Delta_0`, `Delta_1`, `s_j`, and `g_j` contracts above.
- [ ] The complete chain enumerates the finite calendar candidate set deterministically.
- [ ] Trusted verifier independently returns the calendar bit from public-child cashflows.
- [ ] Solver can compute the public formula without forbidden packages or imports.
- [ ] V4 lineage stores candidate-specific, non-null calendar certificate evidence.
- [ ] A real full-oracle fixture realizes exact signature `001`.
- [ ] At least one mixed calendar signature is realized or is accompanied by a deterministic proof of why the attempted mixed signature is unreachable.
- [ ] Raw maturity ordering and model-mismatch negative controls are rejected.
- [ ] No test protects a blocked v4 calendar state.
- [ ] Clean `make install && make test` succeeds.
- [ ] A fresh clone has a reproducible F2A parent/fixture path.
- [ ] README and task plan describe v4 as executable and v2/v3 as superseded blocked records.
- [ ] No F2A child or dataset is published before the independent verifier passes, but the final branch contains at least one verified calendar-enabled smoke artifact or deterministic fixture.

The final handoff summary must report:

```text
new identity IDs
implemented files
calendar candidate count per complete chain
actual reachability results and tick windows
tests executed and exact results
remaining unreachable signatures, if any
confirmation that calendar is enabled rather than merely specified
```

---

## 13. Final implementation priority

Implement in this order:

1. Freeze the v4 finite-date admissibility and call stock-flip formula.
2. Implement the independent calendar candidate evaluator and exact tests.
3. Integrate it into the full X/U/T oracle.
4. Repair v4 lineage so calendar evidence is candidate-specific.
5. Implement real quote mutation and full-oracle reachability tests, beginning with `001`.
6. Complete child materialization, Solver, verifier, and smoke orchestration.
7. Enable the v4 configuration only after the code passes, then commit the enabled configuration in the same final branch.
8. Update active documentation and remove blocked-v4 assertions.

Do not spend another revision expanding the blocked v3 design. The next accepted successor must execute calendar arbitrage end to end.
