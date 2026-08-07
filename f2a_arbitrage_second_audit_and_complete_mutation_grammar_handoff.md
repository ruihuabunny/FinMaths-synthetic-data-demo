# F2A Arbitrage-Finding 第二轮审计、保护清单与完整 Mutation Grammar 返工 Handoff

> Repository: `ruihuabunny/FinMaths-synthetic-data-demo`  
> Audited branch: `f2a-arbitrage-revised`  
> Audit date: 2026-08-07  
> Intended recipient: Codex / repository maintainer  
> Status: **返修仍未完成；不得物化 F2A child、dataset 或宣称七种 positive signatures 已可达。**

## 0. Codex 执行摘要

本轮返修已经修对了大部分基础数学，尤其是 candidate-specific arbitrage predicate、含交易成本的
terminal-spot primitive、single-expiry candidate formulas、spot invariance、public support contract、
family order 和 immutable successor IDs。这些内容必须保护，不能在下一轮返修中退回旧语义。

但当前 branch 仍不能通过最终审计，主要原因是：

1. successor 文档仍有 v1/v2 path 串线；
2. `f2a-lineage-v2.schema.json` 不能正确表示 `000` 和 `111`，且缺少 operator/mutation/signature 条件约束；
3. calendar cell certificate 前面检查 `g_j`，结论却误写成 `W_T2 >= 0`；
4. 我们此前设计的完整 mutation grammar 尚未落入 config：当前只有 single quote 与 spot，缺少
   parity-preserving call+put grouped mutation；
5. parent README 仍残留 initial-surplus-only guard；
6. dataset v2 没有真正冻结 requested-signature distribution、clean-control allocation 和可用 split；
7. 新 math tests 仍只是 primitive/sample smoke tests，不能充当 pathwise/self-financing proof；
8. 根目录旧 handoff 仍把已修问题写成“当前问题”，会污染后续维护语义。

下一轮必须先完成本文第 3--8 节，再决定是否创建新的 executable successor identities。不得直接把
当前 blocked `variant v2 / catalogue v3` 原地改成 runtime-enabled。

---

## 1. 已完成且必须保护的内容

以下结论已经核对。Codex 不得为了“简化实现”改回 frictionless、统一 strict-positive、constant drift
或 raw maturity ordering。

### 1.1 Canonical family 与 signature order

固定类型顺序：

```text
X = cross-sectional
U = cross-asset
T = calendar
signature = (X, U, T)
canonical order = ["cross-sectional", "cross-asset", "calendar"]
```

八种 schema-level combinations：

```text
000 -> []
100 -> ["cross-sectional"]
010 -> ["cross-asset"]
001 -> ["calendar"]
110 -> ["cross-sectional", "cross-asset"]
101 -> ["cross-sectional", "calendar"]
011 -> ["cross-asset", "calendar"]
111 -> ["cross-sectional", "cross-asset", "calendar"]
```

这是 schema/target feasibility set，不是当前 runtime capability。某个 signature 是否能由冻结 parent、
execution profile、operator 和 integer tick grid 实现，必须由 deterministic reachability audit 证明。

### 1.2 Canonical arbitrage predicate

对 candidate `j`：

- `s_j`：在 valuation time 建仓后获得的 initial cash surplus；
- `g_j`：排除已存入 numeraire 的 initial surplus 后，扣除全部声明 cashflows/costs 的 terminal
  certificate payoff。

固定判定：

```text
candidate_is_arbitrage_j =
  (s_j > 0 and g_j >= 0 P-a.s.)
  or
  (s_j == 0 and g_j >= 0 P-a.s. and P(g_j > 0) > 0)
```

因此：

- executable put-call parity 的 `g_j` 恒为零，必须 `s_j > 0`；
- bounds、strike monotonicity、strike convexity 的 `g_j` 非负且在公开 support contract 下非恒零，
  允许 `s_j == 0`；
- calendar candidate 必须先通过完整 pathwise `g_j` certificate；不能只看 initial surplus 或总财富；
- canonical binary64 sign/equality 不使用 tolerance；authoring guard 只做 sample selection，不能改写 truth。

不得恢复：

```text
candidate_spread_strictly_greater_than_zero
candidate_spread > 0
```

作为 successor catalogue 的统一 predicate。上述字符串只允许存在于明确标注的 legacy replay 合同中。

### 1.3 Transaction costs 不是 tolerance，也不是 label knob

必须保护：

```text
option execution                 = directional bid/ask
option fee per contract per side = 0.50 USD
option holding                   = hold to cash settlement
underlying cost per side         = 0.0005 * abs(traded notional)
cash transaction cost            = 0
```

每个 candidate 按实际 option legs、方向、multiplier、underlying trades、funding dates 和 terminal
liquidation 逐项收费。不能在已经使用 bid/ask 与 leg fees 的 cashflow 上再减一次抽象 `TC`；也不能为了
目标 label 在 mutation 后调整 fee profile。

若未来增加 execution profile：

1. 必须在 mutation/requested label 之前选择；
2. 使用新的 public immutable execution/variant identity；
3. 同一 profile 必须跨多个 signatures 使用；
4. 必须证明 profile 本身不泄露 label。

### 1.4 含 underlying proportional cost 的 terminal-spot primitive

在 `q(u)` 为 deterministic、continuous、nonnegative proportional cash-distribution yield，且每次
underlying buy/sell 都收单边 `kappa=0.0005` 时，定义

\[
Q_q(u,v)=\int_u^v q(s)\,ds.
\]

一单位 terminal cash exposure `+S_v` 的 executable ask：

