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
  -> authoring 层按一个原子 mutation group 物化 child；group 可改变一个 quote、一个 spot，
     或同 strike/expiry 的 call+put 两个 quote points
  -> trusted verifier 从公开 child 和 variant contract 独立复算验收结果
```

F2A 的难度来自 mutation 位置未知、需要扫描完整 subset，而不是来自同时修改很多数据。
这条支线复用项目 README 已定义的 `authoring/task_space/mutation/solver/verifier/training`
边界，不新增顶层 `arbitrage` package，也不让 `mutation` 直接写 DuckDB。

当前仓库状态必须区分为：F2A v2 parent、声明式 configs/schemas 和 repo-contract tests 已落位；
F2A child materializer、point-mutation runtime、独立 oracle、transaction-cost-aware calendar
certificate、type-signature selector、Solver/verifier、受限 Solver image、task manifests、children
和 dataset 尚未实现或物化。已有 parent 不等于端到端 F2A 已完成。

## 2. Task 数学与交易合同

### 2.1 固定身份

身份分三条并行线，均不能原地改义：

```text
legacy replay:
  variant v1 / catalogue v2 / mutation v1 / dataset v1 / lineage/submission v1

blocked first-successor review record:
  variant v2 / catalogue v3 / mutation v2 / dataset v2 / lineage/submission v2

blocked complete-grammar successor:
  variant             = bsm-arbitrage-finding-f2a-v3
  candidate catalogue = bsm-f2a-candidate-catalogue-v4
  mutation engine     = f2a-complete-mutation-v3
  dataset             = f2a-dataset-v3
  lineage/submission  = v3
  execution contract  = us-options-underlying-5bps-options-flat-050-v3
  output contract     = arbitrage-opportunity-type-trajectory-v4
```

Legacy `bsm-arbitrage-finding-f2a-v1` / catalogue v2 / mutation engine v1 / dataset v1 / lineage v1
继续按旧 `candidate_spread_strictly_greater_than_zero` skeleton 重放，不能原地迁移。V2 只保留为首次
successor 审阅记录。V3 首次完整声明 single quote、equal call+put group 和 spot 三种 operator；它仍
保持 `calendar_family = null`、`runtime_enabled = false`，不是 executable successor。将来 calendar
proof、reachability 与 runtime 完成时仍须再分配新的 executable identity，不能打开 v2 或 v3。

- Parent 始终以 read-only 打开；child 使用新的 snapshot/task identity。
- 使用 parent 已声明的 `Q`、money-market numeraire、实际 `r/q`、calendar、day-count、
  settlement 和 deterministic pricing-volatility curve。
- Solver 看不到 parent before-value、mutation 位置、private lineage、realized mutation count
  或 hidden label。
- Complete grammar 的 public difficulty 明确声明最多一个 logical mutation group、最多两个 logical/
  physical option quote points，或一个 spot point。Grouped pair 不能伪装成普通 single-point mutation；
  public identity 也不能用 realized count 泄露 positive/negative label。

套利与无套利的通用定义遵循 [AGENTS.md](../../../AGENTS.md)。完整七维框架见
[financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md](../../../docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)。

### 2.2 概率测度、状态、时间与复制

套利定义在物理测度 `P` 及其 null sets 下。单期限 cross-sectional/cross-asset candidate 的 horizon
是共同 expiry `T`；calendar candidate 的 horizon 是较晚 expiry `T_2`，较早 expiry `T_1` 是公开的
中间 settlement/rebalancing time；
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
那会破坏当前 Girsanov-equivalence/BSM contract，并要求新的 model/variant identity。Blocked
successor variant v2 已把 future support 和 `P ~ Q` 明写为 public task contract；legacy variant
不获得该隐式假设。Successor 仍因 calendar proofs、reachability 与 runtime 而 blocked。

时间轴沿用 parent 的 calendar time 和 `Actual365Fixed`。Piecewise-linear instantaneous annualized
volatility 在 valuation-to-expiry 区间按 integrated variance 精确归约：

```text
sigma_eff(t, T)
  = sqrt(integral[t,T] sigma_Q(u)^2 du / (T - t)).
```

这是真正的 deterministic time-inhomogeneous GBM exact reduction，不是 endpoint volatility、
arithmetic-average volatility 或近似。

Volatility node `day_offset` 的 time origin 是 `2026-08-03T16:00:00Z`，offset 使用 calendar days，
node value 单位是 annualized `1/sqrt(year)`；blocked successor variant v2 已显式冻结这些字段。
Legacy variant 不获得隐式默认值。Active catalogue 不使用 volatility repricing，但公开的 pricing
model context 本身仍必须可解释。

Parent 的 P-measure spot path 是按 business-date 观察的量化状态 Markov chain：先按实际 calendar
elapsed time 和 deterministic piecewise-linear P drift/volatility 的精确积分抽样 continuous GBM
endpoint，再以 `0.01 USD / ROUND_HALF_EVEN` 量化；该量化 close 是下一 transition 的 restart
state。它不是未经量化的 continuous-state GBM，也不把 Friday-to-Monday 当成一个 business-day
year fraction。Option 使用同日量化 close、实际曲线和 Q integrated variance 定价，再按 option
increment 量化。

策略类是冻结有限 catalogue 上的 semi-static/dynamic 策略：option 只在 valuation time 交易并持有到
cash settlement；underlying 每次成交按单边 `5 bps` 收费，cash account 保持无摩擦。允许 short
option/underlying 和使用 cash account，但只允许 catalogue 明列的有限整数 option positions 与
公开、有限、可重放的 predictable underlying rules；单期限模板使用确定性 share schedule，calendar
模板只允许第 5.3 节冻结的一个 `T_1` state-contingent Borel rule。Catalogue 内允许按同一 curve
无上限借贷 cash，不要求 margin，初始 surplus 存入 numeraire。每个模板必须是
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
execution_contract_id            = us-options-underlying-5bps-options-flat-050-v3
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
cash_account_balance             = signed; same numeraire curve for borrowing/lending
stock_borrow_availability        = unlimited within catalogue position bounds
incremental_stock_borrow_fee     = 0
short_sale_proceeds_usage        = credited to the declared signed cash account
```

