# F2A Arbitrage-Finding Agent Task 计划

## 1. 目标

F2A 明确选用已经 materialize 并冻结的独立 parent：

```text
database             = snapshots/generated/f2a/parents/
                       DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/parent.duckdb
manifest             = 同目录 parent.manifest.json
snapshot_id          = DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2
revision / status    = 1 / FROZEN
generator_config_id  = quantlib-randomized-tdgbm-metals-f2a-parent-v2
generator / schema   = 0.8.0 / 1.6.0
```

它的 underlying 和 option minimum price increment 都是 `0.01 USD`，由
`configs/generators/quantlib_bsm_metals_f2a_parent_v2.json` 声明；authoring-time IV answer count
为 `0`。该 parent 只读，不能原地追加、同步或 mutation。

仓库默认 active development database 是另一个 `DRAFT` v3 snapshot；
`snapshots/public/quantlib_bsm_smoke_v1.duckdb` 也是 legacy public snapshot。二者都不是 F2A
parent，缺少上述 F2A 文件时必须报告，不能 silent fallback。F2A v2 parent 保留实际非零利率与
dividend/carry、原有 P/Q market model 和新 snapshot identity；它不是通过把 `r/q` 改为零来制造
方便的不等式。

每个 task 不重新生成 parent market，只做四件事：

```text
从新的 tick-aligned frozen parent 抽取一个多 strike、多 expiry 的 clean subset
  -> mutation 层生成纯 mutation spec、child identity 和 lineage
  -> authoring 层物化并冻结至多修改一个 logical quote/spot point 的 child
  -> trusted verifier 从公开 child 和 variant contract 独立复算验收结果
```

F2A 的难度来自 mutation 位置未知、需要扫描完整 subset，而不是来自同时修改很多数据。
这条支线复用项目 README 已定义的 `authoring/task_space/mutation/solver/verifier/training`
边界，不新增顶层 `arbitrage` package，也不让 `mutation` 直接写 DuckDB。

当前仓库状态必须区分为：F2A v2 parent、声明式 configs/schemas 和 repo-contract tests 已落位；
F2A child materializer、point-mutation runtime、独立 oracle、Solver/verifier、受限 Solver image、
task manifests、children 和 dataset 尚未实现或物化。已有 parent 不等于端到端 F2A 已完成。

## 2. Task 数学与交易合同

### 2.1 固定身份

第一版固定为：

```text
task_family_id       = bsm_arbitrage_finding_f2a_v1
coordinates         = (L5, P0, M0, A0, D4, R3, F2A)
parent_snapshot      = DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1 / FROZEN
pricing_model       = parent snapshot 已有的 deterministic time-inhomogeneous BSM
execution_contract  = us-options-underlying-5bps-options-flat-050-v2
candidate_catalogue = bsm-f2a-candidate-catalogue-v2
underlying_minimum_price_increment = 0.01 USD
option_minimum_price_increment     = 0.01 USD
output_contract     = arbitrage-opportunity-type-trajectory-v2
```

- Parent 始终以 read-only 打开；child 使用新的 snapshot/task identity。
- 使用 parent 已声明的 `Q`、money-market numeraire、实际 `r/q`、calendar、day-count、
  settlement 和 deterministic pricing-volatility curve。
- Solver 看不到 parent before-value、mutation 位置、private lineage、realized mutation count
  或 hidden label。
- Public F2A contract 只声明 `max_mutated_points = 1`，不能用 realized mutation count 泄露
  positive/negative label。

套利与无套利的通用定义遵循 [AGENTS.md](../../../AGENTS.md)。完整七维框架见
[financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md](../../../docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)。

### 2.2 概率测度、状态、时间与复制

套利定义在物理测度 `P` 及其 null sets 下。有限 horizon 是每个候选组合共同的 expiry `T`；
状态变量为正的 ex-dividend spot `S_u`，filtration 是由 spot Brownian driver 增广生成的 filtration，
deterministic rate、dividend/carry 和 Q-volatility curves 在 valuation time `t` 已知。任务同时声明
parent BSM pricing measure `Q = USD-MONEY-MARKET-Q-v1` 与 `P` 等价，numeraire 为
`USD-MONEY-MARKET-ACCOUNT-v1`。在 `Q` 下，当前 ex-dividend spot 满足

```text
dS_u / S_u = (r(u) - q(u)) du + sigma_Q(u) dW_u^Q.
```

当前信息集包含 valuation time 的 spot、完整 European option chain、discount/dividend curves、
deterministic `Q` volatility function、calendar、day-count、settlement 和 execution contract。
第一版不含 stochastic rate、stochastic volatility、jump、regime 或 multi-asset state。

Zero-initial-cost candidate 的 strict-positive-payoff 条件还需要公开的 P-support contract。对每个
expiry 的 ordered strikes `K_1 < ... < K_n`，第一版至少声明

```text
P(S_T < K_1) > 0,
P(K_i < S_T < K_{i+1}) > 0  for every i,
P(S_T > K_n) > 0.
```

第一版通过 `P ~ Q` 只冻结套利所需的 P-null sets：future settlement spot 在 Q 下是从 valuation
spot 出发、deterministic volatility 严格为正的 continuous GBM，因此上述 support 条件成立；P 的
risk premium 不进入 candidate truth。Parent 的 rounded-restart P path 是历史 observation DGP，
不是 future option-settlement law。不能把该离散 published chain 静默拿来替换 future P/Q process；
那会破坏当前 Girsanov-equivalence/BSM contract，并要求新的 model/variant identity。当前 public
variant 还没有把 future support 和 `P ~ Q` 明写为 task contract，因此这也是第一批 child 前的
blocking item，不能由 verifier 私下假设。