\[
A_S(u,v;S_u)
=S_u\frac{1+\kappa}{1-\kappa}
\exp\!\left(-\frac{Q_q(u,v)}{1+\kappa}\right).
\]

一单位 terminal liability `-S_v` 的 executable bid：

\[
B_S(u,v;S_u)
=S_u\frac{1-\kappa}{1+\kappa}
\exp\!\left(-\frac{Q_q(u,v)}{1-\kappa}\right).
\]

Signed terminal exposure `Delta*S_v` 的 segment initial cash outflow：

\[
\Phi_{u,v}(\Delta;S_u)
=\Delta^+ A_S(u,v;S_u)-\Delta^-B_S(u,v;S_u).
\]

这些 finite-variation share schedules 已把 initial execution、dividend reinvestment/financing 和 terminal
liquidation/re-entry costs 计入。外层不得再次收费，也不得退回 `S exp(-qT)` 或 frictionless BSM delta hedge。

### 1.5 Single-expiry exact candidate formulas

记：

```text
C^a = M * call.ask + f       C^b = M * call.bid - f
P^a = M * put.ask  + f       P^b = M * put.bid  - f
U^a = M * A_S(t,T)           U^b = M * B_S(t,T)
H   = M * K * D(t,T)
```

#### Discounted price bounds (`cross-asset`)

```text
call upper:  s = C^b - U^a
call lower:  s = U^b - H - C^a
put upper:   s = P^b - H
put lower:   s = H - P^a - U^a
```

对应 terminal payoffs 分别是：

```text
M min(S_T,K)
M max(K-S_T,0)
M min(S_T,K)
M max(S_T-K,0)
```

均 statewise nonnegative、support-certified nonconstant，因此使用 closed boundary `s >= 0`。

#### Executable put-call parity (`cross-asset`)

```text
s_1 = C^b - P^a - U^a + H
s_2 = U^b - H - C^a + P^b
```

两者 terminal payoff 恒为零，因此必须 `s > 0`。

#### Strike monotonicity (`cross-sectional`)

对 `K_1<K_2`：

```text
call: long C(K1), short C(K2) -> s = C_2^b - C_1^a
put:  short P(K1), long P(K2) -> s = P_1^b - P_2^a
```

#### Nonuniform strike convexity (`cross-sectional`)

对 `K_1<K_2<K_3`，先用 exact DECIMAL gaps 形成：

\[
a:b:c=(K_3-K_2):(K_3-K_1):(K_2-K_1),
\]

再除以共同 gcd 得到最小正整数。Positions 是 `(+a,-b,+c)`，spread：

```text
s = b * X_2^b - a * X_1^a - c * X_3^a
```

不得只支持等距 strikes、相邻 strikes、fractional weights 或未经 gcd normalization 的 scalar duplicates。

### 1.6 Spot mutation invariant

Spot mutation 只改变 valuation-time public `spot_close`，option quotes 不变。所有 pure option
cross-sectional candidates 不读取 spot，因此无条件不变量是：

```text
X_after == X_before
```

当前 recommended policy 在 mutation 前扫描完整 clean slice 并要求 signature `000`；只有在此前提下才有：

```text
accepted spot child => X_after = false
```

不得再次写成无条件的“spot-only child 永远不能含 X”。

### 1.7 Public/private 与 snapshot identity 边界

必须保护：

- frozen parent read-only；不得 append、sync、reprice、mutation 或改 identity；
- child 使用新 snapshot/task identity；
- public child 只投影 allowlisted `spot_close`、option `bid/ask/mid`、contract fields 与必要 curves/conventions；
- `settlement_price`、full OHLC、P dynamics、seed/RNG、before/after、private lineage、stored answer 不进入 child；
- option quote mutation 改一个 logical quote point，`bid/ask` 从 half-spread 确定性派生；
- requested signature、operator、target、selector trace、realized mutation count 不进入 public task/child ID；
- Trusted verifier 只从 public child 与 public variant 独立重算，不读 parent/private lineage。

### 1.8 P/Q、support 与 time-varying dynamics

必须保护：

- 套利定义在 `P` 及其 null sets 下；successor public contract 声明 `P ~ Q`；
- deterministic Q volatility 严格为正，给出正状态域上的 full conditional support；
- Q volatility 是 deterministic piecewise-linear instantaneous volatility；
- expiry reduction 使用 exact integrated variance：

\[
\sigma_{\mathrm{eff}}(t,T)
=\sqrt{\frac{\int_t^T\sigma_Q(u)^2du}{T-t}};
\]

- physical drift/volatility 是 deterministic piecewise-linear function，不是 constant drift；
- scalar legacy drift 只可解释为 constant-function special case；
- 不得把 published rounded-restart P path 替换 future continuous P/Q settlement law。

### 1.9 Calendar 必须继续 blocked

在 calendar evaluator、interim self-financing/admissibility proofs、exact boundary/ray verifier 和
reachability audit 完成前，必须保持：

```text
runtime_enabled = false
calendar_family = null
```

不得恢复 raw same-strike maturity price ordering，也不得把 quote 与 BSM theoretical price 的差叫做
calendar arbitrage。当前 arXiv reference 只能提供结构启发，不能替代本仓库 proportional-notional cost
和 dividend segment primitive 的证明。

---

## 2. 当前仍需返工的问题

### P0-1：successor 文档仍引用 v1 source of truth

核心错误：

`src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`

当前写 successor algorithm 来自：

```text
configs/variants/bsm_arbitrage_finding_f2a_v1.json
```

这里必须明确区分：

```text
legacy replay -> bsm_arbitrage_finding_f2a_v1.json
blocked successor -> bsm_arbitrage_finding_f2a_v2.json
future executable successor -> fresh immutable variant id/file
```

