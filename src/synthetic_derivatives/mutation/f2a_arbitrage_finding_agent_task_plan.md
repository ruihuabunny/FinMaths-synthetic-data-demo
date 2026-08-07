# F2A Calendar Arbitrage v4 实施合同与完成记录

## 0. 返工结论与当前基线

本计划以仓库根目录
[`f2a_calendar_enable_v4_rework_handoff.md`](../../../f2a_calendar_enable_v4_rework_handoff.md)
为强制交接合同。下一条 F2A successor 必须是可执行的 calendar-enabled v4，不再接受新的
blocked review identity。

当前仓库事实是：

- v1 是 legacy replay；
- v2/v3 是历史 blocked review records，必须保持原身份与语义；
- v3 的 `runtime_enabled=false`、`calendar_family=null` 和组合爆炸的 calendar target 不能原地修改；
- `f2a_contract.py` 保留 multiplier-safe single-expiry/terminal-spot primitives；v4 child materializer、
  完整 oracle、Solver、verifier 和 smoke orchestration 已落地；
- 生产 F2A parent DuckDB/manifest 仍须由运行方显式提供；clean clone 使用明确标注 CI-only 的
  `tests/fixtures/f2a/v4_parent.json` 与 sidecar integrity manifest，不作 silent fallback。

v4 的唯一完成态是：

```text
status                 = EXECUTABLE
runtime_enabled        = true
blocking_reasons       = []
calendar_family        = transaction-cost-aware-two-expiry-call-stock-flip-v1
publication_task_count > 0 after real reachability audit
```

Executable code、独立 verifier、真实 reachability 和端到端 `001` smoke 已通过，v4 配置因此以
上述 enabled 状态提交；v2/v3 的 null/false 仅保留为 historical immutable records。

Tracked CI fixture 的实际 reachability 结果为：`000 [0,0]`、`100 [6,38]`、`010 [16,29]`、
`001 [132,290]`、`110 [30,3004]`、`101 [39,4096]`、`011 [39,4096]`、
`111 [42,4096]`。每个窗口都来自所记录 target/sign 的真实 public quote mutation 与 full oracle rescan。

F2A 继续使用明确选择的 frozen parent：

```text
database             = snapshots/generated/f2a/parents/
                       DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/parent.duckdb
manifest             = 同目录 parent.manifest.json
snapshot_id          = DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2
revision / status    = 1 / FROZEN
generator_config_id  = quantlib-randomized-tdgbm-metals-f2a-parent-v2
generator / schema   = 0.8.0 / 1.6.0
```

缺少该 parent 时必须失败并报告，不能 fallback 到仓库默认的 DRAFT v3 development database 或 legacy
public snapshot。Parent 只读；每次 mutation 物化到新的 child identity。

## 1. 新身份与兼容边界

### 1.1 身份矩阵

```text
legacy replay:
  variant v1 / catalogue v2 / mutation v1 / dataset v1 / lineage/submission v1

historical blocked records:
  variant v2 / catalogue v3 / mutation v2 / dataset v2 / lineage/submission v2
  variant v3 / catalogue v4 / mutation v3 / dataset v3 / lineage/submission v3

new executable successor:
  variant_id             = bsm-arbitrage-finding-f2a-v4
  candidate_catalogue_id = bsm-f2a-candidate-catalogue-v5
  mutation_engine_id     = f2a-complete-mutation-v4
  dataset_config_id      = f2a-dataset-v4
  lineage_schema         = schemas/f2a-lineage-v4.schema.json
  submission_schema      = schemas/submission-v4.schema.json
  execution_contract_id  = us-options-underlying-5bps-options-flat-050-v4
  output_contract_id     = arbitrage-opportunity-type-trajectory-v5
```

若落地前发现这些 ID 已被占用，整体顺延到下一组空闲版本；variant、catalogue、mutation、dataset、
lineage、submission、execution、output、task manifest、oracle 与 Solver 引用必须一起更新。

V4 保留 v3 正确的 `s_j/g_j` 分离、单期限现金流和三类 mutation grammar，但不继承 v3 的 blocked
状态、calendar 任意仓位网格或 continuous-time interim-wealth blocker。

### 1.2 配置与 schema 文件

新增而不覆盖历史文件：