时间轴沿用 parent 的 calendar time 和 `Actual365Fixed`。Piecewise-linear instantaneous annualized
volatility 在 valuation-to-expiry 区间按 integrated variance 精确归约：

```text
sigma_eff(t, T)
  = sqrt(integral[t,T] sigma_Q(u)^2 du / (T - t)).
```

这是真正的 deterministic time-inhomogeneous GBM exact reduction，不是 endpoint volatility、
arithmetic-average volatility 或近似。

Volatility node `day_offset` 的 time origin 是 parent `start_date = 2026-08-03`，offset 使用 calendar
days，node value 单位是 annualized `1/sqrt(year)`。当前 variant 尚未显式写出 time origin/units；
在 public child 发布前必须补齐，不能让 Solver 猜测。Active catalogue 不使用 volatility repricing，
但公开的 pricing model context 本身仍必须可解释。

Parent 的 P-measure spot path 是按 business-date 观察的量化状态 Markov chain：先按实际 calendar
elapsed time 和 deterministic piecewise-linear P drift/volatility 的精确积分抽样 continuous GBM
endpoint，再以 `0.01 USD / ROUND_HALF_EVEN` 量化；该量化 close 是下一 transition 的 restart
state。它不是未经量化的 continuous-state GBM，也不把 Friday-to-Monday 当成一个 business-day
year fraction。Option 使用同日量化 close、实际曲线和 Q integrated variance 定价，再按 option
increment 量化。

策略类是冻结有限 catalogue 上的 semi-static/dynamic 策略：option 只在 valuation time 交易并持有到
cash settlement；underlying 每次成交按单边 `5 bps` 收费，cash account 保持无摩擦。允许 short
option/underlying 和使用 cash account，但只允许 catalogue 明列的有限整数 option positions 与
确定性的 underlying share schedule；catalogue 内允许按同一 curve 无上限借贷 cash，不要求
margin，初始 surplus 存入 numeraire。每个模板必须是
self-financing、terminal liquidation wealth 逐状态非负且 discounted wealth 有统一下界；禁止
doubling strategy。

非零 underlying transaction cost 意味着无摩擦 BSM delta hedge 不再是可执行的精确复制策略。
因此不能把 analytic BSM price 与 option quote 的差直接判为套利；所有启用的 candidate 都必须在
逐次扣除 underlying cost 后仍给出 admissible self-financing arbitrage。

因此本阶段的 truth 精确限定为“冻结候选模板 catalogue 中是否存在可执行套利”，不宣称穷尽该市场
所有可能的全局动态策略。

### 2.3 Bid/ask 与 transaction cost

当前 parent 是 USD、European cash-settled、contract multiplier `100` 的 synthetic option market，
因此第一版采用版本化的美国上市期权风格简化费用，而不混入 CNY/A 股规则：

```text
execution_contract_id            = us-options-underlying-5bps-options-flat-050-v2
currency                         = USD
option_contract_multiplier       = parent contract_multiplier
option_fee_per_contract_per_side = 0.50 USD
option_execution                 = directional bid/ask
option_holding                   = hold_to_cash_settlement
settlement_fee                   = 0
underlying_execution             = single_price_plus_proportional_cost
underlying_trading_cost_per_side = 0.0005 * absolute traded notional
underlying_trading_cost_scope    = every trade, including dynamic rebalancing
cash_account_transaction_cost    = 0
```

`0.50 USD/contract/side` 和 underlying 单边 `5 bps` 都只是本 synthetic task 的冻结 variant
assumption，不声称复刻任何真实交易所、broker 或 clearing fee schedule。

每个 candidate 的 observation/trading timeline 固定为：在 valuation timestamp `t` 同时观察 child
spot、curves 和 quotes；option 只能在 `t` 按 bid/ask 建仓并持有；underlying 可在 `t`、连续 dividend
再投资/融资 schedule 和 expiry `T` 交易；cash account 在相同 curve 下连续累计。Expiry `DATE`
解释为该日期与 parent valuation time 相同的 UTC clock time，cash-settled option payoff 与 terminal
underlying liquidation 使用同一个 `S_T`，不存在盘中先后顺序或额外信息。这个 timestamp rule 也
必须在第一批 child 前写入 public variant。

对价格为 `S`、signed share quantity 为 `Delta` 的一次 underlying trade，valuation-time cash outflow
固定为

```text
Delta * S + 0.0005 * abs(Delta) * S
```

所以买入按 `S * 1.0005`、卖出按 `S * 0.9995` 执行。该费用不是 bid/ask quote，也不改变
snapshot 中的 spot；它只在 candidate strategy 的 cashflow 中逐次计入。

最小报价单位、bid/ask spread 和逐合约费用是三个不同对象。frozen parent 的 generator config
是 minimum-price-increment 的市场 source of truth；公开 variant contract 必须声明 Solver
实际使用的相同单位，authoring gate 在 child 物化时只做一次跨边界一致性检查：

```text
underlying_minimum_price_increment = 0.01 USD
option_minimum_price_increment     = 0.01 USD
```

Underlying 的连续 GBM endpoint 先按 underlying increment 做 `ROUND_HALF_EVEN`，量化后的 close
成为下一步 restart state；option 必须使用该量化 spot 定价，随后 mid/bid/ask/settlement 再按
option increment 做 `ROUND_HALF_EVEN`。F2A 不对 parent quotes 进行第二次 tick projection。
Mutation config 中的 option-price 和 spot 幅度用整数 tick counts 表示，不重复声明
另一份 decimal increment；authoring 用 parent 的 increment 将 tick counts 唯一还原为价格幅度。