同时以下文档仍写：

```text
datasets/manifests/splits/f2a_v1.json
```

但 `authoring/configs/f2a_dataset_v2.json` 已声明：

```text
datasets/manifests/splits/f2a_v2.json
```

受影响文件：

- `README.md`
- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`

必须写清 legacy/successor 两条 artifact path，不能让 v2 dataset 覆盖或写入 v1 split manifest。

### P0-2：lineage-v2 schema 不能表示两端 signatures

`schemas/f2a-lineage-v2.schema.json` 当前同时规定：

```text
active_family_guards.minProperties = 1
inactive_family_guards.minProperties = 1
```

这导致：

- `000` 没有 active family，却被迫伪造一个 active guard；
- `111` 没有 inactive family，却被迫伪造一个 inactive guard。

必须允许空 object，并通过 signature-conditioned schema 验证正确 keys。至少增加 `if/then/else` 或
`oneOf` 约束：

```text
operator == clean_control_v1
  => mutation == null
  => realized_chi_F == 0
  => requested_signature == "000"
  => realized_signature == "000"

operator == mutate_option_price_point_...
  => mutation != null
  => mutation.field == "mid" 或新的 grouped pair shape
  => realized_chi_F == declared logical count

operator == mutate_underlying_spot_point_...
  => mutation != null
  => mutation.field == "spot_close"
  => realized_chi_F == 1

all accepted positive records
  => requested_signature == realized_signature