V3 的 option/underlying 数值现金流与 v2 相同，但 stock-borrow 与 signed-cash 语义首次成为 normative
fields，故使用新的 execution identity；不能把 `r-q` carry 解释成额外 stock-loan fee。
`0.50 USD/contract/side` 和 underlying 单边 `5 bps` 都只是本 synthetic task 的冻结 variant
assumption，不声称复刻任何真实交易所、broker 或 clearing fee schedule。

每个 candidate 的 observation/trading timeline 固定为：在 valuation timestamp `t` 同时观察 child
spot、curves 和 quotes；option 只能在 `t` 按 bid/ask 建仓并持有。单期限模板的 underlying 可在
`t`、连续 dividend 再投资/融资 schedule 和 expiry `T` 交易；calendar 模板额外允许在较早 expiry
`T_1` 观察 settlement spot 后执行第 5.3 节唯一一次 state-contingent rebalance，再持有至 `T_2`。
Cash account 在相同 curve 下连续累计。Expiry `DATE` 解释为该日期与 parent valuation time 相同的
UTC clock time，cash-settled option payoff 与同日期 underlying liquidation/re-entry 使用同一个
`S_T`，不存在盘中先后顺序或额外信息。Blocked successor variant v2 已把 valuation/expiry
`16:00:00Z` 与 `T1` 同 timestamp operation order 写入 public contract。

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
算法来源必须按身份分流：legacy replay 读取
`configs/variants/bsm_arbitrage_finding_f2a_v1.json`；首次 blocked review 读取
`configs/variants/bsm_arbitrage_finding_f2a_v2.json`；完整 grammar review 读取
`configs/variants/bsm_arbitrage_finding_f2a_v3.json`。V3 仍不可执行。Variant 不能复制另一组 curve/vol
数值，child 也不能覆盖 variant 的 execution 或 algorithm contract。

## 4. 三类 mutation operator 与完整 grammar

### 4.1 Option price

```text
mutate_option_price_point_v2
```

纯 `mutation/f2a.py` 在一个 `(valuation_date, option_id)` 上生成 `delta_ticks` spec，不读写
DuckDB。`authoring/f2a_child_materializer.py` 在新 child snapshot 中只改变该 logical quote
point，并按第 3 节规则确定性物化 `mid/bid/ask`。Strike、expiry、option type、spot、
curves、volatility contract 和其他 option quotes 全部保持不变。

Mutation amount 从版本化 integer-tick grid 中确定性选择。Authoring-side selector 按冻结 order 扫描
grid，并对每个 point 调 candidate-specific exact cashflow evaluator；选择 realized signature exact match
且 active/inactive setup/terminal guard vector 全部通过的第一个值，不依赖随机 retry。这个计算不是最终
ORM label；trusted verifier 使用独立实现从物化后的 public child 重算。Mutation 后只强制第 3 节的
numeric/quote domain；不得用 clean-market BSM bounds、parity、monotonicity 或 convexity 把 signal
clip 掉。Successor mutation v2 已把 gates 逐项冻结；legacy v1 的
`child_option_price_in_model_domain` 不得解释成 successor 的 BSM/model-price gate。

### 4.2 Parity-preserving equal call+put group

```text
mutate_call_put_pair_equal_shift_v1
```

该 operator 原子选择同 valuation time、underlying、expiry、strike 与 multiplier 的 call/put pair，
对两者的 `mid/bid/ask` 同时施加同一 `delta_ticks`。它是一个 declared logical mutation group，
但诚实记录：

```text
logical_mutation_groups       = 1
logical_quote_points_changed  = 2
physical_quote_points_changed = 2
group_semantics = same-strike-same-expiry-call-put-equal-shift
```

两个 executable parity surplus 中 call 与 put 的等量变化严格相消，option leg count/fees 也不变；
但 discounted call/put upper bounds 随 shift 增加，lower bounds 随 shift 减少。因此该 group 只严格
保持 same-pair parity，不保持整个 `cross-asset` family。构造 `100/001/101` 时必须在所有
cross-asset bounds/parity candidates 都保持 inactive 且 guard 通过的 tick window 内接受。

Materializer 必须把两行 quote copy-on-write 作为一个原子操作：任一 leg、before/after、tick、
same-pair key 或 domain gate 失败时整组失败，不能留下半个 mutation。

### 4.3 Underlying spot

```text
mutate_underlying_spot_point_v2
```