对第 `i` 张 option，记 multiplier 为 `M_i`、每边费用为 `f_i`。建立一张 long 或 short position
的 valuation-time 可执行现金额为：

```text
buy_cost_i      = M_i * child_ask_i + f_i
sell_proceeds_i = M_i * child_bid_i - f_i
```

费用按 `abs(position)` 线性累计。到期 cash settlement 不再收取 option 退出交易费。Cash account
无摩擦，但 underlying 明确不是无摩擦；每次初始、rebalancing 和 terminal liquidation trade 都收
`5 bps`。因此 frictionless BSM delta replication 和“quote 与 analytic BSM value 不同”都不进入
oracle。第 5 节只使用已把每次 underlying cost 纳入现金流的冻结策略。

## 3. Subset 与 solver-visible child

一个 task 的稳定选择键为：

```text
(parent_snapshot_id, parent_revision, valuation_date, underlying_id)
```

从该时刻抽取：

- 一个 underlying spot；
- 同一 valuation date 的完整 live option chain；
- 多个 strikes、call/put pairs 和多个 expiries；
- 对应的 rate、dividend/carry、calendar/day-count、currency、contract multiplier 和报价精度。

第一版只接受满足 authoring config 的完整
`4 expiries x 7 strikes x call/put = 56` 行 chain；这不是 preference。少一个 expiry、strike 或
call/put mate 都跳过，不补数据、不重定价 parent。稳定顺序是 valuation date、underlying id、
expiry、strike、call/put、option id。

Child DuckDB 是新 identity 下的最小 frozen market snapshot，保持项目现有的 market/task
边界。Materializer 必须按字段 allowlist 新建 task-specific `solver_visible` views，不能把 parent
中同名 view 做 `SELECT *` 复制，因为 parent pricing metadata 仍包含 P dynamics、seed/RNG 和
authoring canonicalization。第一版 public child 只暴露：

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

这里的 `spot_close` 是 valuation-time canonical spot，不是一个可继续模拟的完整 OHLC bar。
`settlement_price`、volume/open interest、`physical_dynamics`、generator version、seed/RNG、
input precision、authoring canonicalization 和所有 audit/metadata tables 都不进入 child。尤其不能
保留一个未 mutation 的 `settlement_price` 作为 parent theoretical/before-value 旁路。

Child 不新增第四个市场价格 `task_price`。对 option-price mutation，authoring 层先从 clean
parent 记录 half-spread，再在 child 中物化标准 `mid/bid/ask` 字段：

```text
bid_offset = parent.mid - parent.bid
ask_offset = parent.ask - parent.mid

child.mid = parent.mid + delta_ticks * option_minimum_price_increment
child.bid = child.mid - bid_offset
child.ask = child.mid + ask_offset
```

这是一个 logical quote-point mutation；`bid/ask` 是从该 point 确定性派生的市场字段。Materializer
必须验证 `0 <= bid <= mid <= ask` 且三个价格都在 `0.01 USD` tick grid 上；这里的 domain gate
只检查有限性、非负性、side ordering 和 tick alignment，不能把待检测的 no-arbitrage inequality
重新强制为真。
Private lineage 保存一个 logical target 及所有派生的 physical before/after 值，但
`realized chi_F` 仍按冻结合同计数。Child 不包含 parent database、private authoring tables、
physical path、seed/RNG、before-value、reference answer 或 private lineage。

Task convention 不混入 market snapshot。Public task manifest 只引用 child `snapshot_id/revision`、
task-space v2 rule、public variant contract 和 output contract。Actual valuation timestamp、curves
和 deterministic Q-volatility nodes 来自 child pricing metadata；Q/numeraire/rate-path identities、
节点的 time-origin/interpolation/extrapolation/units 解释、execution fee 和 candidate catalogue
算法来自 `configs/variants/bsm_arbitrage_finding_f2a_v1.json`。Variant 不能复制另一组 curve/vol
数值，child 也不能覆盖 variant 的 execution 或 algorithm contract。

## 4. 两个单点 mutation operator

### 4.1 Option price

```text
mutate_option_price_point_v1
```

纯 `mutation/f2a.py` 在一个 `(valuation_date, option_id)` 上生成 `delta_ticks` spec，不读写
DuckDB。`authoring/f2a_child_materializer.py` 在新 child snapshot 中只改变该 logical quote
point，并按第 3 节规则确定性物化 `mid/bid/ask`。Strike、expiry、option type、spot、
curves、volatility contract 和其他 option quotes 全部保持不变。

Mutation amount 从版本化 integer-tick grid 中确定性选择。Authoring-side selector 按冻结
target/order 扫描 grid，选择第一个越过目标边界且 positive spread 超过 authoring guard
band 的值，不依赖随机 retry。这个 authoring-side 计算不是最终 ORM label；trusted
verifier 使用独立实现从物化后的 public child 重算。Mutation 后只强制第 3 节的 numeric/quote
domain；不得用 clean-market BSM bounds、parity、monotonicity 或 convexity 把目标 violation clip 掉。
当前 mutation config 中的字符串 `child_option_price_in_model_domain` 语义过宽；在 runtime 实现前
必须改成或明确定义为上述 numeric/quote domain，不能保留两种解释。

### 4.2 Underlying spot

```text
mutate_underlying_spot_point_v1
```

纯 mutation 层生成一个 `(valuation_date, underlying_id, delta_ticks)` spec；authoring 层只改变
新 child 中作为 valuation-time spot 的 `spot_close`，所有 option prices、execution contract 和
pricing contract 保持不变。Child 不复制其余 OHLC/adjusted-close 字段，因此不会制造一个违反
OHLC inequality 的伪 daily bar。这是该时刻的 task-state mutation，
不生成或声称生成新的 `P`-measure underlying path。