```

还应冻结：

- `variant_id`；
- `output_contract_id`；
- operator 与 changed logical/physical points 的对应；
- `before/after` 的数值类型与 tick units；
- active/inactive guard keys 必须与 signature bits 相符。

### P0-3：calendar certificate 把 `g_j` 与 `W_T2` 混淆

当前定义：

\[
W_{T_2}(x,y)=\frac{s_j}{D(t,T_2)}+g_j(x,y).
\]

Canonical predicate 明确分别检查 `s_j` 与 `g_j`。因此 cell vertices、actual boundaries、one-sided
limits 和 recession rays 必须证明：

\[
g_j(x,y)\ge0\quad\forall(x,y)\in(0,\infty)^2,
\]

而不是只证明：

\[
W_{T_2}(x,y)\ge0.
\]

反例：若 `D=1, s_j=10, g_j=-5`，则 `W=5>=0`，但 `g_j<0`，不满足当前 frozen
candidate-specific predicate。`W>=0` 是更宽的 standard total-wealth test，不能静默替换 catalogue
已经选择的 `s/g` 分离 predicate。

受影响文件：

- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
- `README.md` 中 calendar certificate 的“terminal wealth”表述
- `configs/variants/bsm_arbitrage_finding_f2a_v2.json` 的 future review target/tests

### P0-4：完整 mutation grammar 未落地

当前只有：

```text
mutate_option_price_point_v1
mutate_underlying_spot_point_v1
max_mutated_points = 1
```

缺少我们此前用于构造 `X-only / T-only / X+T` 的 parity-preserving equal call+put mutation。
完整数学见第 3--6 节。

必须做 scope 决策：

1. **完整 grammar 路线：**增加 versioned grouped call+put operator，并分配新的 mutation/dataset/
   lineage/variant identities；或
2. **严格 single-point F2A 路线：**保留当前两个 operators，但删除“必须覆盖七种 positives”的要求，
   只发布 deterministic reachability audit 证明可达的 signatures；paired mutation 移到更高 F level。

不得一边声明 `max_mutated_points=1`，一边把改变 call 与 put 两个 quote points 偷算作一个普通 quote point。
若使用 logical group，必须同时记录：

```text
logical_mutation_groups = 1
physical_quote_points_changed = 2
group_semantics = same-strike-same-expiry-call-put-equal-shift
```

并在 public difficulty contract 中明确最大 physical changed points，不能制造隐性难度或 lineage 歧义。

### P1-1：parent README 仍使用旧 guard

文件：

`snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md`

当前仍写：

```text
active family: initial surplus >= active_guard
inactive family: nearest surplus <= -inactive_guard
```

必须替换为 candidate-specific guard vector：

```text
setup-boundary distance (open/closed by payoff class)
terminal finite-vertex slack
terminal actual-boundary slack
terminal one-sided-limit slack
terminal recession-ray slope slack
strict-gain certificate
```

Guard 仅用于 sample selection；canonical verifier 仍执行 exact predicate。

### P1-2：dataset v2 没有冻结 signature distribution 与 clean allocation

`authoring/configs/f2a_dataset_v2.json` 虽列出八个 target statuses，但没有定义：

- requested signature 的稳定顺序和目标 count/mass；
- clean `000` 如何进入 dataset；
- `operator_balance` 是否只对 positives 生效；
- 每个 signature 允许哪些 operators；
- unreachable signature 的缺额如何处理；
- smoke `8` 与 pilot `128` 如何按 proven scope 分配。

当前 `operator_balance` 只有两个 mutation operators，没有 `clean_control_v1`。因此“一个 clean control +
七个 positives”无法由 config 唯一重放。

建议冻结如下结构，而不是依赖 JSON object insertion order：

```json
{
  "requested_signature_order": ["000", "100", "010", "001", "110", "101", "011", "111"],
  "scope_policy": "only_reachability_proved_signatures",
  "clean_control_policy": "one_per_selected_clean_slice_or_explicit_target_mass",
  "operator_policy_by_signature": {
    "000": ["clean_control_v1"],
    "100": ["parity_preserving_call_put_pair_v1"],
    "010": ["single_option_quote_v2", "underlying_spot_v2"],
    "001": ["parity_preserving_call_put_pair_v1", "underlying_spot_v2"]
  }
}
```

上面只是 shape 示例；最终列表必须由第 6 节 reachability audit 决定。

### P1-3：当前 split contract 在单一 parent 下不可用

Dataset v2 使用：

```text
grouping_key = parent_snapshot_id
names = train, validation, test
```

但当前所有 F2A tasks 都来自同一个 frozen parent snapshot。若严格按 parent grouping，所有 tasks 必须
进入同一个 split，另外两个 split 为空。

修复方案必须二选一：

1. 先物化多个独立 parent worlds，再按 parent snapshot 分组；或
2. 定义不会泄漏 latent world 的更细 grouping unit，并证明同一 clean slice/underlying/path block 不跨 split。

在只有一个 parent 时，不得声称已经生成可用 train/validation/test 三分数据集。

### P1-4：execution contract identity 与 stock-borrow assumption

Legacy v1 与 successor v2 复用：

```text
execution_contract_id = us-options-underlying-5bps-options-flat-050-v2
```

但 successor 增加/明确了 signed cash balance、borrowing/lending curve、continuous dividend
reinvestment/financing cost scope 等 normative fields。若这些字段改变经济语义，必须分配新 execution
contract ID；若只属澄清，必须增加 explicit backward-compatibility test，证明所有 legacy cashflows 不变。

此外 terminal-spot bid 使用 short underlying。当前 task 排除了真实 borrow availability/margin，但仍应
明确冻结：

```text
stock_borrow_availability = unlimited_within_catalogue_position_bounds
incremental_stock_borrow_fee = 0
short_sale_proceeds_usage = declared by cash-account contract
```

或加入实际 borrow-fee curve。不能公开 `borrow_or_carry_rate` 字段却让读者猜它是否是 stock borrow fee；
当前 `r-q` carry 不是额外 stock-loan fee。

### P1-5：blocking reasons 不完整

Variant/dataset 当前列出的 blockers 主要是 calendar、reachability 和 runtime。但 task plan 自己还承认：

- single-expiry binary64 cast/reduction order 未完全 machine-readable；
- lineage conditional schema 不完整；
- requested-signature distribution 未冻结；
- split 无法在单 parent 下工作；
- Solver environment/runtime proof 未完成。

这些都应进入 machine-readable `blocking_reasons`，避免未来只清掉 calendar flag 就错误启用 runtime。

### P1-6：新增 tests 不能证明文档所称性质

`tests/unit/test_f2a_math_contracts.py` 当前只做 primitive smoke checks：

- `A_S/B_S` 使用 endpoint equality 与 `pytest.approx`；
- terminal payoff 只抽查少量 spot values；
- convexity helper 不从 strikes 自己生成 gcd-normalized weights；
- spot invariance 通过一个直接忽略 spot 参数的 toy function 测试；
- 没有真实 complete-chain scanner、candidate enumeration 或 independent oracle。

这些 tests 可以保留，但不得命名/描述成完整 pathwise/self-financing proof。必须补充第 8 节测试。

### P2-1：旧 handoff 已过期但仍位于 repo root

`f2a_markdown_math_rework_handoff.md` 仍把已经修复的内容称为“当前问题”。建议：

- 移至 `docs/audits/2026-08-07_f2a_first_math_audit.md`；
- 顶部标记 `HISTORICAL / SUPERSEDED`；
- 链接本 handoff 或最终 normative contracts；
- repo-wide semantic lint 排除 historical audit，或只在 historical folder 中允许旧 strings。

不得让 root-level search 把历史 strict-positive/v1 问题误当成现行实现要求。

---

## 3. 完整 Mutation Grammar：统一数学表示

本节记录我们此前设计的完整 grammar，并把 transaction-cost threshold、candidate-specific boundary
和 signature reachability 统一为可编码形式。

### 3.1 Candidate position 与 executable initial surplus

对 candidate `j`，令 `w_{j,i}` 是 option `i` 的 signed integer position：

```text
w > 0 -> long option
w < 0 -> short option
```

每张合约的 executable initial cash outflow：

\[
O_i(w_{j,i})
=w_{j,i}^+(M_i ask_i+f_i)
-w_{j,i}^-(M_i bid_i-f_i).
\]

令 `O_j^other` 包含该 candidate 声明的 underlying/funding segment outflows，则：

\[
s_j=-\left(\sum_i O_i(w_{j,i})+O_j^{other}\right).
\]

注意：bid/ask、option fees、underlying proportional costs 和 funding 已经嵌入 `s_j`。后续 threshold
推导中的 `TC_j` 只能用于解释性分解，不能在 evaluator 中再次扣除。

### 3.2 Integer-tick mutation

令 mutation direction `d in {-1,+1}`，absolute tick magnitude `n` 来自冻结 integer grid，price
increment 为 `Delta_p`：

\[
\delta_n=d\,n\,\Delta_p.
\]

对一个 logical option quote point，保持 parent half-spread：

\[
mid_i'=mid_i+\delta_n,\qquad
bid_i'=bid_i+\delta_n,\qquad
ask_i'=ask_i+\delta_n.
\]

只要 domain gates 通过，candidate initial surplus 对 `n` 是 exact affine response：

\[
s_j(n)=s_j(0)-M_iw_{j,i}\delta_n
=s_j(0)+c_j(d)n,
\]

其中：

\[
c_j(d)=-d\,\Delta_p M_iw_{j,i}.
\]

若一个 grouped operator 同时等量移动 option set `G`：

\[
s_j(n)=s_j(0)-\delta_n\sum_{i\in G}M_iw_{j,i}.
\]

Option quote mutation 不改变 fixed candidate 的 terminal payoff `g_j`；它只移动 setup surplus。Family
bit 仍必须重新枚举全部 candidates，因为 family truth 是 candidate union，而不是一个预存 margin。

### 3.3 Candidate-specific open/closed trigger

对 candidate `j`，先验证 terminal certificate class：

```text
Z-class: g_j identically zero
N-class: g_j >= 0 P-a.s. and P(g_j > 0) > 0
C-class: calendar pathwise certificate supplies the corresponding result
```

Setup boundary：

```text
Z-class -> open boundary:   s_j > 0
N-class -> closed boundary: s_j >= 0
C-class -> candidate-specific boundary plus full terminal certificate
```

若 `c_j(d)>0`，在理想 exact-real arithmetic 下：

\[
n_{j,\mathrm{closed}}
=\min\{n\in\mathbb N:s_j(0)+c_jn\ge0\},
\]

\[
n_{j,\mathrm{open}}
=\min\{n\in\mathbb N:s_j(0)+c_jn>0\}.
\]

如果把 baseline slack 与 costs 仅用于分析地分写成：

\[
s_j(0)=-\operatorname{slack}_j-TC_j,
\]

则连续近似 threshold 为：

\[
\tau_j\approx
\frac{\operatorname{slack}_j+TC_j}{\text{positive mutation slope}_j}.
\]

这就是 transaction cost / option fee 移动 family activation threshold 的数学来源。不同 family 的
option leg counts、directions 和 underlying segments 不同，所以 `TC_j` 与 slope 都不同；同一个 mutation
幅度可以只激活一种、两种或三种 family。

实际 authoring 不得只使用上述连续公式。Canonical selector 必须在 declared binary64 cast/reduction
order 下，对每个 integer tick 运行 candidate-specific evaluator，并区分 `>` 与 `>=`。

### 3.4 Family bit 与可达窗口

记 family `f in {X,U,T}` 的 candidate set 为 `J_f`：

\[
B_f(n)=\mathbf 1\left\{\exists j\in J_f:\mathcal A_j(n)=\mathrm{true}\right\}.
\]

Realized signature：

\[
B(n)=(B_X(n),B_U(n),B_T(n)).
\]

对 requested signature `b=(b_X,b_U,b_T)`，exact feasible tick set：

\[
\mathcal I_b=
\left(\bigcap_{f:b_f=1}\mathcal I_f^{on}\right)
\cap
\left(\bigcap_{f:b_f=0}\mathcal I_f^{off}\right)
\cap\mathcal I_{domain}
\cap\mathcal I_{guards}.
\]

其中：

- `I_f^on`：family 至少一个 candidate 满足 canonical predicate 和 active guards；
- `I_f^off`：family 所有 candidates 均不满足 predicate，且 inactive guards 通过；
- `I_domain`：finite/nonnegative/side-order/tick gates；
- `I_guards`：setup 与 terminal guard vector。

Reachability 的定义是：

\[
\mathcal I_b\cap\text{declared integer tick grid}\ne\varnothing.
\]

Selector 只接受稳定顺序中的第一个元素。若交集为空，必须 deterministic skip 并记录不可达诊断；
不能改 fee、扩大 grid、改 parent 或随机 retry。

---

## 4. Operator A：Single Option Quote Mutation

### 4.1 定义

选择一个 `(valuation_date, option_id)`，只移动该 option 的 `mid/bid/ask`：

```text
single_option_quote_shift(target=i, delta_ticks=d*n)
```

Strike、expiry、call/put、spot、curves、其他 option quotes 全部不变。

### 4.2 为什么 single call/put 会直接推动 `U`

以 target call 为例，equal half-spread shift `delta` 后，put-call parity 两个方向变为：

\[
s_1'=C^b+M\delta-P^a-U^a+H=s_1+M\delta,
\]

\[
s_2'=U^b-H-(C^a+M\delta)+P^b=s_2-M\delta.
\]

因此 upward call shift 推动第一个 parity direction，downward shift 推动第二个 direction。Target put
同理，只是方向对调。

在 frictionless exact-parity baseline 中，任意非零 single-call/put shift 都产生 `U`。在当前 bid/ask、
fee 和 underlying-cost contract 下，`U` 具有 nonzero no-trade band；所以必须由 exact threshold audit
确定从哪个 tick 起 `U=1`。

### 4.3 我们此前设计的 U-anchored grammar

为了用 single quote mutation 构造含 `U` 的四种 positives，authoring 必须选择满足以下 ordering 的
target/direction/profile：

\[
\tau_U<\min(\tau_X,\tau_T).
\]

然后随 mutation magnitude 增大：

```text
before tau_U                         -> 000
after tau_U, before tau_X/tau_T      -> 010  (U only)
if tau_X < tau_T: next interval      -> 110  (X + U)
if tau_T < tau_X: next interval      -> 011  (U + T)
after both X and T thresholds        -> 111  (X + U + T)
```

单个 target/direction 的 threshold order 只能先经过 `110` 或 `011` 之一；要在 dataset 中同时得到二者，
需要不同 target、direction、expiry/strike node 或经过版本化的 execution profile。

因此我们此前把 single-call/single-put operator 的 constructive target set 写成：

```text
010, 110, 011, 111
```

它不能在 U-anchored grammar 下构造：

```text
100, 001, 101
```

如果实际 reachability audit 发现某节点 `tau_X < tau_U` 或 `tau_T < tau_U`，single quote 也可能出现
X-only/T-only 等别的窗口；但那属于另外一个 threshold regime，不能与 U-anchored constructive claim
混为一谈。必须由 versioned audit 明确采用哪种 grammar，而不能在 `AGENTS.md` 中无条件写“any subset”。

---

## 5. Operator B：Parity-Preserving Equal Call+Put Mutation

### 5.1 定义

选择同一 valuation time、underlying、expiry、strike、multiplier 的 call/put pair，同时等量移动：

\[
C^{mid/bid/ask}\mapsto C^{mid/bid/ask}+\delta,
\]

\[
P^{mid/bid/ask}\mapsto P^{mid/bid/ask}+\delta.
\]

建议 operator ID 使用新的 immutable name，例如：

```text
mutate_call_put_pair_equal_shift_v1
```

不要复用 `mutate_option_price_point_v1`。

### 5.2 Put-call parity 的 exact invariance

假设 call/put multiplier 相同。第一个 executable parity surplus：

\[
\begin{aligned}
s_1'
&=(C^b+M\delta)-(P^a+M\delta)-U^a+H\\
&=C^b-P^a-U^a+H=s_1.
\end{aligned}
\]

第二个方向：

\[
\begin{aligned}
s_2'
&=U^b-H-(C^a+M\delta)+(P^b+M\delta)\\
&=U^b-H-C^a+P^b=s_2.
\end{aligned}
\]

Option fees 也不变，因为 positions/leg counts 不变。因此同 strike/expiry parity candidates 对 equal
call+put shift 完全不变。

### 5.3 重要限制：parity-preserving 不等于整个 U family preserving

`U` 还包括 discounted price bounds。Equal upward shift 对 bounds 的影响：

```text
call upper  -> s + M*delta
call lower  -> s - M*delta
put upper   -> s + M*delta
put lower   -> s - M*delta
```

所以 pair mutation 只无条件保持 parity，不无条件保持全部 `cross-asset` bit。要构造
`100 / 001 / 101`，必须额外要求整个 mutation window 内所有 cross-asset bounds/parity candidates
保持 inactive 且通过 inactive guard：

\[
B_U(n)=0\quad\text{on the accepted pair-mutation window}.
\]

### 5.4 我们此前设计的 non-U grammar

在 `U` inactive band 内，equal call+put shift 可以改变：

- call/put strike-shape candidates `X`；
- 使用该 pair 的 two-expiry candidates `T`。

若 thresholds 满足：

```text
tau_X < tau_T -> 100, then 101
tau_T < tau_X -> 001, then 101
```

跨不同 targets/directions 得到：

```text
100  (X only)
001  (T only)
101  (X + T)
```

这正好补齐 U-anchored single quote operator 无法构造的三个 positives。

### 5.5 Logical/physical mutation count 必须诚实

该 operator 改变两个 logical quote points。实现只能二选一：

1. `max_mutated_quote_points=2`，把它归入 multi-point mutation level；或
2. 定义一个公开的 grouped logical mutation，但 lineage 同时记录 physical count `2`。

不得只记录 `realized_chi_F=1` 而隐藏两个 option IDs/before/after values。Difficulty、lineage 和 public
`max_mutated_points` 的含义必须一致且 versioned。

---

## 6. Operator C：Underlying Spot Mutation

### 6.1 定义与 X invariant

\[
S_t' = S_t+\zeta_n,
\qquad \zeta_n=d\,n\,\Delta_S.
\]

所有 option quotes 不变，因此：

\[
B_X(S_t')=B_X(S_t).
\]

在 clean `000` policy 下，spot operator 只能产生：

```text
000, 010, 001, 011
```

不能产生含 `X` 的 signature。

### 6.2 Cross-asset surplus 对 spot 的 exact slopes

因为：

\[
A_S(t,T;S)=a_T S,\qquad B_S(t,T;S)=b_T S,
\]

其中：

\[
a_T=\frac{1+\kappa}{1-\kappa}e^{-Q_q/(1+\kappa)},
\qquad
b_T=\frac{1-\kappa}{1+\kappa}e^{-Q_q/(1-\kappa)},
\]

所以 spot shift `zeta` 对 single-expiry cross-asset surpluses 的变化为：

```text
call upper parity/bound direction using -U^a -> -M*a_T*zeta
call lower using +U^b                  -> +M*b_T*zeta
put upper                              -> 0
put lower using -U^a                   -> -M*a_T*zeta
parity direction 1                     -> -M*a_T*zeta
parity direction 2                     -> +M*b_T*zeta
```

这给出 spot operator 的 `U` thresholds。

### 6.3 Calendar initial-surplus slope

Calendar candidate 的 first segment outflow：

\[
\Phi_{t,T_1}(\Delta_0;S_t)
=\phi_{T_1}(\Delta_0)S_t,
\]

其中：

\[
\phi_{T_1}(\Delta_0)
=\Delta_0^+a_{T_1}-\Delta_0^-b_{T_1}.
\]

因此：

\[
s_j(S_t+\zeta)=s_j(S_t)-\phi_{T_1}(\Delta_0)\zeta.
\]

固定 candidate 的 `g_j(x,y)` 不依赖 valuation-time mutated `S_t`，但 calendar family 仍必须通过完整
pathwise certificate。根据 `tau_U` 与 `tau_T` 的顺序：

```text
tau_U < tau_T -> 010, then 011
tau_T < tau_U -> 001, then 011
```

Spot mutation 是 `U/T` signatures 的冗余构造和重要 property-test operator，但它永远不直接改变 X。

---

## 7. 完整 Grammar 汇总与 scope 决策

### 7.1 Constructive target table

| Operator | Changed public points | Exact invariant | Intended constructive positives |
|:---|:---:|:---|:---|
| Clean control | 0 | baseline full scan `000` | `000` |
| Single option quote | 1 quote point | none across X/U/T; U-anchored regime required | `010`, `110`, `011`, `111` |
| Equal call+put pair | 2 quote points or 1 declared group | same-pair executable parity unchanged; whole U still needs bounds guard | `100`, `001`, `101` |
| Underlying spot | 1 spot point | `X_after == X_before` | `010`, `001`, `011` |

Union of the first three constructive rows gives all seven positive signatures；spot operator provides additional
U/T reachability and invariance tests。

### 7.2 Exact reachability—not table assertion—determines publication

上表是 grammar design，不是 reachability proof。对每个 signature 必须输出：

```text
requested_signature
execution_profile_id
operator_id
target identifiers
direction
integer tick window(s)
active candidate ids and setup/terminal slacks
inactive nearest candidates and setup/terminal slacks
domain-gate window
reachable / unreachable diagnostic
```

只有存在 exact integer tick window 的 signature 才能进入当前 dataset acceptance。

### 7.3 推荐 identity 策略

当前 blocked contracts 已声明 immutable。不要原地启用或改变其经济语义。若采用完整 grouped-pair grammar，
建议创建新的并行 identities（具体编号可按 repo registry 决定）：

```text
legacy replay:
  variant v1 / catalogue v2 / mutation v1 / dataset v1 / lineage v1