```text
configs/variants/bsm_arbitrage_finding_f2a_v4.json
configs/mutations/f2a_complete_v4.json
authoring/configs/f2a_dataset_v4.json
schemas/f2a-lineage-v4.schema.json
schemas/submission-v4.schema.json
datasets/manifests/splits/f2a_v4.json
```

V4 继续使用七维 `task-v2`/`difficulty-v2` registry 和 `F="F2A"`；不修改六维 v1 manifests，也不新增
顶层 `configs/arbitrage/` 或 `src/synthetic_derivatives/arbitrage/`。

历史与新 successor 的公开 contract 必须按身份路由：legacy replay 读取 v1，首次 blocked review
读取 v2，完整 grammar review 读取
`configs/variants/bsm_arbitrage_finding_f2a_v3.json`，新的 executable runtime 读取
`configs/variants/bsm_arbitrage_finding_f2a_v4.json`。对应 split artifacts 保持并行：

```text
datasets/manifests/splits/f2a_v1.json  # legacy only
datasets/manifests/splits/f2a_v2.json  # blocked v2 only
datasets/manifests/splits/f2a_v3.json  # blocked v3 historical audit only
datasets/manifests/splits/f2a_v4.json  # executable reachability-proved scope
```

## 2. 数学、信息与市场合同

### 2.1 Measure、状态、时间与支持集

套利定义在物理测度 `P` 下。定价上下文使用与 money-market numeraire
`USD-MONEY-MARKET-ACCOUNT-v1` 关联的
`Q = USD-MONEY-MARKET-Q-v1`，并公开冻结 `P ~ Q` 到每个候选 horizon 的 null-set 合同。

状态是严格为正的 ex-dividend spot。估值时刻为 `t`，calendar candidate 的早、晚现金结算时刻为
`T1`、`T2`，满足 `t < T1 < T2`。Filtration 由 spot process 增广生成；在 `T1` 只使用已观察到的
`S_T1` 选择公开、可预测的 stock-flip branch。Rate、dividend/carry、calendar、day-count 和确定性
`Q` volatility curve 在 `t` 已知。

在 `Q` 下：

\[
\frac{dS_u}{S_u}=(r(u)-q(u))du+\sigma_Q(u)dW_u^Q.
\]

`Actual365Fixed`、calendar-day node offsets、piecewise-linear instantaneous annualized volatility、
flat extrapolation 和 exact integrated variance reduction 延续 v3 合同。严格正 volatility 与
`P ~ Q` 给出每段 transition 的正条件密度，因此 `(S_T1,S_T2)` 的每个非空 open rectangle 都有
正 `P` 概率。Parent 的 rounded-restart historical `P` path 不是 future settlement law。

### 2.2 有限日期 semi-static admissibility

V4 明确采用以下有限 catalogue 策略类：

```text
trading dates                   = t, T1, T2 only
option positions               = exactly -1 early call and +1 later call
state-dependent rebalance      = exactly once at T1
underlying quantities          = real-valued; no integer-share rounding
cash account                   = signed; unlimited borrowing/lending on the same curve
margin requirement             = none
terminal requirement           = pathwise g_j >= 0 plus candidate-specific strict-gain rule
```

`A_S/B_S` 内的连续 dividend reinvestment/financing 是每个 segment 的 deterministic
finite-variation implementation，不是额外 adaptive rebalance。仓位有公开上界、交易日期有限且只有一个
state-dependent rule，所以 doubling strategy 在该 catalogue 中结构上不可能。V4 不额外要求所有 spot
state 上统一的 intermediate-wealth lower bound；这是 signed cash account、unlimited borrowing 和
no-margin 下的显式有限日期市场合同，不得再用未来 margin/continuous-time admissibility 扩展阻塞 v4。

结论只能表述为：

> catalogue-scoped executable two-expiry calendar arbitrage under the v4 finite-date semi-static contract.

不得表述为 full-market dynamic arbitrage、global calendar no-arbitrage 或 continuous-time `NFLVR`。

### 2.3 Execution contract

V4 冻结：

```text
currency                         = USD
option execution                  = directional bid/ask
option fee per contract per side = 0.50 USD
option holding                    = hold to cash settlement
option settlement fee            = 0
underlying cost per side          = 0.0005 * abs(traded notional)
underlying/cash account           = real-valued shares / signed balance
cash borrowing and lending        = same money-market curve
stock borrow                      = unlimited within catalogue bounds
incremental stock borrow fee      = 0
option and underlying tick        = 0.01 USD / ROUND_HALF_EVEN
```