### 4.3 正负样本

- Positive：应用上述一个 operator，且 trusted verifier 必须从 public child 复算为 `true`。
- Negative：物化同分布的 clean child，且 trusted verifier 必须复算为 `false`。
- Public schema 不暴露是否做过 mutation；private manifest 保存 target、before/after、operator、
  parent/child ids、realized `chi_F` 和预期受影响的 invariant。
- Authoring guard band 是样本选择条件，不改变第 5.1 节由 initial surplus 与 terminal payoff 共同
  定义的 canonical arbitrage predicate。

## 5. Independent oracle 与候选策略

### 5.1 Oracle 边界

Oracle 只读取 child 的 solver-visible 数据和 public variant contract，不读取 parent、预存 label、
private lineage 或 mutation intention。它使用冻结的 candidate catalogue、dtype、枚举顺序和曲线
积分合同，生成每个候选策略的 valuation-time net spread。Active catalogue 不调用 BSM repricer，
不读取 hidden theoretical price，也不使用 Q volatility 判断 quote 是否“偏离模型”；Q model 只保证
clean parent 的边际市场来源一致。

对任一 option portfolio `w`，其市场 acquisition cost 按每条腿的方向使用 ask/bid，并对
`abs(w_i)` 收费；其比较对象必须是该模板声明且在 `5 bps` underlying cost 下可执行的
funding/underlying portfolio 或非负 payoff。无摩擦 BSM dynamic replication 不进入当前
catalogue。令 `s_j` 是 candidate 在 `t` 建仓后的初始 cash surplus，`g_j` 是扣除全部到期
liquidation cashflow 后的 terminal payoff。Candidate 的 canonical decision 是

```text
candidate_is_arbitrage_j =
  (s_j > 0 and g_j >= 0 P-a.s.)
  or
  (s_j == 0 and g_j >= 0 P-a.s. and P(g_j > 0) > 0)
```

`s_j < 0` 需要正初始 endowment，不是套利；`s_j == 0` 不能一律判 false。第 5.2 节的 bounds、
monotonicity 和 convexity payoff 逐状态非负；在第 2.2 节冻结的 P-support contract 下，它们不恒为
零且以正概率严格为正，所以在 `s_j == 0` 时仍是套利。Put-call parity payoff 恒为零，只有
`s_j > 0` 才是套利。Verifier 对 canonical binary64 `s_j` 使用 exact sign/equality，不使用
tolerance；authoring guard band 只负责拒绝接近零的生成样本，不能改写 canonical rule。

当前 variant 和 private authoring config 中的
`candidate_spread_strictly_greater_than_zero` 因而过时，必须在第一批 child 前与 candidate catalogue
decision contract 一起更新。若继续把 `candidate_spread` 作为字段名，必须明确它只是 initial
surplus，不是完整的 arbitrage predicate。

### 5.2 Cross-sectional 与 cross-asset catalogue

同一 valuation time、currency、expiry、exercise/settlement、contract multiplier bucket 内检查：

1. `cross-asset`：European discounted lower/upper bounds；
2. `cross-asset`：同 strike 的 executable put-call parity；
3. `cross-sectional`：strike monotonicity；
4. `cross-sectional`：非等距 strike convexity。

所有 inequalities 都使用可执行方向：long option 用 child `ask`，short option 用 child `bid`，
每条 option leg 按 `abs(position)` 计费。Candidate spread 的单位是 valuation-time USD；正 spread
作为初始 surplus 存入 cash account。

#### 5.2.1 含 underlying cost 的 terminal-spot bid/ask

Cross-asset candidate 不能直接代入 frictionless `S exp(-qT)`。令 `kappa = 0.0005`，
`Q_q(t,T) = integral[t,T] q(u) du`。F2A v1 把 child `dividend_curve` 明确解释为 deterministic、
non-negative、continuous proportional cash-distribution yield；若将来 `q` 只是 borrow/carry quote、
为负或具有随机状态，以下策略失效，必须换 catalogue identity。

构造一单位 terminal cash exposure `S_T` 的 executable ask：在 `t` 买入

```text
n_ask(t) = exp(-Q_q(t,T) / (1 + kappa)) / (1 - kappa)
```

股 underlying；之后把连续 dividend cashflow 全部按 buy price `S_u(1+kappa)` 再投资，到 `T`
持有 `1/(1-kappa)` 股并按 sell price `S_T(1-kappa)` liquidation。对应 valuation-time cost 为

```text
A_S(t,T) = S_t * (1 + kappa) / (1 - kappa)
             * exp(-Q_q(t,T) / (1 + kappa)).
```

同理，一单位 terminal liability `-S_T` 的 executable bid 通过初始 short

```text
n_bid(t) = exp(-Q_q(t,T) / (1 - kappa)) / (1 + kappa)
```

股、用新增 short-sale proceeds 支付连续 dividend liability、到期按 ask cover `1/(1+kappa)` 股实现。
其 valuation-time proceeds 为

```text
B_S(t,T) = S_t * (1 - kappa) / (1 + kappa)
             * exp(-Q_q(t,T) / (1 - kappa)).
```

这两个 finite-variation share schedules 把 initial、dividend-rebalancing 和 terminal trade 的成本都
计入；它们不是无摩擦 delta hedge。Authoring/verifier tests 必须逐 cashflow 证明 self-financing、
terminal exposure 和 admissibility，不能只测试最终公式数值接近。

#### 5.2.2 Cross-asset exact spreads

令 `D(t,T)` 是 child discount curve 按 `Actual365Fixed` 得到的 discount factor。对同 strike 的一张
call/put，令 `M` 为 multiplier、`f = 0.50 USD`，并定义：