blocked first successor review record:
  variant v2 / catalogue v3 / mutation v2 / dataset v2 / lineage v2

corrected complete-grammar successor:
  fresh variant id
  fresh candidate catalogue id
  fresh mutation engine id
  fresh dataset config id
  fresh lineage schema id
  fresh submission/output id if shape or semantics changes
  fresh execution contract id if funding/borrow semantics changes
```

旧 configs/schemas 保留 replay 或明确标记 `SUPERSEDED_BLOCKED`，不得覆盖。

---

## 8. 必须新增的 tests

### 8.1 Candidate predicate boundaries

必须 exact 测试：

```text
parity: s < 0 -> false
parity: s == 0 -> false
parity: s > 0 and g == 0 -> true

nonconstant nonnegative payoff: s < 0 -> false
nonconstant nonnegative payoff: s == 0 -> true
nonconstant nonnegative payoff: s > 0 -> true

g negative anywhere -> false under frozen s/g-separated predicate
```

增加显式 regression：`s>0, W>=0, g<0` 仍必须 false。

### 8.2 Terminal-spot primitive proof tests

不能只比较最终公式。对任意 deterministic piecewise q partition：

1. 重放 initial buy/short shares；
2. 对每个 dividend interval 重放 reinvestment/financing trade；
3. 每笔使用 correct buy/sell proportional cost；
4. terminal liquidation/cover 后恰好得到 `+S_T` 或 `-S_T` exposure；
5. cash ledger self-financing；
6. discounted wealth 有 declared finite lower bound；
7. 分割 interval 与合并 integrated yield 结果一致。

### 8.3 Single-expiry pathwise tests

对每个 bounds/parity/monotonicity/convexity strategy：

- 测 setup cashflow 与每条 leg fee；
- 按 strike cells 检查 affine slope/intercept；
- 检查所有 knots 的 actual values；
- 检查 left/right cells 与 positive tail slope；
- 不以 5 个 sampled spot values 代替 proof；
- convexity weights 从 exact DECIMAL strikes 自动生成并 gcd-normalize；
- scalar duplicate positions 被拒绝。

### 8.4 Mutation-response algebra tests

#### Single quote

对每个包含 target option 的 candidate：

\[
s_j(n)-s_j(0)=-M_iw_{j,i}\delta_n.
\]

验证 call/put parity 两方向 slope signs 相反。

#### Equal call+put pair

验证：

```text
both executable parity surpluses exactly unchanged
call/put upper bounds move +M*delta
call/put lower bounds move -M*delta
all non-target quotes unchanged
physical changed points == 2
```

#### Spot

验证完整 scanner：

```text
X_after == X_before
U candidate slopes match a_T/b_T formulas
calendar setup slope matches phi_T1(Delta_0)
```

不要用一个直接忽略 spot 参数的 toy function 作为最终 property test。

### 8.5 Threshold-order/signature tests

构造小型可手算 market，使 thresholds 分别满足：

```text
tau_U < tau_X < tau_T -> 010 -> 110 -> 111
tau_U < tau_T < tau_X -> 010 -> 011 -> 111
pair with U inactive, tau_X < tau_T -> 100 -> 101
pair with U inactive, tau_T < tau_X -> 001 -> 101
spot, tau_U < tau_T -> 010 -> 011
spot, tau_T < tau_U -> 001 -> 011
```

每个测试同时验证 option fee、underlying cost、open/closed boundary 和 integer tick rounding。

### 8.6 Lineage schema tests

至少覆盖：

- valid clean `000`；
- valid each positive signature；
- clean control with mutation object -> reject；
- mutation operator with null mutation -> reject；
- operator/field mismatch -> reject；
- `realized_chi_F` mismatch -> reject；
- requested != realized -> reject；
- grouped pair missing one leg/before/after -> reject；
- active/inactive guard keys inconsistent with signature -> reject；
- public submission contains requested signature/selector trace -> reject。

### 8.7 Calendar certificate tests

在启用 calendar 前：

- exact ledger at `t/T1/T2`；
- option fees and each underlying cost charged exactly once；
- `g_j`, not only `W_T2`, checked at all finite vertices/boundaries/limits/rays；
- actual right-cell boundary assignment；
- full positive state domain；
- strict-gain open-cell witness；
- interim discounted-wealth lower bound；
- raw maturity ordering、Monte Carlo grid 和 frictionless repricer rejected。

### 8.8 Reachability and no-label-leakage integration tests

- 对每个 requested signature 输出 exact reachable window 或 deterministic unreachable diagnostic；
- selector 只接受 exact bitmask + guards；
- fee/profile 不在 label 之后改变；
- task/child IDs 不编码 signature/operator/mutation status；
- verifier 删除 private lineage 后 truth 不变；
- only reachability-proved signatures enter dataset acceptance；
- single-parent dataset 不伪造三分 split。

---

## 9. 逐文件返工清单

### 必须修改或新建版本

- `AGENTS.md`
  - 把 one-quote “may activate any subset” 改为 threshold/reachability-dependent；
  - 链接/概述完整 three-operator grammar；
  - 保留 canonical predicate 与 family order。

- `README.md`
  - 修正 `f2a_v1`/`f2a_v2` split path；
  - calendar exact certificate 写 `g_j>=0`，不要只写 terminal `W`；
  - 七 signatures 明确为 reachability-gated；
  - 说明 grouped pair 的 logical/physical point count。

- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
  - successor source 改指 v2/fresh successor config；
  - 修正 `g_j`/`W_T2`；
  - 加入本文完整 grammar 与 threshold derivation；
  - 修正 lineage requested/realized signature 当前是 top-level 还是 guard-evidence fields；
  - 删除“当前 blocked successor 也必须覆盖全部七种”的矛盾句；
  - split path version 化。

- `snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md`
  - 删除 initial-surplus-only family guard；
  - 同步 candidate-specific setup/terminal guard vector；
  - 说明 parent 本体未修改，README 仅描述 future child contract。

- `src/synthetic_derivatives/authoring/README.md`
  - 增加 requested-signature distribution、operator routing、grouped pair materialization；
  - 明确 two physical quote points；
  - reachability-gated publication。

- `docs/authoring_pipeline.md`
  - 增加 grouped pair copy-on-write/lineage atomicity；
  - public/private point-count semantics；
  - no double fee/cost。

- `src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md`
  - 更新 F2A single-point vs grouped/multi-point scope；
  - 不把 schema combinations 写成 runtime coverage。

- `configs/variants/bsm_arbitrage_finding_f2a_v2.json`
  - 若保持 immutable，只标记 superseded blocked；新建 fresh variant；
  - 修正 calendar certificate target 为 `g_j`；
  - execution/borrow assumptions；
  - 增加完整 blocking reasons 与 operation order。

- `configs/mutations/f2a_point_v2.json`
  - 若保持 single-point，只作为 blocked/superseded record；
  - 完整 grammar 路线需新 mutation config，加入 pair operator 与 physical count。

- `authoring/configs/f2a_dataset_v2.json`
  - 若 immutable，另建 fresh dataset config；
  - requested-signature order/distribution；
  - clean-control allocation；
  - operator policy；
  - reachability-gated scope；
  - usable split contract。

- `schemas/f2a-lineage-v2.schema.json`
  - 不要在已声明 immutable schema 下静默改变；创建 fresh schema；
  - 修复 empty active/inactive ends；
  - operator/mutation/chi/signature conditionals；
  - grouped pair shape。

- `tests/unit/test_f2a_math_contracts.py`
  - 保留 smoke tests；新增 exact algebra/pathwise tests，不把 sampled grid 称为 proof。

- `tests/unit/test_f2a_repo_contracts.py`
  - identity mapping；
  - v1/v2/fresh successor coexistence；
  - lineage conditionals；
  - no stale successor references to v1 paths；
  - no unconditional seven-signature capability claim。

- `tests/unit/README.md`
  - 准确说明 smoke、algebraic proof、pathwise proof、calendar blocked status。

### 历史文档处理

- `f2a_markdown_math_rework_handoff.md`
  - 移入 dated audit folder；
  - 标记 historical/superseded；
  - 不作为 normative source of truth。

---

## 10. Repo-wide 验收搜索

修复后至少运行：

```bash
rg -n \
  "candidate_spread_strictly_greater_than_zero|candidate_spread > 0|any subset|splits/f2a_v1.json|算法来自.*f2a_v1|terminal wealth|W_T2.*>=|active_guard|inactive_guard|calendar_family" \
  AGENTS.md README.md docs environments snapshots src configs authoring schemas tests