每个 option price/payoff 都乘该行正的 contract multiplier；每条 option leg 按 `abs(position)` 收费。
Underlying 的 `5 bps` 成本对 initial、dividend reinvestment/financing、`T1` rebalance 和 terminal
liquidation 中的实际交易生效。Transaction costs 是经济现金流，不是 numerical tolerance。

对 `u < v` 和 spot `S_u > 0`，令

\[
Q_q(u,v)=\int_u^v q(s)ds,
\]

\[
A_S(u,v;S_u)=S_u\frac{1+\kappa}{1-\kappa}
\exp\!\left(-\frac{Q_q(u,v)}{1+\kappa}\right),
\]

\[
B_S(u,v;S_u)=S_u\frac{1-\kappa}{1+\kappa}
\exp\!\left(-\frac{Q_q(u,v)}{1-\kappa}\right),
\]

\[
\Phi_{u,v}(\Delta;S_u)=\Delta^+A_S(u,v;S_u)-\Delta^-B_S(u,v;S_u).
\]

`Phi` 已是完整 segment cash outflow，外层禁止再次扣 underlying fee，也禁止用 frictionless
`S exp(-qT)` 替换。

## 3. Candidate catalogue v5

Canonical family 顺序固定为：

```text
(cross-sectional, cross-asset, calendar)
```

### 3.1 保留的单期限 families

V4 按值保留 v3 的 transaction-cost-aware scanner：

- `cross-sectional`: strike monotonicity、nonuniform strike convexity；
- `cross-asset`: discounted price bounds、executable put-call parity。

Scanner 在形成任何跨 strike candidate 前，必须按 valuation time、currency、underlying、expiry、
exercise/settlement 和共同 contract multiplier 分桶。不同 multiplier 的 quotes 不能进入同一个
monotonicity/convexity candidate。

对一张 option：

```text
X_ask = M * ask + f
X_bid = M * bid - f
U_ask = M * A_S(t,T;S_t)
U_bid = M * B_S(t,T;S_t)
H     = M * K * D(t,T)
```

Cross-asset 依次枚举：

```text
call upper: C_bid - U_ask
call lower: U_bid - H - C_ask
put upper:  P_bid - H
put lower:  H - P_ask - U_ask
parity 1:   C_bid - P_ask - U_ask + H
parity 2:   U_bid - H - C_ask + P_bid
```

Bounds 的 terminal payoff 非负且 nonconstant，`s >= 0` 激活；parity payoff 恒为零，只在
`s > 0` 时激活。Strike monotonicity 与 gcd-normalized nonuniform convexity 延续 v3 的 executable
sides、整数仓位和 closed `s >= 0` boundary。

### 3.2 Calendar eligibility 与有限枚举

启用唯一 calendar family：

```text
transaction-cost-aware-two-expiry-call-stock-flip-v1
```

对每个 valuation slice，按 `(underlying_id,T1,T2,K,M)` 枚举一个 candidate，且必须同时满足：

```text
t < T1 < T2
T1/T2 都有同 strike K 的 European cash-settled call
两张 call 的 multiplier 是同一个正数 M
currency、underlying、exercise、settlement conventions 相同
discount/dividend curves 覆盖 [t,T2]
F_12 = D(t,T1) / D(t,T2) >= 1
```

四 expiries、七个 common strikes 的完整 chain 在当前正 rate parent 下应产生
`C(4,2) * 7 = 42` 个 calendar candidates/underlying/date slice。实现必须从 public child 实际枚举并
报告 count，不能硬编码 42。未来若某个 expiry pair 有 `F_12 < 1`，本 family 跳过该 pair；不得通过
改 rate、放宽 theorem 或 relabel 来保留 candidate。

稳定顺序为：

```text
valuation_date
underlying_id
T1 ascending
T2 ascending
strike ascending
call option_id at T1
call option_id at T2
```

Candidate ID：

```text
calendar-call-stock-flip-v1|<underlying>|<date>|<T1>|<T2>|<K>|<M>
```

禁止随机 candidate、nonlinear optimization、Monte Carlo、finite spot grid 或 v3 的任意 option-position/
`Delta_1` cell-value 组合枚举。

### 3.3 Canonical call-stock-flip

定义