```text
C^a = M * call.ask + f       C^b = M * call.bid - f
P^a = M * put.ask  + f       P^b = M * put.bid  - f
U^a = M * A_S(t,T)           U^b = M * B_S(t,T)
H   = M * K * D(t,T)
```

`discounted_price_bounds` 按固定顺序枚举以下四个 statewise non-negative terminal portfolios：

```text
call upper:  short call + long terminal spot              s = C^b - U^a
call lower:  long call  + short terminal spot + K bond    s = U^b - H - C^a
put upper:   short put  + K bond                           s = P^b - H
put lower:   long put   + long terminal spot - K bond     s = H - P^a - U^a
```

对应 terminal payoffs 依次为 `M min(S_T,K)`、`M max(K-S_T,0)`、
`M min(S_T,K)`、`M max(S_T-K,0)`。它们非负且在 parent P-support 上不恒为零，所以 `s >= 0`
给出套利，`s < 0` 不给出零初始 endowment 的套利。

`executable_put_call_parity` 再按固定方向枚举：

```text
short option forward + long underlying forward:
  s = C^b - P^a - U^a + H

short underlying forward + long option forward:
  s = U^b - H - C^a + P^b
```

两者 terminal payoff 都恒为零，所以必须 `s > 0` 才是套利；所有 bid/ask、两条 option fees、
underlying cost、funding 与 dividend reinvestment 已包含在上述 cash amounts 中。

#### 5.2.3 Cross-sectional exact spreads

对任一 option quote `X_i` 定义 `X_i^a = M * ask_i + f`、
`X_i^b = M * bid_i - f`。对每个 `K_1 < K_2` 的有序 strike pair：

```text
call monotonicity:  long C(K_1), short C(K_2)   s = C_2^b - C_1^a
put monotonicity:   short P(K_1), long P(K_2)   s = P_1^b - P_2^a
```

对每个 `K_1 < K_2 < K_3` 的有序 strike triple，把三个 exact DECIMAL strike gaps 先放大为整数，
再除以共同 gcd，得到最小正整数：

```text
a : b : c = (K_3-K_2) : (K_3-K_1) : (K_2-K_1).
```

Call 和 put convexity 都使用 `+a` 张 `K_1`、`-b` 张 `K_2`、`+c` 张 `K_3`，spread 为

```text
s = b * X_2^b - a * X_1^a - c * X_3^a.
```

Monotonicity 和 convexity terminal payoff 都逐状态非负且在 parent P-support 上不恒为零，所以
`s >= 0` 是套利。禁止只枚举等距/相邻 strikes、使用 fractional weights，或在 verifier 中临时
优化 position ratios。

上述 family/order/formulas、pair/triple order、curve integration、IEEE-754 binary64 cast point 和
逐项 reduction order 必须在第一批 child 生成前补进 public variant contract。当前 config 只列出
family names 和 `dtype=float64`，仍不足以定义 canonical oracle；在这部分 versioned contract 与
独立 cashflow tests 落位前不得物化 F2A children。因为尚无 F2A child/dataset 发布，可以完成当前
draft contract；发布后再改任何公式或 operation order 都必须换 candidate/variant identity。

### 5.3 Calendar catalogue

第 5 类不再笼统地把“逐 option 与理论价不一致”路由成 calendar。单个 option 与同到期日复制
成本的比较不跨 cashflow dates，不能仅因 scanner 同时读取多个 expiries 就称为 calendar。

原先的相邻 expiry option portfolio 与无摩擦 BSM 联合动态复制策略，在 underlying 每次交易收取
`5 bps` 后不再是精确的 self-financing replication，因此从当前 candidate catalogue 移除。
`calendar` 保留为 versioned output enum，但 F2A v1 config 将 `calendar_family` 设为 `null`；在冻结
transaction-cost-aware super/subhedging strategy、trading grid/state space 和证明之前，authoring
不得生成 `calendar` positive child。

仍然禁止使用“同 strike 的长期限 option raw price 必须更高”这条错误捷径。未来启用 calendar
family 时，必须使用 snapshot 的实际 `r/q`、calendar/day-count、cashflow dates 和每次 underlying
交易的 `5 bps` 成本，并证明候选策略在声明状态空间上是 admissible、self-financing arbitrage。

### 5.4 Canonical truth

最终 truth 为：

```text
arbitrage_opportunity = any(candidate_is_arbitrage)
arbitrage_type = ordered union of types whose candidate_is_arbitrage is true
```

第 1--2 类路由为 `cross-asset`，第 3--4 类路由为 `cross-sectional`；未来启用后的跨期限候选路由为
`calendar`。Schema 保留固定顺序 `cross-sectional`、`cross-asset`、`calendar` 的完整 enum，
但当前 `calendar_family = null`，所以 F2A v1 的 reachable canonical values 只有：

```text
false -> []
true  -> ["cross-sectional"]
      | ["cross-asset"]
      | ["cross-sectional", "cross-asset"]
```

Verifier 必须保证 `arbitrage_opportunity == bool(arbitrage_type)`。数组表示 AND/OR，不接受
顺序不定的 set、重复值或自由文本；类型也不得从 private mutation intention 复制。当前 child
提交任何含 `calendar` 的 answer 都必须失败，即使 trajectory 文本声称发现 calendar relation。

## 6. LLM 输出与 ORM

LLM 仍需输出完整 trajectory：

```text
Problem
Context
Assumptions
Skills
Evidence
Intermediate Reasoning
Verification
Confidence
Outcome
```

但 ORM 只投影并验收：

```json
{
  "arbitrage_opportunity": true,
  "arbitrage_type": ["cross-sectional", "cross-asset"]
}
```