```

对 legacy/historical matches 必须逐个证明其 scope 标记明确；不能靠全局忽略。

还应运行：

```bash
python -m compileall -q src tests
pytest -q tests/unit/test_f2a_math_contracts.py
pytest -q tests/unit/test_f2a_repo_contracts.py
pytest -q tests/unit
pytest -q tests/public
git diff --check
```

若 calendar 或 grouped-pair runtime 尚未实现，对应 tests 应验证它们保持 blocked，而不是伪造 pass。

---

## 11. Codex 最终回报格式

下一轮完成后必须报告：

1. 修改/新增的 Markdown、config、schema、test 路径；
2. 本文每个 P0/P1/P2 finding 的处理结果；
3. legacy、blocked review、fresh successor identities 的完整映射；
4. 选择“完整 grouped-pair grammar”还是“严格 single-point scope”，以及理由；
5. 每个 operator 的 exact changed logical/physical point counts；
6. deterministic reachability audit 对每个 signature 的结果；
7. `g_j` 与 `W_T2` tests；
8. lineage `000/111` 和 operator conditional schema tests；
9. 完整测试命令与 pass/fail；
10. 仍然 blocking 的项目；
11. 明确确认 frozen parent DB/manifest 未修改，且未生成未经授权的 child/dataset。

在上述回报完成前，不得把 F2A 标为 end-to-end ready。