纯 mutation 层生成一个 `(valuation_date, underlying_id, delta_ticks)` spec；authoring 层只改变
新 child 中作为 valuation-time spot 的 `spot_close`，所有 option prices、execution contract 和
pricing contract 保持不变。Child 不复制其余 OHLC/adjusted-close 字段，因此不会制造一个违反
OHLC inequality 的伪 daily bar。这是该时刻的 task-state mutation，
不生成或声称生成新的 `P`-measure underlying path。

### 4.4 目标类型签名与控制样本

Blocked successor authoring contract 不再只抽象成 positive/negative balance，而是先在 private
dataset config 中选择一个 canonical type signature。类型顺序固定为：

~~~text
X = cross-sectional
U = cross-asset
T = calendar
signature = (X, U, T)
~~~

Schema-level feasibility set 包含一个 clean control 与七个 positive signatures：

~~~text
000 -> []
100 -> ["cross-sectional"]
010 -> ["cross-asset"]
001 -> ["calendar"]
110 -> ["cross-sectional", "cross-asset"]
101 -> ["cross-sectional", "calendar"]
011 -> ["cross-asset", "calendar"]
111 -> ["cross-sectional", "cross-asset", "calendar"]
~~~

- Clean control：物化同分布 clean child，trusted verifier 必须复算为 `false/[]`。
- Positive child：至多应用一个 logical mutation group；single quote 改一处，spot 改一处，grouped
  call+put 明确改两个 quote points；
  trusted verifier 必须从 public child 复算出与目标 signature 完全相同的 canonical type array。
- Public schema、task id、child snapshot id 和 Solver bundle 都不能暴露目标 signature、是否做过
  mutation、realized mutation count 或 selector failure history。
- Private lineage 保存 requested signature、realized signature、operator、target、before/after、
  cost-adjusted family margins 和最终 guard evidence；requested signature 只是 authoring intention，
  绝不能直接成为 ORM truth。

这八种组合不是当前 runtime capability。只有 deterministic reachability audit 给出非空 exact integer
tick window 的 signature 才能进入 acceptance；当前 v3 audit 对所有 signature 仍为 `NOT_RUN` 或
`BLOCKED_CALENDAR_FAMILY_NULL`，publication count 为零。

### 4.5 Transaction-cost-aware 激活阈值与类型隔离

Transaction cost 不是 verifier tolerance。Selector 对每个 grid point 直接调用 candidate-specific exact
cashflow evaluator；bid/ask、fee、underlying cost 已在各腿现金流中逐项出现，不能再抽象成统一
`alpha + beta * n * Delta - TC` 后重复扣费，也不能假定所有 operator/candidate 对 tick 全局 affine。
Evaluator 先生成 `s_j` 与完整 terminal certificate，再应用第 5.1 节 predicate；calendar candidate
还必须先通过全部 cell/boundary/ray checks，不能用 initial surplus 替代 terminal non-negativity。

对 option signed integer position `w_{j,i}`（正数 long、负数 short），valuation-time executable
cash outflow 是

\[
O_i(w_{j,i})=w_{j,i}^{+}(M_i ask_i+f_i)-w_{j,i}^{-}(M_i bid_i-f_i),
\qquad
s_j=-\left(\sum_iO_i+O_j^{other}\right).
\]

令 `delta_n = d * n * Delta_p`，其中 `d in {-1,+1}`、`n` 属于冻结 integer grid。Single quote
mutation 对固定 candidate 的 exact response 为

\[
s_j(n)=s_j(0)-M_iw_{j,i}\delta_n.
\]

Grouped quote set `G` 则为

\[
s_j(n)=s_j(0)-\delta_n\sum_{i\in G}M_iw_{j,i}.
\]

这些公式只用于推导 response；canonical selector 仍在声明的 binary64 cast/reduction order 下逐 tick
执行 evaluator。Identically-zero payoff 使用 open trigger `s_j>0`，support-certified nonconstant
nonnegative payoff 使用 closed trigger `s_j>=0`。Family bit 是所有 candidate predicates 的 union，
不能用一个 intended candidate 的 threshold 代替 full rescan。

所有可缩放 position vector 先按 joint integer representation 除以正整数 gcd；gcd 不为 1 的 scalar
multiple 不进入 catalogue。之后为每个 candidate 记录 guard vector，而不是一个 family `max(s_j)`：

~~~text
active family:
  至少一个 canonical-arbitrage candidate 同时满足：
    candidate-specific setup-boundary distance >= active_setup_guard
    terminal finite-vertex/boundary/ray slack >= active_terminal_guard（如适用）
    strict-gain certificate 通过

inactive family:
  该 family 的所有 candidates 都不构成套利；并按各自 open/closed setup boundary 与 terminal
  certificate 记录最近的 inactive setup/terminal slack，全部通过 inactive guards
~~~

Parity 的 setup trigger 是开边界 `s > 0`；support-certified nonconstant nonnegative payoff 的 trigger
是闭边界 `s >= 0`。Calendar guard 是 setup USD slack 加 finite-vertex、actual-boundary、one-sided-limit
和 recession-ray slack 的向量，不能把不同量纲压成一个 USD 标量。完整 grammar dataset v3 暂只冻结
single-expiry setup separation `1.00 USD`，calendar terminal guard 为 `null`，因此保持 blocked。Guards
只筛样本，不进入 exact canonical verifier predicate。