`arbitrage_opportunity` 与 `arbitrage_type` 都必须 exact-match hidden verifier 从 public child 重算的
canonical 值。无套利答案为
`{"arbitrage_opportunity": false, "arbitrage_type": []}`。F2A 不生成、不提交、也不验收
`maximal_spread`；该字段从 F2B 开始。

## 7. 代码与 artifact 结构

### 7.1 配置与 schema

F2A 不增加新的顶层 config 类别。兼容设计只使用项目 README 已定义的
`generators/task_space/mutations/curricula/variants` 与 `authoring/configs` 边界：

```text
authoring/configs/
└── f2a_dataset_v1.json
    # private selector、authoring guard、label balance 和 smoke/pilot 规模

configs/generators/
└── quantlib_bsm_metals_f2a_parent_v2.json
    # 已物化 frozen parent 的 0.01 USD increments、identity 和完整 parent DGP

configs/variants/
└── bsm_arbitrage_finding_f2a_v1.json
    # public market/method/execution/candidate/output contract

configs/mutations/
└── f2a_point_v1.json
    # logical operator ids、integer-tick grids 和稳定枚举顺序

configs/task_space/
└── derivatives_v2.json
    # v2 七维 registry；v1 保持不变

configs/curricula/
└── adaptive_v2.json
    # 旧 stages 显式路由到 F0，新增 F2A stage

schemas/
├── difficulty-v2.schema.json
├── task-v2.schema.json
├── mutation-v2.schema.json
├── curriculum-v2.schema.json
├── f2a-lineage.schema.json
├── trajectory.schema.json
└── submission.schema.json
```

上述 generator/variant/mutation/private-authoring configs、并行 v2 registry/curriculum 和 schemas
已经按这些路径落位；`configs/arbitrage/` 已移除。F2A v2 parent 也已 materialize/freeze，但这些
declarative files 只锁定了目录责任和部分 contract：public variant 仍须补齐第 5.2 节的 exact
candidate formulas/order，Python v2 dispatch 和 F2A runtime 也不存在。不得据此声称 child snapshot
或 dataset 已经 materialize。

不原地改写现有 `difficulty.schema.json`、`task.schema.json`、
`configs/task_space/derivatives_v1.json` 或六维 task manifests。旧 v1 task 仍按原六字段
表示和原 identity 重放；F2A 使用 v2 schema/registry 与 `F = "F2A"`。V2 ordinary rules
显式使用 `F0`，并新增精确的 `bsm_arbitrage_finding_f2a_v1` rule。这是并行版本，
不是对 v1 manifest 的隐式迁移。

当前 Python 实现把 `AXES`、registry selector、mutation direction 和 curriculum selector 都
固定为六个整数轴。V2 接入必须一次性版本化扩展 `task_space`、`mutation`
和 `curriculum`，使 string enum F 只在 v2 路径中解析。F 轴转移使用 dedicated
`direction = any` operator，不对 `F0/F2A/...` 做数值大小比较。

配置 source of truth 按边界拆分：

```text
generator config:
  parent DGP、minimum price increments、rounding、generator/snapshot identity

public variant config:
  P-null-set/Q-equivalence、numeraire/rate-path、timeline/dividend-cashflow contract
  execution cost、candidate catalogue/predicate、active type routing、operation order
  output contract 和 Solver 权限

mutation config:
  logical operator ids、integer tick counts、target 与 enumeration order

private authoring config:
  parent selectors、authoring guard band、positive/negative balance、split 与数据集规模
```

这些 config 可以相互引用 immutable ID，但不得复制 hidden answer。Public variant 不存
mutation target/order/before-value；private authoring config 不成为 Solver bundle 的一部分。

### 7.2 计划中的 Python 模块

不创建 `src/synthetic_derivatives/arbitrage/`。F2A runtime 实现阶段按项目已有 package 边界分工：

```text
src/synthetic_derivatives/authoring/
└── f2a_child_materializer.py

src/synthetic_derivatives/mutation/
├── engine.py
└── f2a.py

src/synthetic_derivatives/solver/
└── f2a.py

src/synthetic_derivatives/verifier/
├── __init__.py
├── f2a_oracle.py
└── f2a.py

src/synthetic_derivatives/training/
└── f2a.py

scripts/
└── materialize_f2a.py
```

职责边界：

- `authoring/f2a_child_materializer.py`：以 read-only 方式打开 frozen parent，校验 public
  mutation spec，按第 3 节 allowlist 生成新 DRAFT child，物化 market fields、运行 authoring
  gates、写 private lineage，最后以新 identity/revision 冻结。它不重新运行 QuantLib repricing，
  也不把 authoring-side expected label 当成 ORM truth。
- `mutation/f2a.py`：定义 immutable point-mutation spec、stable selector、operator ID、integer
  tick count、child identity input 和 lineage record。它是纯逻辑，不读写 DuckDB、不导入
  QuantLib、不运行 oracle。
- `solver/f2a.py`：只读公开 child 与 public variant contract，在 allowlist-only Solver image 内
  手工枚举 catalogue 并产生完整 trajectory/submission；不导入 authoring/verifier，也不调用
  QuantLib、py_vollib、mibian、rateslib 或其他预制 option pricing/IV/Greek/surface/arbitrage API。
- `verifier/f2a_oracle.py`：只从 public child 和 public variant contract 重算第 5 节的 candidate
  spreads 与 canonical truth；不读 parent、private lineage、mutation intention 或 authoring-side
  expected result，不导入 Solver 实现。
- `verifier/f2a.py`：验证 trajectory/submission schema，只投影 `Outcome.orm_answer`，调用
  verifier-owned oracle 并 exact-compare；它不把 hidden diagnostics 暴露给 Solver。