\[
\beta_{12}=\frac{B_S(T_1,T_2;x)}{x}
=\frac{1-\kappa}{1+\kappa}
\exp\!\left(-\frac{Q_q(T_1,T_2)}{1-\kappa}\right).
\]

由 v4 的 nonnegative cash-dividend yield 和 `0 <= kappa < 1` 得
`0 < beta_12 <= 1`。公开冻结经济 over-hedge exposure：

```text
calendar_bridge_exposure_buffer_ratio = eta = 1e-8
```

`eta` 是通过 `Phi` 付费的真实 underlying exposure，不是 verifier tolerance。

在 `t`：

```text
short 1 T1 call at bid
long  1 T2 call at ask
Delta_0 = M * (1 - beta_12 + eta)
```

`Delta_0` 使用 real-valued shares，禁止 integer rounding。在 `T1` 观察 `x=S_T1` 后使用唯一的
predictable right-continuous rule：

\[
\Delta_1(x)=
\begin{cases}
0,&0<x<K,\\
-M,&x\ge K.
\end{cases}
\]

边界 `x=K` 属于 high-state branch。

定义 fee-adjusted executable option amounts：

\[
C_1^b=M\,bid(T_1,K)-f,\qquad C_2^a=M\,ask(T_2,K)+f.
\]

初始 surplus 必须精确为：

\[
\boxed{s_j=C_1^b-C_2^a-\Phi_{t,T_1}(\Delta_0;S_t).}
\]

它包含两笔 option fee、multiplier、bid/ask 方向和第一 segment 内全部 underlying costs。

### 3.4 `T1` ledger 与 terminal certificate

Short early-call payoff：

\[
V_1(x)=-M(x-K)^+.
\]

按冻结的 `T1` 同 timestamp 顺序执行：early call cash settlement、第一 segment liquidation、读取
`x` 并选 `Delta_1(x)`、第二 segment 建仓、signed cash 进入 numeraire。未累计的 `T1` cash 是：

\[
C_{T_1}(x)=V_1(x)+\Delta_0x-\Phi_{T_1,T_2}(\Delta_1(x);x),
\]

并化简为：

\[
C_{T_1}(x)=
\begin{cases}
M(1-\beta_{12}+\eta)x,&0<x<K,\\
MK+M\eta x,&x\ge K.
\end{cases}
\]

实现必须独立重放未化简 ledger，并 exact-check 与 piecewise form 一致。

令

\[
F_{12}=\frac{D(t,T_1)}{D(t,T_2)},\qquad V_2(y)=M(y-K)^+.
\]

不含已存 initial surplus 的 terminal certificate payoff：

\[
g_j(x,y)=F_{12}C_{T_1}(x)+V_2(y)+\Delta_1(x)y,
\]

其 canonical piecewise form 是：

\[
g_j(x,y)=
\begin{cases}
F_{12}M(1-\beta_{12}+\eta)x+M(y-K)^+,
  &0<x<K,\\[4pt]
F_{12}(MK+M\eta x)-My,
  &x\ge K,\ 0<y<K,\\[4pt]
MK(F_{12}-1)+F_{12}M\eta x,
  &x\ge K,\ y\ge K.
\end{cases}
\]

在 `M,K,x,y>0`、`0<beta_12<=1`、`eta>0`、`F_12>=1` 下三 branch 非负；high-x/low-y
和 high-x/high-y 的下界至少为

\[
MK(F_{12}-1+F_{12}\eta)>0.
\]

Certificate 必须分别检查：

- `x -> K-` 的 left limit；
- actual `x=K` right-cell value；
- continuous boundary `y=K`；
- 所有 unbounded-cell ray slopes；
- 一个非空 open strict-gain region。

严禁用 `W_T2=s_j/D(t,T2)+g_j` 非负代替 `g_j` 非负。

### 3.5 Calendar decision boundary

每个 eligible candidate 的 `g_j` 已由结构 theorem 证明 nonnegative、nonconstant，且 strict-gain open
set 具有正 `P` 概率。因此 v4 calendar 的 candidate-specific closed boundary 是：

\[
\boxed{candidate\_is\_calendar\_arbitrage\iff s_j\ge0.}
\]

`s_j=0` 接受；`s_j<0` 拒绝，即使外加 hypothetical endowment 后的 total wealth 会非负。

所有 families 的通用 predicate 保持：