Authoring-side selector 的稳定顺序固定为：

~~~text
requested signature
  -> execution contract / profile
  -> operator
  -> valuation date / underlying / expiry / strike / call_put / option_id
  -> sign_order
  -> absolute_tick_grid
~~~

对每个 grid point，selector 在内存中的 solver-visible projection 上运行 authoring-side
独立实现，计算完整 realized bitmask 和双边 guard。它只接受

\[
\operatorname{realized\_signature}(n)
  = \operatorname{requested\_signature}
\]

且所有 active/inactive guards 同时通过的第一个 tick；不存在合适窗口时跳过该 target，不做随机
retry、不扩大 tick grid、不修改 parent、不偷偷换成本合同。Trusted verifier 随后从最终物化的
public child 和 public variant contract 使用独立实现重算。

Transaction costs 只改变各 family 的激活阈值，不能保证任意节点都能实现任意 signature：

- cross-sectional candidates 只使用 option legs，因此不受 underlying cost 直接影响；
- cross-asset candidates 同时使用 option、terminal-spot 和 funding legs；
- calendar candidates 使用两个 expiries，并可能在中间日期调整 underlying，因此 option fee 和
  underlying cost 都进入，但腿数与 cross-asset 不同；
- option fee 会按各模板的 option contract 数量不同地移动阈值，underlying cost 主要移动
  cross-asset/calendar 阈值；两者都不能被当成随意的 label knob。

当前完整 grammar 只使用 `us-options-underlying-5bps-options-flat-050-v3` baseline execution
contract。先在该固定合同下跨 target/node/sign/tick 搜索，不为了某个目标 label 临时改 fee。
若 pilot feasibility audit 证明某些 signature 在整个 parent 上不可达，才允许新增少量
versioned public execution profiles；profile 必须在 mutation 之前确定、写入 variant identity，
并跨多个 signatures 平衡使用，避免 fee profile 成为 label leakage。

三个 mutation operator 的结构性作用与 intended constructive targets 必须分开记录：

| Operator | Changed points | Exact invariant | Intended positives（仍须 reachability proof） |
|:---|:---|:---|:---|
| Clean control | 0 | clean full scan 必须为 `000` | `000` |
| Single option quote | 1 quote point | 无跨 X/U/T invariant；U-anchored threshold regime 是额外条件 | `010`, `110`, `011`, `111` |
| Equal call+put group | 1 group / 2 quote points | 同 pair 两个 parity surplus 不变；整个 U 不 invariant | `100`, `001`, `101` |
| Underlying spot | 1 spot point | `X_after == X_before` | clean-baseline 下 `010`, `001`, `011` |

因此唯一无条件不变量是 `X_after == X_before`，不是“spot-only child 永远没有 X”。本合同采用推荐的
clean-baseline policy：mutation 前独立全量扫描 slice，要求完整 signature 为 `000`，否则 deterministic
skip；在此前提下才可推出 spot child 的 `X_after = false`。若 before/after 不相等，才是 routing 或实现
失败。Single quote 与 spot 的 `realized_chi_F=1`；grouped pair 也按一个 declared group 记
`realized_chi_F=1`，但 lineage 同时强制 `logical_quote_points_changed=2` 和
`physical_quote_points_changed=2`，不得隐藏第二个 option ID。

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

Legacy variant v1/private dataset v1 的 `candidate_spread_strictly_greater_than_zero` 只为旧 skeleton
replay 保留，不能用于新任务。Blocked successor v2/v3 已改用 candidate-specific decision contract；
若实现继续把 `candidate_spread` 作为诊断字段名，必须明确它只是 initial surplus，不是完整 predicate。

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

### 5.3 Transaction-cost-aware calendar catalogue

Calendar family 不能恢复为“同 strike 的长期限 raw option price 必须更高”，也不能把一个 quote
与 BSM theoretical value 的差重新命名为 calendar。首次 blocked review contract 固定为

~~~text
candidate_catalogue_id = bsm-f2a-candidate-catalogue-v3
calendar_family        = null
calendar_target_spec   = transaction-cost-aware-two-expiry-terminal-spot-bridge-v1
~~~

Complete grammar review 在 catalogue v4 中修正 certificate target 为 `g_j`，仍保持
`calendar_family=null`。Future executable identity 若启用该 target，必须使用更新的
candidate/variant ID。该 target 是
catalogue-scoped 的两日期 pathwise certificate，不宣称穷尽所有跨期限动态策略。
对每个 ordered expiry pair \(t<T_1<T_2\)，candidate 必须同时包含至少一个 \(T_1\) option leg
和一个 \(T_2\) option leg；只使用单一 expiry 的组合仍路由到 cross-sectional/cross-asset，
不能因为 scanner 读取了多期限数据就路由成 calendar。

#### 5.3.1 交易时间和 segment execution primitive

Option 仍只在 valuation time \(t\) 按 directional bid/ask 建仓并持有到各自 cash settlement。
Underlying 只允许在 \(t,T_1,T_2\) 改变目标 terminal exposure；每个 segment 内使用第 5.2.1
节已经证明 self-financing 的 continuous-dividend terminal-spot ask/bid primitive。对
\(u<v\)，把第 5.2.1 节公式中的 \(Q_q(t,T)\) 换成