- `training/f2a.py`：只把已通过验证的 public task、trajectory、outcome 与 snapshot grouping
  转换为训练记录；不打包 private lineage 或 hidden diagnostics。
- `scripts/materialize_f2a.py`：作为统一非交互入口编排 config 加载、mutation spec、authoring
  child 物化、verifier gate 和 manifest 写出；业务数学仍留在各 package。
- 现有 `mutation/engine.py` 继续处理 task-coordinate compatibility 与 deterministic identity。V2
  路径能识别 F enum，但 executable market point 的 DuckDB 物化不塞入该 engine。

Authoring-side candidate selection 与 verifier 不共用一份 oracle implementation。前者只用于在冻结
grid 中选择远离边界的 child；后者才是 ORM 的独立 canonical acceptance 路径。

### 7.3 Public 与 private artifacts

```text
snapshots/generated/f2a/parents/<parent_snapshot_id>/parent.duckdb
snapshots/generated/f2a/parents/<parent_snapshot_id>/parent.manifest.json
snapshots/generated/f2a/children/<child_snapshot_id>/child.duckdb
snapshots/generated/f2a/children/<child_snapshot_id>/child.manifest.json

datasets/manifests/tasks/f2a/<task_id>.json
datasets/manifests/splits/f2a_v1.json

snapshots/private/f2a/<task_id>/lineage.json

datasets/generated/f2a/<dataset_id>.jsonl
```

`snapshots/generated/` 存放可重建的 solver-visible child snapshot；每个 child 必须有新
`snapshot_id`、明确 revision、`FROZEN` status 和 sidecar manifest。`datasets/manifests/tasks/`
只保存应提交的 public task identity、child snapshot id/revision、variant/output contract IDs、
task-space registry/rule ID 和 split grouping，不复制 DuckDB 或 oracle answer。

`snapshots/private/` 属于 authoring/verifier 私有且被 `.gitignore` 排除的 material；不得把其
实例放进应提交的 `datasets/manifests/`。Private lineage 至少保存：

```text
parent_task_id / child_task_id
parent_snapshot_id / child_snapshot_id / revisions
stable selector
operator / seed / engine id
table / row key / field / before / after
realized chi_F
source pricing-model ids
execution-contract id
candidate-catalogue id
oracle result and authoring guard evidence
```

`authoring_guard_evidence` 必须包含 exact violated invariant 和 candidate id；positive lineage 不能只写
宽泛的 mutation intention。若把它提升为独立字段，必须先 version schema，不能向当前
`additionalProperties: false` 的 lineage object 私自加字段。

`datasets/generated/` 只存最终可重建的训练 JSONL/Parquet，不存 child DuckDB 或 private
lineage。Public task id 和 child snapshot id 不编码 operator、target、`arbitrage_type` 或
positive/negative status。Solver bundle 只挂载 public child、public task manifest、public variant contract 和
trajectory/submission schemas。

### 7.4 权限与数据流

Solver 的权威 environment/import/API allowlist 见
[`environments/solver/README.md`](../../../environments/solver/README.md)。当前 public variant 与
`requirements.lock` 的实际第三方 distribution intersection 只有 `duckdb==1.5.5`，且 DuckDB
connection 只能由尚未实现的 trusted query adapter 持有；Solver-authored candidate enumeration
使用 allowlisted Python standard library。README 中列出的 NumPy/Pandas 是尚未进入 lock/variant
的目标，当前不能声称可用。若后续确需启用，必须先同步 versioned variant、完整 transitive lock、
import/API audit 和 sandbox tests；这仍不授权任何预制 option pricing/IV/Greek/surface/arbitrage API。

目前 trusted adapter、runtime import/capability gate 和 sandbox acceptance 都未实现，所以仓库根
`.venv` 不能充当 Solver image，也不能把现有 repo-contract test 当作权限隔离已经通过的证据。

| 边界 | 可读 | 可写 | 禁止 |
|:---|:---|:---|:---|
| Authoring | frozen parent、generator/mutation/private dataset configs | new child snapshot、private lineage、authoring gates | 覆盖 parent；把 expected label 写入 public child |
| Mutation | public identities 与 versioned mutation config | immutable spec 与 lineage record | 读写 DuckDB；运行 oracle |
| Solver | public child、task/variant contract、public schemas；allowlist-only dependencies | trajectory 和 submission | 任意预制 option pricing/IV/Greek/surface/arbitrage capability、private lineage、hidden tests/verifier |
| Trusted verifier | public child、task/variant contract、submission、hidden tests | verification report 和 private diagnostics | 导入 Solver 实现；信任 stored label |
| Training | verified public task/trajectory/outcome 和 grouping IDs | JSONL/Parquet 与 split manifest | 导出 private lineage、hidden diagnostics 或 oracle traces |

## 8. 实施顺序

已完成的 foundation 是：独立 v2 generator config、`0.01 USD` tick-aligned parent 的新 identity
materialization/freeze、declarative v2 registry/curriculum/schemas，以及 variant/mutation/dataset
config skeleton。不要重做或 mutation 该 parent。

剩余工作按以下顺序进行：

1. 先把第 2、3、5 节的 future P-law/support、Q-volatility time origin/units、
   observation/settlement timeline、public child schema、continuous-dividend semantics、terminal-spot
   bid/ask、candidate formulas、
   enumeration/reduction order、mutation numeric domain 和 ORM routing 写入 versioned public
   contracts；为每个 template 给出 self-financing/admissibility proof test。此 gate 未通过前停止。
2. 保留六维 task-space/schema/config v1 不变，并行增加七维 v2 runtime dispatch、F enum、F2A
   compatibility rule 和 curriculum v2。先证明旧 manifests、serialization 和 deterministic child IDs
   完全不变。