\[
(s_j>0\land g_j\ge0\ P\text{-a.s.})
\lor
(s_j=0\land g_j\ge0\ P\text{-a.s.}\land P(g_j>0)>0).
\]

## 4. Canonical arithmetic 与 oracle truth

### 4.1 Calendar operation order

Public variant 必须冻结并测试同一 binary64 operation order：

1. public DECIMAL market inputs 只 cast 一次；
2. 按曲线合同计算 `Q_q(T1,T2)`；
3. 计算 `beta_12`；
4. 计算 `Delta_0=M*(1-beta_12+eta)`；
5. 计算含 fee 的 `C_1^b`、`C_2^a`；
6. 计算第一 segment `Phi`；
7. 计算 `s_j`；
8. 计算 `F_12`；
9. 验证 unsimplified/simplified terminal certificate；
10. 应用 candidate-specific predicate。

Canonical sign/equality 使用声明算术的 exact comparison，没有 verifier tolerance。NaN 或 infinite
spot、quote、curve、fee、multiplier、surplus 或 certificate 一律 domain failure。

### 4.2 Full X/U/T oracle

Trusted oracle 从 public child 与 public variant 独立扫描：

```text
X = any enabled cross-sectional candidate is arbitrage
U = any enabled cross-asset candidate is arbitrage
T = any calendar stock-flip candidate has s_j >= 0

realized_signature     = XUT in canonical order
arbitrage_opportunity  = X or U or T
arbitrage_type         = ordered subset of
                         [cross-sectional, cross-asset, calendar]
```

Calendar bit 只能来自 public-child cashflow rescan，不能来自 mutation operator、requested signature、
private lineage 或 stored label。

Authoring-side scanner 只用于 deterministic tick selection 与 guard evidence；trusted verifier 拥有独立
final family-bit implementation。两者可共享 immutable public data structures 和 low-level primitives，
但不能共享最终 bit-decision routine。Solver 只能依 public contract 重写公式，不得 import authoring
或 verifier。

## 5. Mutation、child 与 reachability

### 5.1 保留的三类 logical operators

Fresh v4 mutation engine 保留：

```text
mutate_option_price_point_v2
mutate_call_put_pair_equal_shift_v1
mutate_underlying_spot_point_v2
```

Single quote 改一处 `mid` 并按 parent half-spreads 派生 `bid/ask`；grouped call+put 是一个 atomic
logical group、两个 physical quote points；spot operator 只改 public `spot_close`。每个 operator 最多
一个 logical group，mutation 后只检查 finite/nonnegative/side-order/tick-alignment 等 domain gates，
不能用 no-arbitrage inequality、BSM repricing 或 clipping 修复 signal。

所有 operator 都必须触发完整 X/U/T rescan。Equal call+put shift 只保持同 pair parity surplus，不能
假定保持整个 `U`。Spot mutation 只保证 `X_after==X_before`；accepted slices 另行要求 clean baseline
为 `000`。

Calendar surplus 对 mutation 的结构 response 是：

- raise early call quote -> `s_j` 增加；
- lower late call quote -> `s_j` 增加；
- early-expiry same-strike call+put equal shift -> calendar call surplus 改变，但 same-pair parity 不变；
- spot mutation -> first-segment hedge cost 和 calendar threshold 改变。

### 5.2 Public child

每个 task 从完整 `4 expiries x 7 strikes x call/put = 56` chain 抽取，稳定 key 是
`(parent_snapshot_id,parent_revision,valuation_date,underlying_id)`。不完整 slice 直接 skip，不补数、不
重定价 parent。

Materializer 按 allowlist 新建：

```text
solver_visible.underlying_daily(
  snapshot_id, date, underlying_id, spot_close
)
solver_visible.option_daily(
  snapshot_id, date, underlying_id, option_id, call_put, strike, expiry,
  exercise_style, settlement_type, contract_multiplier, bid, ask, mid
)
solver_visible.pricing_metadata(
  snapshot_id, valuation_timestamp, underlying_id, currency,
  discount_curve, risk_free_rate, dividend_curve, dividend_yield,
  borrow_or_carry_rate, calendar, day_count, pricing_dynamics,
  pricing_model, pricing_engine
)
```

Child 不含 parent DB、before values、private lineage、settlement/theoretical price、完整 OHLC、physical
DGP、seed/RNG 或 authoring metadata。Parent 永远 read-only；child 用新 identity 从 DRAFT 物化，独立
verifier 通过后才 freeze。