\[
Q_q(u,v)=\int_u^v q(s)\,ds
\]

得到 \(A_S(u,v;S_u)\) 和 \(B_S(u,v;S_u)\)。Signed terminal exposure \(\Delta S_v\) 的
segment valuation-time cash outflow 定义为

\[
\Phi_{u,v}(\Delta;S_u)
  = \Delta^+ A_S(u,v;S_u)
    - \Delta^- B_S(u,v;S_u).
\]

这个 primitive 已把 segment 初始成交、continuous dividend reinvestment/financing 和 segment
末端 liquidation/re-entry 的 `5 bps` 成本计入；不能在外层再次收费，也不能用 frictionless
\(\Delta S_u e^{-q(v-u)}\) 替代。

Candidate 在 \(t\) 选择冻结 catalogue 中的 initial exposure \(\Delta_0\)。在 \(T_1\) 收到短期限
option cash settlement、结算第一段 terminal exposure，并根据已观察的 \(S_{T_1}\) 选择公开
catalogue 中的 Borel rule \(\Delta_1(S_{T_1})\)；随后按
\(\Phi_{T_1,T_2}(\Delta_1(S_{T_1});S_{T_1})\) 建立第二段 exposure。所有 \(T_1\) 剩余 cash
按 child discount curve累计到 \(T_2\)。在 \(T_2\) 收到长期限 option settlement 并完成 terminal
underlying liquidation。每条 option leg 在 \(t\) 按 `abs(position)` 收取每边 fee；hold-to-settlement
不再收退出费。

令 `w_{m,i}` 是 expiry `T_m` option 的 signed integer position（正数 long、负数 short），
`h_{m,i}(S)` 是每张合约、含 multiplier 的 cash-settlement payoff，并定义

\[
\begin{aligned}
O_t(w)&=\sum_{m,i}\left[
  w_{m,i}^{+}(M_{m,i}\,ask_{m,i}+f)
  -w_{m,i}^{-}(M_{m,i}\,bid_{m,i}-f)\right],\\
V_m(S)&=\sum_i w_{m,i}h_{m,i}(S),\\
s_j&=-\left[O_t(w)+\Phi_{t,T_1}(\Delta_0;S_t)\right].
\end{aligned}
\]

`O_t` 和 `Phi` 都是 cash outflow；所以负 outflow 产生正 initial surplus。`s_j >= 0` 时在 `t` 立即
逐项存入 money-market numeraire，但从 certificate payoff `g_j` 中排除，避免用正 setup surplus
掩盖未来负现金流。令 `D(t,u)` 是 valuation-time discount factor，冻结

\[
F_{T_1,T_2}=\frac{D(t,T_1)}{D(t,T_2)}.
\]

在 `T1` 的同一 timestamp 内严格按“短期限 option cash settlement -> 第一段 liquidation -> 根据
实际 `S_T1` 选择唯一 `Delta_1` cell -> 第二段建仓 -> signed 剩余 cash 记入 numeraire”执行。正负
cash balance 都按同一公开 money-market curve lending/borrowing：

\[
\begin{aligned}
C_{T_1}(x)
  &=V_1(x)+\Delta_0x-\Phi_{T_1,T_2}(\Delta_1(x);x),\\
g_j(x,y)
  &=F_{T_1,T_2}C_{T_1}(x)+V_2(y)+\Delta_1(x)y,\\
W_{T_2}(x,y)
  &=\frac{s_j}{D(t,T_2)}+g_j(x,y).
\end{aligned}
\]

第一段/第二段 terminal exposure 已由各自 `Phi` 内部完成 endpoint liquidation/re-entry costs；外层不得
再次收 underlying cost。`g_j` 是第 5.1 节的 terminal certificate payoff，`W_T2` 只用于展示把
initial surplus 存入 numeraire 后的总 liquidation wealth。Canonical predicate 仍分别检查 `s_j`
和 `g_j`，不只检查 `W_T2 >= 0`。

#### 5.3.2 有限 catalogue 与 exact pathwise certificate

Blocked complete-grammar catalogue v4 将以下 target grid 冻结为 review input，但
`calendar_family` 仍为 `null`：

- ordered expiry-pair、option-id、call/put 和 strike enumeration；
- 每个 option position 属于 `{-2,-1,0,1,2}`；每个 maturity 有 `1..4` 个非零 legs、两期限合计至多
  `6` 个，max absolute option position 为 `2`；
- terminal-spot exposure unit 是 `50 shares`；`Delta_0` 与每个 `Delta_1` cell value 的整数 unit grid
  都是 `{-4,-2,-1,0,1,2,4}`，所以 max absolute exposure 是 `200 shares`；
- 把全部 option positions、`Delta_0/50` 和所有 `Delta_1/50` 组成一个 joint integer vector；非零项
  absolute gcd 不为 `1` 的正整数 scalar duplicate 一律拒绝；
- \(T_1\) state partition，固定使用两个 expiry 的全部 strike knots、0 与正无穷 tail；
- expiry pair 按 `T1,T2` ascending，option 按 expiry、call-before-put、strike、option-id，position vector
  按该列顺序 lexicographic；tie-break 是 declared order 的第一个 candidate；