3. 在 `verifier/` 用手工小市场实现独立 F2A oracle，覆盖第 5.2 节全部 cross-sectional/cross-asset
   exact spreads、option fee、underlying `5 bps`、integer positions、严格零边界与 canonical type
   order。Calendar candidate 不实现。
4. 实现纯 `mutation/f2a.py` spec/identity/lineage，再在 `authoring/f2a_child_materializer.py` 实现
   parent selection、allowlisted market projection、两个 logical point operators、authoring-side boundary search、
   child freeze 与 private lineage。两层不共享 DB write 权限。
5. 实现 Solver 任务入口与 trajectory/submission verifier；trusted verifier 只从 public child 和
   public variant contract 重算 bool 与 canonical type array。
6. 实现 trusted DuckDB adapter、实际 dependency lock、import/API gate 和 sandbox acceptance；在此
   之前不发布 Solver image。
7. 实现 `scripts/materialize_f2a.py`、public task/split manifests 和 training export，确认 private lineage
   不进入 Solver bundle 或训练数据。
8. 先 materialize 小型 smoke set，正负样本平衡，option-price/spot operator 均有覆盖；通过
   public、unit、integration 与 verifier-robustness gates 后再扩容。每个 positive 仍只有一个
   logical mutation point，它可以确定性派生多个标准 market fields。

## 9. 最小验收测试

### Unit

- V1 six-axis schema/registry/manifests 继续原样 parse/serialize，旧 deterministic child IDs 不变；v2
  F enum/schema/registry/curriculum round-trip。
- Pure mutation spec 同 selector/operator/seed/tick count 完全相等，换任一 identity input 即不同；
  `mutation/f2a.py` 不打开 DuckDB、不导入 QuantLib/verifier。
- Option operator 只改变一个 logical quote point，child `mid/bid/ask` 从 parent half-spread 与 tick
  delta 唯一物化；spot operator 只改变一个 child `spot_close`。
- Public child 严格匹配第 3 节列 allowlist，不含 `settlement_price`、full OHLC、physical dynamics、
  seed/RNG、before-value 或 authoring/audit tables；parent view 新增字段也不能自动进入 child。
- Parent generator increment 与 public variant 声明在 authoring boundary 检查一次；mutation 幅度使用
  integer tick counts，不存另一份 decimal increment source。
- Frozen parent 的 published underlying fields 是 underlying increment 的整数倍，量化 close 是 restart
  state；option 从 rounded spot 定价。Child `spot_close/bid/ask/mid` 分别落在对应 tick grid。
- 每条 option leg 按 `abs(position)`、multiplier 和 `0.50 USD/contract/side` 计费；每条
  underlying trade 按 `0.0005 * abs(traded_notional)` 计费；initial surplus 恰好为零时，parity
  payoff 判 `false`，非恒零的 bounds/monotonicity/convexity payoff 判 `true`，不使用 tolerance。
- 从逐时 cashflow 独立验证 `A_S/B_S` 的 initial shares、dividend reinvestment/financing、terminal
  liquidation 和 self-financing；用正 dividend curve，不把 borrow/carry quote 偷换成 cash dividend。
- Cross-asset bounds/parity 使用第 5.2 节 executable sides、`A_S/B_S` 和实际 discount curve；
  cross-sectional 对全部 strike pairs/triples 使用 executable sides、费用和最小整数 ratios。
- 当前 catalogue 不生成 calendar truth；测试必须拒绝把 raw same-strike maturity comparison 或
  frictionless BSM dynamic replication 当作 transaction-cost market 的套利证明。

### Integration

- 精确选择 F2A v2 parent path，并核对 manifest/DB identity 为
  `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1 / FROZEN`；缺失时失败，不 fallback 到
  active v3 或 legacy public snapshot。
- Parent 始终 read-only；child 从 DRAFT 物化后以新 identity/revision 冻结；同一完整输入
  可 byte-identical replay。
- Public task manifest 只引用 child snapshot id/revision、registry/rule 与 public contract IDs，不复制
  parent、oracle、before/after 或 reference answer。
- Solver child 不含 parent database、private authoring tables、physical DGP、seed/RNG、mutation location、
  realized mutation count、hidden label 或 private lineage。
- Private lineage 只写入 ignored private artifact path；删除或篡改它不改变 verifier 从 public child
  得到的 truth。
- Clean child 验证为 `false/[]`，option-price 和 spot operators 均能稳定生成远离边界的
  positive child。

### Public 与 verifier robustness

- Solver bundle 只包含 public child、task/variant contract 和 trajectory/submission schemas；公开 smoke 能完成
  child -> submission -> verifier 端到端路径。
- ORM 接受完整 trajectory 中的 bool 与 canonical `arbitrage_type`，拒绝缺失/错序/错分的 type
  array、当前不可达的 `calendar`、与 bool 不一致的 type array、缺失 trajectory 或额外的
  `maximal_spread`。
- 保持 ORM answer 不变而改写 trajectory 文本不改变 reward；翻转 bool、删除/交换 type、改单位、
  contract ID 或 child snapshot revision 均必须失败。
- 现有 authoring/public/task-space/mutation/curriculum tests 全部继续通过。

## 10. F2A 暂不做

- 多点 mutation；
- `maximal_spread` 或最优套利组合；
- 完整复刻某一真实交易所的 maker/taker、broker、clearing、regulatory fee/rebate schedule；
- transaction-cost-aware calendar super/subhedging strategy；
- bid/ask size、partial fill、market impact、margin、borrow availability 或 position limit；
- 多 pricing-model snapshot；
- LP/PDE/Monte Carlo arbitrage search；
- 修改现有 frozen parent。