### 5.3 Real integer-tick reachability audit

对固定 parent/profile/grid，按顺序审计：

```text
000 100 010 001 110 101 011 111
```

每个 signature 必须记录：

```text
reachable true/false
operator and target IDs
sign
exact integer tick interval(s)
active candidate IDs and guard distances
nearest inactive candidate IDs and guard distances
domain-gate interval
deterministic unreachable diagnostic
```

Audit 必须 mutation actual option quotes/spot 并运行 full oracle；禁止 `_AffineTrigger`、抽象阈值或把
intended label 复制成 result。V4 最低 publication acceptance：

```text
000 clean control
001 calendar only
at least one mixed signature containing calendar
```

`001` 优先使用 early-expiry equal call+put shift 搜索：calendar `s_j>=0`，same-pair parity 不变，
全部 cross-asset bounds 与 cross-sectional candidates inactive，full oracle exact 返回 `001`。

其余 signature 若在固定 parent/profile/grid 上不可达，发布 proved reachable subset 并保留确定性证明；
不得因此关闭 calendar，也不得随机 retry、临时改 fee、扩大 grid 或换 parent。

Guard 是 authoring sample-selection rule，不改变 verifier truth。V4 lineage 的 active calendar candidate
必须有 non-null terminal certificate；inactive family 记录离各自 candidate-specific boundary 最近的
candidate。`calendar_terminal_guard` 不得为 `null`。

## 6. Runtime 与 artifact 工作分解

### 6.1 必须实现的模块

```text
src/synthetic_derivatives/verifier/f2a_oracle.py
  complete X/U/T scanner; calendar enumeration/evaluation; canonical bitmask

src/synthetic_derivatives/verifier/f2a.py
  submission validation; public-child load; oracle call; exact ORM comparison

src/synthetic_derivatives/authoring/f2a_child_materializer.py
  read-only parent selection; public projection; atomic mutations; authoring scan;
  active/inactive guards; freeze and private lineage

src/synthetic_derivatives/mutation/f2a.py
  immutable specs; deterministic selectors and identities; no DB writes/oracle truth

src/synthetic_derivatives/solver/f2a.py
  independent public-formula implementation; no authoring/verifier imports

scripts/materialize_f2a.py
  non-interactive materialization -> Solver -> verifier smoke orchestration
```

`f2a_contract.py` 可保留真正的 low-level primitives，但 module description 不能继续声称只表示 blocked
v2；v4 complete scanner 不能排除 calendar。

### 6.2 权限和独立性

| Boundary | Reads | Writes | Forbidden |
|:---|:---|:---|:---|
| Mutation | public identities/config | immutable spec | DuckDB writes, oracle truth |
| Authoring | frozen parent, mutation/private dataset configs | child, private lineage | overwrite parent, publish expected label |
| Solver | public child/task/variant/schemas | trajectory/submission | parent, lineage, QuantLib, authoring/verifier imports |
| Verifier | public child/task/variant/submission | private report | Solver import, stored/requested labels |
| Training | verified public records/group IDs | export/split | private lineage/diagnostics |

Solver 不得使用 QuantLib 或预制 pricing/IV/Greek/surface/arbitrage scanners。Locked environment 必须实际
安装 compatible `jsonschema`、`referencing` 和 runtime 所需依赖；clean clone 要能运行
`make install && make test`。

### 6.3 Reproducible CI parent/fixture

采用 tracked minimal F2A fixture 路线：提交能覆盖完整 calendar chain 与三类 family scan 的小型
tick-aligned frozen fixture、sidecar parent manifest、明确的 integrity metadata，以及 deterministic
materialization command。生产 authoring 仍必须显式选择 frozen F2A v2 parent；fixture 只服务 CI 和
可重放 smoke，不得 silent fallback 成生产 parent。

## 7. V4 lineage 与 output schema

### 7.1 Candidate-specific evidence

V4 废弃 v3 的 `candidate_ids + one shared guard` 结构，改为 candidate evidence array。Calendar entry
至少包含：

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
  "early_bid_amount_after_fee": 0.0,
  "late_ask_amount_after_fee": 0.0,
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