- public DECIMAL market inputs 只在 candidate arithmetic 前 cast 一次 binary64；之后按 option legs、
  `Delta_0` segment、`T1` option settlement、第一段 exposure、第二段 outflow、cash accumulation、
  `T2` option settlement、第二段 exposure 的顺序做 left-to-right reduction。

令 sorted unique strike knots 为 `0 < k_1 < ... < k_m`。`Delta_1` 的唯一 Borel boundary rule 是

```text
(0,k1), [k1,k2), ..., [k_(m-1),k_m), [k_m,+infinity)
```

即 `x = k_l` 使用从该 knot 开始的右侧 cell value；正状态域不含 `0`。Left-cell one-sided limit
仍必须检查，因为它控制任意接近 boundary 的 open-cell 状态，但它不能代替 boundary actual value。

Parent BSM contract 与 \(P\sim Q\) 给出每个 transition 的 full positive conditional support，因此
pathwise state domain 为

\[
(S_{T_1},S_{T_2})\in(0,\infty)^2.
\]

在上述 piecewise-constant stock rule 下，option payoff、segment exposure 和 proportional-cost
cashflow 在 strike cells 上都是 piecewise affine：每个二维 cell 上
`g_j(x,y) = a_cell*x + b_cell*y + c_cell`。Verifier 严格执行：

1. 按 x-cell 后 y-cell 的顺序枚举；对 unbounded cell 的 recession cone，先按 x-ray、y-ray 检查
   `gradient dot ray >= 0`。单尾 cell 只有对应一条 extreme ray；双尾 cell 有两条。
2. Ray 通过后，按 `(x,y)` lexicographic 检查该 cell closure 的全部 finite generator vertices；这些
   值是该 cell 的 one-sided limits。包含 `0` 的 closure 只检查从正状态域进入的 limit。
3. 再按 knot order 检查所有 finite boundary 的 actual values；`Delta_1` 使用上面的右侧归属，`y`
   payoff 在 strike 上取其连续 actual value。交叉 knot 也必须检查，不能只检查两个 one-sided limits。
4. 任一步 `g_j` 的 exact binary64 value 为负即拒绝；不使用 tolerance。全部通过才证明

\[
g_j(S_{T_1},S_{T_2})\ge 0
\quad\text{for every }(S_{T_1},S_{T_2})\in(0,\infty)^2,
\]

而不是只证明可能被正 initial surplus 掩盖的 `W_T2 >= 0`，也不是只在 Monte Carlo paths 或有限
spot grid 上采样。显式 regression 必须覆盖 `D=1, s_j=10, g_j=-5`：虽然 `W_T2=5`，canonical
predicate 仍为 false。Strict gain 固定为：存在一个非空二维 open
cell，其 canonical interior witness（有限区间用 midpoint，单/双尾坐标用 lower-knot-plus-one）满足
`g_j > 0`。在已证明 cell 全域非负后，这等价于 `g_j` 在该 cell 不恒零，并由 `P ~ Q` 与每段 full
positive conditional support 推出 `P(g_j > 0) > 0`。若 `g_j` 恒零，则仍要求 `s_j > 0`。

Self-financing proof test 必须逐项重放上面的 `t/T1/T2` cash ledger，并断言每一笔 option fee 与每次
underlying cost 只收一次。Admissibility proof test 还必须证明整个持有区间的 numeraire-discounted
liquidation wealth 有一个与状态、时间无关的有限下界；只证明 `T2` 非负不够。Blocked catalogue v4 尚未完成
该 interim-wealth proof/evaluator，因此 target grid 不能执行，`calendar_family = null` 必须保持。