示例 `0.0` 只是 shape placeholder；实际 lineage 必须写入 canonical values。Active family 记录第一条
canonical active candidate 和 active count；inactive family 记录离其自身 boundary 最近的 candidate。

Schema 必须拒绝：terminal fields 为 `null`、calendar open boundary、`delta_1_low!=0`、
`delta_1_high!=-M`、两 call 的 strike/multiplier/underlying 不一致、`T1>=T2`、缺 fee-adjusted amounts、
accepted record 的 requested/realized signature 不一致，以及没有 verified candidate 却设置 calendar bit。

### 7.2 Public output

ORM 只包含：

```json
{
  "arbitrage_opportunity": true,
  "arbitrage_type": ["calendar"]
}
```

数组必须是 `[cross-sectional,cross-asset,calendar]` 的 ordered subset，并满足
`arbitrage_opportunity == bool(arbitrage_type)`。F2A 不输出 `maximal_spread`。Submission v4 必须允许
calendar，并拒绝错序、重复、bool/type 不一致和额外 maximal-spread 字段。

## 8. 实施阶段与强制 gates

### Phase 1 — Freeze v4 contract

- 新建 v4 identity/config/schema skeleton；历史 v1/v2/v3 不改义；
- 把 finite-date admissibility、`beta/eta/Delta_0/Delta_1/s_j/g_j`、`F_12>=1`、canonical arithmetic
  和 42-candidate enumeration 写入 public variant；
- 配置暂不单独提交为假 enabled；最终 enable 与 runtime 同分支落地。

Gate：schema 能表达完整 non-null certificate，contract/repo tests 不再保护 blocked v4 状态。

### Phase 2 — Independent calendar evaluator

- 在 trusted verifier 路径实现 candidate enumeration、unsimplified ledger 与 piecewise theorem；
- 用独立 authoring evaluator 实现相同 public contract；
- 补齐 `f2a_contract.py` 的公共 primitives 与 multiplier bucketing。

Gate：第 9.1 节 calendar algebra/negative-control tests 全部通过；不存在 finite-grid proof。

### Phase 3 — Complete X/U/T oracle

- 集成现有 X/U families 与 calendar family；
- 固定 candidate IDs、enumeration、reduction order、first-active/nearest-inactive diagnostics；
- 实现 canonical bitmask 和 ordered type list。

Gate：hand-built public fixtures 能 exact 区分 `000`、`001` 和 mixed calendar signature。

### Phase 4 — Mutation、materializer 与 lineage

- 实现纯 mutation specs 和 deterministic child IDs；
- 实现 read-only parent extraction、allowlisted projection、三类 atomic mutation；
- 每个尝试运行完整 authoring X/U/T rescan，写 candidate-specific guard/evidence；
- independent verifier 通过后 freeze child。

Gate：任何 grouped leg/domain/guard/oracle mismatch 都原子失败；public child 不泄漏 lineage/label。

### Phase 5 — Real reachability audit

- 对 actual quotes 按固定 integer tick grid 运行八 signature audit；
- 首先找到并复验 `000`、`001`、至少一个 mixed calendar signature；
- 保存 exact tick windows 和不可达证明，据结果确定 publication subset/count。

Gate：`publication_task_count>0`，calendar tests 观察到真实 oracle bit，不使用 synthetic trigger。

### Phase 6 — Solver、verifier 与 smoke

- 实现 Solver 独立 scanner/submission；
- 实现 schema validation 和 trusted exact ORM comparison；
- 实现 `scripts/materialize_f2a.py` 非交互路径；
- 加入 tracked CI fixture 与 clean-clone dependency lock。

Gate：fixture/parent -> mutation -> frozen child -> Solver -> independent verifier exact 通过，且结果含
calendar。

### Phase 7 — Enable 与文档迁移

- 最后设置 v4 `status=EXECUTABLE`、`runtime_enabled=true`、non-null family、empty blockers；
- 发布 reachability-proved tasks/smoke artifact；
- 更新 `AGENTS.md`、`README.md`、`docs/authoring_pipeline.md`、authoring/task-space/unit-test 文档；
- v2/v3 只作为 superseded historical blocked records 保留。

Gate：clean `make install && make test` 通过，active docs 不再把当前 successor 描述成 future/blocked。

## 9. 验收测试矩阵

### 9.1 Calendar algebra

必须 exact 覆盖：

1. `beta_12=B_S(T1,T2;x)/x` 与 `x` 无关；
2. `Delta_0=M*(1-beta_12+eta)` 通过 first-segment executable ask 收费；
3. `Delta_1=0` for `x<K`，`Delta_1=-M` for `x>=K`；
4. unsimplified `T1` ledger 等于 piecewise `C_T1(x)`；
5. unsimplified terminal ledger 等于 piecewise `g_j(x,y)`；
6. `x->K-` 与 actual `x=K` 都非负；
7. `y=K` 一致；
8. 所有 unbounded ray slopes 非负；
9. strict-gain open region 存在；
10. `s_j=0` 接受；
11. `s_j<0` 拒绝，即使 hypothetical endowment 能救 total wealth；
12. 每笔 option fee 和 underlying transaction cost 恰好收一次。

### 9.2 Classification 与 negative controls

Real selector/full oracle fixtures 至少覆盖：

```text
000
001
101 or 011
111, only if real reachability audit proves it
```

必须拒绝：

- raw `longer-maturity price < shorter-maturity price`；
- 只有 BSM theoretical-price mismatch；
- finite spot-grid/Monte Carlo 代替 pathwise proof；
- `W_T2>=0` 但 `g_j<0`；
- 缺 option fee、用 frictionless spot primitive、wrong `T1` boundary、double cost；
- call multipliers 不同或 `F_12<1`；
- NaN/infinite inputs 或 diagnostics。

### 9.3 End-to-end 与 reproducibility

- parent/fixture 身份必须 exact，缺失不得 fallback；
- parent read-only，child 新 identity/revision 且 verifier gate 后 frozen；
- public child 只有 allowlisted fields；
- Solver bundle 无 parent/private/mutation/hidden label；
- calendar candidate count 非零，当前完整 positive-rate chain 预期为 42；
- submission/output schema 接受 canonical calendar type；
- clean clone 可执行 `make install && make test`；
- v1/v2/v3 historical repo-contract tests 继续通过，但没有测试把 blocked v4 当成功条件。

## 10. Definition of done

- [x] Fresh v4 identity 全链一致且 v1/v2/v3 未被原地启用。
- [x] V4 `status=EXECUTABLE`、`runtime_enabled=true`、`blocking_reasons=[]`。
- [x] Calendar family 是 `transaction-cost-aware-two-expiry-call-stock-flip-v1`。
- [x] Public contract 精确包含 `beta/eta/Delta_0/Delta_1/s_j/g_j/F_12`。
- [x] 完整 chain deterministic 枚举 42 个 calendar candidates。
- [x] Trusted oracle 只从 public child 独立复算 X/U/T。
- [x] Solver 不 import authoring/verifier/prebuilt arbitrage code。
- [x] V4 lineage 存 candidate-specific non-null certificate evidence。
- [x] Real full-oracle fixture exact 实现 `001`。
- [x] `101/011/111` mixed calendar signatures 被真实 full oracle 实现；无不可达 signature。
- [x] Raw maturity ordering/model mismatch negative controls 被拒绝。
- [x] `publication_task_count=8`，且 tracked fixture 与 verified calendar smoke command 存在。
- [x] Fresh clone 有可重放 F2A fixture/parent path。
- [x] Clean `make install && make test` 通过（188 tests passed）。
- [x] Active docs 把 v4 写为 executable，把 v2/v3 写为 superseded blocked records。

最终 handoff 必须报告：全部新 IDs、实现文件、每个完整 chain 的 calendar candidate count、实际
signature reachability/tick windows、执行过的 tests 与 exact results、仍不可达 signatures，以及
calendar 已执行并启用而非只被配置声明的确认。

## 11. 明确不做与禁止捷径

V4 不做 unrestricted/global calendar LP、PDE/Monte Carlo search、margin/position-limit 扩展、
`maximal_spread`、多 logical mutation groups、完整真实交易所费用复刻或 frozen parent 修改。

以下任何结果均不验收：只改 JSON/Markdown；保留 null/false；恢复 raw maturity ordering；把 model
mismatch 称为 arbitrage；用 finite grid/Monte Carlo 作 certificate；混淆 `s_j/g_j/W_T2`；遗漏或重复
费用；从 mutation intention 复制 truth；保留 v3 组合爆炸 catalogue；因部分 signatures 不可达而关闭
calendar；或把未来更强 admissibility 要求当成 v4 blocker。