这个设计借鉴多期限 bid/ask 市场中 executable semi-static stock-rebalancing certificate 的结构，
但不能直接复制 additive bounded-spread 模型的公式；当前 proportional `5 bps`、实际
dividend curve 和 segment primitive 必须有仓库自己的逐现金流证明。理论参考：
[Multi-maturity consistency of option prices under bounded bid–ask spreads](https://arxiv.org/abs/2607.27649)。

Blocked variant v3 已把上述 grid、cash ledger、`g_j` boundary/ray order 写成 non-executable review
target，但 calendar evaluator、interim admissibility proof tests 和 reachability audit 尚未完成，所以
`calendar_family = null` 仍是 blocking 状态，不能物化含 calendar truth 的 child。完成后必须使用
新的 executable variant/candidate identity；不得在 catalogue v3 或 v4 下静默启用。

### 5.4 Canonical truth 与七种 positive signatures

最终 truth 为：

~~~text
arbitrage_opportunity = any(candidate_is_arbitrage)
arbitrage_type = ordered union of types whose candidate_is_arbitrage is true
~~~

第 1--2 类路由为 `cross-asset`，第 3--4 类路由为 `cross-sectional`，第 5.3 节
two-expiry bridge 路由为 `calendar`。Canonical order 始终是
`cross-sectional`、`cross-asset`、`calendar`。Calendar catalogue 激活且 baseline reachability audit
逐项证明后，schema 允许的 values 为：

~~~text
false -> []
true  -> ["cross-sectional"]
      | ["cross-asset"]
      | ["calendar"]
      | ["cross-sectional", "cross-asset"]
      | ["cross-sectional", "calendar"]
      | ["cross-asset", "calendar"]
      | ["cross-sectional", "cross-asset", "calendar"]
~~~

Verifier 必须保证 `arbitrage_opportunity == bool(arbitrage_type)`。数组表示各 type family 至少各有
一个通过 canonical predicate 的 candidate，不接受顺序不定的 set、重复值或自由文本。
Authoring requested signature、mutation intention 和 fee-threshold estimate 都不能复制成 truth。

Legacy variant v1 与 blocked successor v2 都把 calendar 设为 `null`。七种 positive signatures 只是
target feasibility set；audit 必须逐 signature 输出 reachable target/operator/tick windows 或确定性
不可达诊断。在 audit 证明前不得把七种 positives 写成 dataset acceptance；不可达时缩小发布 scope
或继续 blocking，不能随机 retry、扩大 grid、修改 parent 或临时换 fee。

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
├── f2a_dataset_v1.json       # legacy replay
├── f2a_dataset_v2.json       # blocked first-successor review record
└── f2a_dataset_v3.json       # blocked complete-grammar distribution/routing/split contract

configs/generators/
└── quantlib_bsm_metals_f2a_parent_v2.json
    # 已物化 frozen parent 的 0.01 USD increments、identity 和完整 parent DGP

configs/variants/
├── bsm_arbitrage_finding_f2a_v1.json  # legacy catalogue v2
├── bsm_arbitrage_finding_f2a_v2.json  # blocked catalogue v3 review record
└── bsm_arbitrage_finding_f2a_v3.json  # blocked complete grammar / catalogue v4

configs/mutations/
├── f2a_point_v1.json         # legacy engine
├── f2a_point_v2.json         # blocked single-point review record
└── f2a_complete_v3.json      # single quote + grouped pair + spot；诚实 point counts

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
├── f2a-lineage.schema.json   # legacy catalogue v2 const
├── f2a-lineage-v2.schema.json
├── f2a-lineage-v3.schema.json
├── trajectory.schema.json
├── submission.schema.json    # legacy variant v1 const
├── submission-v2.schema.json
└── submission-v3.schema.json
```

Legacy、首次 blocked review 与完整 grammar review contracts 已按这些路径并存；`configs/arbitrage/`
仍不存在。V3 锁定 candidate-specific predicate、完整三 operator grammar、single-expiry formulas、
public support/clock/timeline、selector shape 和 `g_j` calendar review target，但
`runtime_enabled = false`、`calendar_family = null`。Calendar
evaluator/admissibility proofs、Python v2 dispatch 和 F2A runtime 均不存在；不得据此声称 child 或
dataset 已物化，也不得修改 blocked v2/v3 identity 来启用后续能力。

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
  execution cost、candidate catalogue/predicate、calendar segment/cell certificate
  active type routing、operation order、output contract 和 Solver 权限

mutation config:
  logical operator ids、integer tick counts、target 与 enumeration order

private authoring config:
  parent selectors、requested type-signature balance、active/inactive guard bands
  execution-profile feasibility policy、split 与数据集规模
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
  mutation spec，按第 3 节 allowlist 生成新 DRAFT child，物化 market fields，并按第 4.5 节的
  stable tick search 验证 requested signature 与双边 guard；随后写 private lineage，最后以新
  identity/revision 冻结。它不重新运行 QuantLib repricing，也不把 authoring-side expected label
  当成 ORM truth。
- `mutation/f2a.py`：定义 immutable point-mutation spec、stable selector、operator ID、integer
  tick count、child identity input 和 lineage record。它是纯逻辑，不读写 DuckDB、不导入
  QuantLib、不运行 oracle。
- `solver/f2a.py`：只读公开 child 与 public variant contract，在 allowlist-only Solver image 内
  手工枚举 catalogue 并产生完整 trajectory/submission；不导入 authoring/verifier，也不调用
  QuantLib、py_vollib、mibian、rateslib 或其他预制 option pricing/IV/Greek/surface/arbitrage API。
- `verifier/f2a_oracle.py`：只从 public child 和 public variant contract 重算第 5.2 节的
  cross-sectional/cross-asset spreads、第 5.3 节的 calendar pathwise certificate 与 canonical
  type bitmask；不读 parent、private lineage、requested signature、mutation intention 或
  authoring-side expected result，不导入 Solver 实现。
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
datasets/manifests/splits/f2a_v1.json  # legacy only
datasets/manifests/splits/f2a_v2.json  # blocked v2 only
datasets/manifests/splits/f2a_v3.json  # blocked v3 audit-only; not materialized

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

V3 lineage 把 requested/realized signature 放在 top level；`authoring_guard_evidence` 保存每个 family
的最接近触发
candidate id 与 cost-adjusted margin、active/inactive guard、execution contract/profile、最终 tick
和 exact violated invariant；positive lineage 不能只写宽泛的 mutation intention。若把它提升为独立字段，必须先 version schema，不能向当前
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
   bid/ask、cross-sectional/cross-asset formulas，以及第 5.3 节 two-expiry segment/cell certificate
   写入 versioned public contracts；为每个 template 给出 self-financing/admissibility proof test。
   Calendar contract 未通过时保持 `calendar_family = null`，不得伪造 calendar label。
2. 把第 4.5 节 cost-adjusted signature selector 写入 private authoring contract：固定 stable search
   order、requested signature distribution、active/inactive guards、profile feasibility policy 和
   no-label-leakage rules。当前过时的 positive/negative-only balance 与统一 `spread > 0` 字符串必须移除。
3. 保留六维 task-space/schema/config v1 不变，并行增加七维 v2 runtime dispatch、F enum、F2A
   compatibility rule 和 curriculum v2。先证明旧 manifests、serialization 和 deterministic child IDs
   完全不变。
4. 在 `verifier/` 用手工小市场实现独立 F2A oracle，覆盖第 5.2 节全部 exact spreads、
   option fee、underlying `5 bps`、integer positions、严格零边界，以及第 5.3 节全部
   calendar cell/tail certificates和 canonical type order。
5. 实现纯 `mutation/f2a.py` spec/identity/lineage，再在
   `authoring/f2a_child_materializer.py` 实现 parent selection、allowlisted market projection、
   single quote、grouped call+put 和 spot 三类 operators、requested-signature tick search、双边
   guard、atomic grouped materialization、child freeze 与
   private lineage。两层不共享 DB write 权限，authoring selector 与 trusted verifier 不共享实现。
6. 实现 Solver 任务入口与 trajectory/submission verifier；trusted verifier 只从 public child 和
   public variant contract 重算 bool 与 canonical type array。
7. 实现 trusted DuckDB adapter、实际 dependency lock、import/API gate 和 sandbox acceptance；在此
   之前不发布 Solver image。
8. 实现 `scripts/materialize_f2a.py`、public task/split manifests 和 training export，确认 private
   requested signature、selector trace 和 family margins 不进入 Solver bundle 或训练数据。
9. 先运行不写 child 的 deterministic reachability audit；逐 signature 输出 exact integer-tick windows
   或不可达诊断。只有 audit 证明的 scope 才能 materialize feasibility set。Grouped pair 是一个
   logical group/两个 quote points；不得要求未证明可达的七种 positives 全覆盖。

## 9. 最小验收测试

### Unit

- V1 six-axis schema/registry/manifests 继续原样 parse/serialize，旧 deterministic child IDs 不变；v2
  F enum/schema/registry/curriculum round-trip。
- Pure mutation spec 同 selector/operator/seed/tick count 完全相等，换任一 identity input 即不同；
  `mutation/f2a.py` 不打开 DuckDB、不导入 QuantLib/verifier。
- Single-option operator 只改变一个 quote point；grouped operator 原子改变 same-pair 两个 quote
  points并分别由 parent half-spread 派生 `mid/bid/ask`；spot operator 只改变一个 `spot_close`。
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
- Calendar tests 必须逐 cashflow 验证两个 segment execution primitives、T1 state-contingent
  rebalancing、T2 liquidation 和 option fees；cell vertices、one-sided boundaries 与 tail slopes
  必须共同证明全正状态域非负。仍须拒绝 raw same-strike maturity comparison、有限 spot-grid
  sampling 或 frictionless BSM dynamic replication 作为 transaction-cost market 的套利证明。
- Grouped-pair algebra 必须验证两方向 parity surplus 不变、upper/lower bounds 的相反 slopes、两个
  physical quote point counts 和任一 leg 失败时 atomic rejection。
- 对每个 requested signature，测试 stable tick search 只接受 realized bitmask 完全相同且 active/
  inactive guards 同时通过的 child；不存在可行窗口时 deterministic skip，不能改 fee 或扩大 grid。

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
- 每个拟用 slice 先独立验证为 `false/[]`，不满足就 deterministic skip；只有 deterministic
  reachability audit 证明的 signatures 才进入发布 acceptance。Spot mutation 前后必须断言
  `X_after == X_before`；由于本 policy 要求 baseline `000`，accepted spot child 才有 `X_after=false`。
  每个 accepted child 都远离全部 candidate-specific active/inactive boundaries。

### Public 与 verifier robustness

- Solver bundle 只包含 public child、task/variant contract 和 trajectory/submission schemas；公开 smoke 能完成
  child -> submission -> verifier 端到端路径。
- ORM schema-level 可表示 `000` 与七个 positive signatures；public runtime tests 只覆盖
  reachability-proved scope。拒绝缺失/错序/错分/重复的 type array、与 bool 不一致的 type array、缺失 trajectory
  或额外的 `maximal_spread`。Legacy 与 blocked successor 都未启用 calendar，仍必须拒绝 calendar
  type；未来启用需要新的 executable catalogue identity。
- 保持 ORM answer 不变而改写 trajectory 文本不改变 reward；翻转 bool、删除/交换 type、改单位、
  contract ID 或 child snapshot revision 均必须失败。
- 现有 authoring/public/task-space/mutation/curriculum tests 全部继续通过。

## 10. F2A 暂不做

- 同一 child 中超过一个 logical mutation group，或超出 declared grouped call+put pair 的任意多点 mutation；
- `maximal_spread` 或最优套利组合；
- 完整复刻某一真实交易所的 maker/taker、broker、clearing、regulatory fee/rebate schedule；
- bid/ask size、partial fill、market impact、margin、borrow availability 或 position limit；
- 多 pricing-model snapshot；
- 超出冻结 two-expiry bridge catalogue 的 unrestricted/global LP、PDE 或 Monte Carlo arbitrage search；
- 修改现有 frozen parent。
