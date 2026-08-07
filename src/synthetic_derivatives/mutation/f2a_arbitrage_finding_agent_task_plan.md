# F2A Arbitrage-Finding Agent Task 计划

## 1. 目标

现有 frozen DuckDB 保持不可变：

```text
snapshots/public/quantlib_bsm_smoke_v1.duckdb
```

它的 underlying 和 option quotes 已按 `0.00000001` 生成，只作为 legacy replay source。要求
`0.01 USD` 最小报价单位的 F2A 不能原地修改或继续沿用该 snapshot identity；必须使用增加了
显式 minimum-price-increment config、以新 snapshot id materialize 的 frozen parent。

Authoring 只需按新 increment config 一次性 materialize 并冻结新的 clean parent；不覆盖 legacy
snapshot，不把利率或股息改成 0，也不构造新的 pricing model。此后每个 task 不重新生成市场，
只做四件事：

```text
从新的 tick-aligned frozen parent 抽取一个多 strike、多 expiry 的 clean subset
  -> mutation 层生成纯 mutation spec、child identity 和 lineage
  -> authoring 层物化并冻结至多修改一个 logical quote/spot point 的 child
  -> trusted verifier 从公开 child 和 variant contract 独立复算验收结果
```

F2A 的难度来自 mutation 位置未知、需要扫描完整 subset，而不是来自同时修改很多数据。
这条支线复用项目 README 已定义的 `authoring/task_space/mutation/solver/verifier/training`
边界，不新增顶层 `arbitrage` package，也不让 `mutation` 直接写 DuckDB。

## 2. Task 数学与交易合同

### 2.1 固定身份

第一版固定为：

```text
task_family_id       = bsm_arbitrage_finding_f2a_v1
coordinates         = (L5, P0, M0, A0, D4, R3, F2A)
pricing_model       = parent snapshot 已有的 deterministic time-inhomogeneous BSM
execution_contract  = us-options-underlying-5bps-options-flat-050-v2
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

套利定义在物理测度 `P` 及其 null sets 下。任务同时声明 parent BSM pricing measure
`Q = USD-MONEY-MARKET-Q-v1` 与 `P` 等价，numeraire 为
`USD-MONEY-MARKET-ACCOUNT-v1`。在 `Q` 下，当前 ex-dividend spot 满足

```text
dS_u / S_u = (r(u) - q(u)) du + sigma_Q(u) dW_u^Q.
```

当前信息集包含 valuation time 的 spot、完整 European option chain、discount/dividend curves、
deterministic `Q` volatility function、calendar、day-count、settlement 和 execution contract。
第一版不含 stochastic rate、stochastic volatility、jump、regime 或 multi-asset state。

时间轴沿用 parent 的 calendar time 和 `Actual365Fixed`。Piecewise-linear instantaneous annualized
volatility 在 valuation-to-expiry 区间按 integrated variance 精确归约：

```text
sigma_eff(t, T)
  = sqrt(integral[t,T] sigma_Q(u)^2 du / (T - t)).
```

这是真正的 deterministic time-inhomogeneous GBM exact reduction，不是 endpoint volatility、
arithmetic-average volatility 或近似。

策略类是有限 catalogue 上的 semi-static/dynamic 策略：option 只在 valuation time 交易并持有到
cash settlement；underlying 每次成交按单边 `5 bps` 收费，cash account 保持无摩擦。所有候选模板
使用有限整数 option positions，discounted wealth 必须有统一下界；禁止 doubling strategy。

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

`0.50 USD/contract/side` 和 underlying 单边 `5 bps` 都是本 synthetic task 的冻结 variant
assumption，不声称复刻某一家真实
交易所的完整收费。真实美国 option minimum increment 和费用随 option class、premium、参与者、
add/remove liquidity 和 simple/complex order 改变；第一版只借用其按合约计费和 multiplier 语义。
参考 [C2 Rule 5.2--5.4](https://cdn.cboe.com/resources/regulation/rule_book/C2_Exchange_Rule_Book.pdf)
与 [C2 fee schedule](https://www.cboe.com/us/options/membership/fee_schedule/c2/)。

对价格为 `S`、signed share quantity 为 `Delta` 的一次 underlying trade，valuation-time cash outflow
固定为

```text
Delta * S + 0.0005 * abs(Delta) * S
```

所以买入按 `S * 1.0005`、卖出按 `S * 0.9995` 执行。该费用不是 bid/ask quote，也不改变
snapshot 中的 spot；它只在 candidate strategy 的 cashflow 中逐次计入。

最小报价单位、bid/ask spread 和逐合约费用是三个不同对象。新 parent 的 generator config
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

费用按 `abs(position)` 线性累计。到期 cash settlement 不再收取退出交易费。Underlying 与 cash
account 必须保持无摩擦，否则连续动态对冲不再具有 BSM analytic replication cost，不能继续沿用
本阶段的 oracle。

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

优先使用当前 snapshot 已有的完整 `4 expiries x 7 strikes x call/put = 56` 行 chain。若 slice
不完整就跳过，不补数据、不重定价 parent。

Child DuckDB 是新 identity 下的最小 frozen market snapshot，保持项目现有的
market/task 边界。它只保存市场事实与公开 market context：

```text
solver_visible.underlying_daily
solver_visible.option_daily(..., bid, ask, mid)
solver_visible.pricing_metadata
```

它不新增第四个市场价格 `task_price`。对 option-price mutation，authoring 层先从 clean
parent 记录 half-spread，再在 child 中物化标准 `mid/bid/ask` 字段：

```text
bid_offset = parent.mid - parent.bid
ask_offset = parent.ask - parent.mid

child.mid = parent.mid + delta_ticks * option_minimum_price_increment
child.bid = child.mid - bid_offset
child.ask = child.mid + ask_offset
```

这是一个 logical quote-point mutation；`bid/ask` 是从该 point 确定性派生的市场字段。
Private lineage 保存一个 logical target 及所有派生的 physical before/after 值，但
`realized chi_F` 仍按冻结合同计数。Child 不包含 parent database、private authoring tables、
physical path、seed/RNG、before-value、reference answer 或 private lineage。

Task convention 不混入 market snapshot。Public task manifest 只引用 child `snapshot_id/revision`、
task-space v2 rule、public variant contract 和 output contract。`Q`/numeraire/rate-path identities、
deterministic `Q` volatility function 的 `time_origin/interpolation/extrapolation/units`、execution fee
和 candidate catalogue 都放在 `configs/variants/bsm_arbitrage_finding_f2a_v1.json`。它们是
Solver 必须知道的 task convention，不是 child 市场中的第二份 DGP provenance。

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
verifier 使用独立实现从物化后的 public child 重算。Mutation 后必须保持 `child.bid >= 0`
和 option price domain。

### 4.2 Underlying spot

```text
mutate_underlying_spot_point_v1
```

纯 mutation 层生成一个 `(valuation_date, underlying_id, delta_ticks)` spec；authoring 层只在
新 child 中改变 solver-visible `spot`，所有 option prices、execution contract 和 pricing
contract 保持不变。这是该时刻的 task-state mutation，
不生成或声称生成新的 `P`-measure underlying path。

### 4.3 正负样本

- Positive：应用上述一个 operator，且 trusted verifier 必须从 public child 复算为 `true`。
- Negative：物化同分布的 clean child，且 trusted verifier 必须复算为 `false`。
- Public schema 不暴露是否做过 mutation；private manifest 保存 target、before/after、operator、
  parent/child ids、realized `chi_F` 和预期受影响的 invariant。
- Authoring guard band 是样本选择条件，不改变 oracle 的严格 `spread > 0` decision rule。

## 5. Independent oracle 与候选策略

### 5.1 Oracle 边界

Oracle 只读取 child 的 solver-visible 数据和 public execution contract，不读取 parent、预存 label、
private lineage 或 mutation intention。它使用冻结的 candidate catalogue、dtype、枚举顺序和 BSM
数值合同，生成每个候选策略的 valuation-time net spread。

对任一 option portfolio `w`，其市场 acquisition cost 按每条腿的方向使用 ask/bid，并对
`abs(w_i)` 收费；其比较对象必须是该模板声明且在 `5 bps` underlying cost 下可执行的
funding/underlying portfolio 或非负 payoff。无摩擦 BSM dynamic replication 不进入当前
catalogue。只有

```text
candidate_spread > 0
```

才构成 catalogue arbitrage。`candidate_spread == 0` 不是套利。Verifier 不使用 tolerance；
authoring 通过 guard band 排除接近零、可能跨 backend 翻转的 child。

### 5.2 Cross-sectional 与 cross-asset catalogue

同一 valuation time、同一 expiry/settlement bucket 内检查：

1. `cross-asset`：European discounted lower/upper bounds；
2. `cross-asset`：同 strike 的 executable put-call parity；
3. `cross-sectional`：strike monotonicity；
4. `cross-sectional`：非等距 strike convexity。

所有 inequalities 都必须使用可执行方向：long leg 用 child `ask`，short leg 用 child `bid`，每条
option leg 计入逐合约费用。Convexity 使用由 strike gaps 确定的冻结整数 position ratios，不能在
verifier 中临时改成任意 fractional portfolio。Bounds/parity 的每条 underlying trade 按 signed
quantity、spot 和单边 `5 bps` 计费；cash、discount bond 和 dividend/carry legs 使用第 2 节声明的
funding contract。

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
arbitrage_opportunity = any(candidate_spread > 0)
arbitrage_type = ordered union of types whose candidate_spread > 0
```

第 1--2 类路由为 `cross-asset`，第 3--4 类路由为 `cross-sectional`；启用后的跨期限候选路由为
`calendar`。`arbitrage_type` 使用固定顺序 `cross-sectional`、`cross-asset`、`calendar` 的 enum
array：

```text
false -> []
true  -> ["cross-sectional"]
      | ["cross-asset"]
      | ["calendar"]
      | ["cross-sectional", "cross-asset"]
      | ["cross-sectional", "calendar"]
      | ["cross-asset", "calendar"]
      | ["cross-sectional", "cross-asset", "calendar"]
```

Verifier 必须保证 `arbitrage_opportunity == bool(arbitrage_type)`。数组表示 AND/OR，不接受
顺序不定的 set、重复值或自由文本；类型也不得从 private mutation intention 复制。

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
  "arbitrage_type": ["cross-sectional", "cross-asset", "calendar"]
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
└── quantlib_bsm_metals_f2a_parent_v1.json
    # 0.01 USD increments、新 generator/snapshot identity 和完整 parent DGP

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
已经按这些路径落位；`configs/arbitrage/` 已移除。它们冻结 repo contract 与目录责任，不代表
F2A runtime、child snapshot 或 dataset 已经 materialize。

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
  Q/numeraire/rate-path contract、execution cost、candidate catalogue
  active type routing、operation order、output contract 和 Solver 权限

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
  mutation spec，copy-on-write 生成新 DRAFT child，物化 market fields、运行 authoring gates、
  写 private lineage，最后以新 identity/revision 冻结。它可以使用 pinned QuantLib，但不把
  authoring-side expected label 当成 ORM truth。
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
snapshots/generated/f2a/<child_snapshot_id>/child.duckdb
snapshots/generated/f2a/<child_snapshot_id>/child.manifest.json

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

`datasets/generated/` 只存最终可重建的训练 JSONL/Parquet，不存 child DuckDB 或 private
lineage。Public task id 和 child snapshot id 不编码 operator、target、`arbitrage_type` 或
positive/negative status。Solver bundle 只挂载 public child、public task manifest、public variant contract 和
trajectory/submission schemas。

### 7.4 权限与数据流

Solver 的权威 environment/import/API allowlist 见
[`environments/solver/README.md`](../../../environments/solver/README.md)。F2A 允许 pinned
NumPy/Pandas 作为通用数值与表格原语，但 DuckDB connection 只由 trusted query adapter 持有；
允许这些通用包不授权任何预制 option pricing/IV/Greek/surface/arbitrage API。该 allowlist
当前已完成设计，dependency lock 与 runtime enforcement 仍待实现。

| 边界 | 可读 | 可写 | 禁止 |
|:---|:---|:---|:---|
| Authoring | frozen parent、generator/mutation/private dataset configs | new child snapshot、private lineage、authoring gates | 覆盖 parent；把 expected label 写入 public child |
| Mutation | public identities 与 versioned mutation config | immutable spec 与 lineage record | 读写 DuckDB；运行 oracle |
| Solver | public child、task/variant contract、public schemas；allowlist-only dependencies | trajectory 和 submission | 任意预制 option pricing/IV/Greek/surface/arbitrage capability、private lineage、hidden tests/verifier |
| Trusted verifier | public child、task/variant contract、submission、hidden tests | verification report 和 private diagnostics | 导入 Solver 实现；信任 stored label |
| Training | verified public task/trajectory/outcome 和 grouping IDs | JSONL/Parquet 与 split manifest | 导出 private lineage、hidden diagnostics 或 oracle traces |

## 8. 实施顺序

1. 先冻结本文件第 2、5 节的 market、execution、candidate-template 和 ORM contract，并按
   第 7.1 节分配 generator/variant/mutation/private-authoring config ownership。
2. 复制现有 metals generator config 为新 F2A parent config，只在声明的模型变更范围内将
   underlying/option increments 改为 `0.01 USD`，同时更新 generator config/version 与
   snapshot identity。使用现有 authoring pipeline 生成到 `snapshots/generated/`，通过 quality
   gates 后冻结；不改已有 public snapshot。
3. 保留六维 task-space/schema/config v1 不变，并行增加七维 v2 dispatch、F enum、F2A
   compatibility rule 和 curriculum v2。先证明旧 manifests、serialization 和 deterministic child IDs
   完全不变。
4. 在 `verifier/` 用手工小市场实现独立 F2A oracle，覆盖两类 cross-sectional candidate、
   两类 cross-asset candidate、option fee、underlying `5 bps`、integer positions、严格零边界与
   canonical type order。Calendar candidate 在交易成本下的策略合同冻结前不实现。
5. 实现纯 `mutation/f2a.py` spec/identity/lineage，再在 `authoring/f2a_child_materializer.py` 实现
   parent selection、standard market projection、两个 logical point operators、authoring-side boundary search、
   child freeze 与 private lineage。两层不共享 DB write 权限。
6. 实现 Solver 任务入口与 trajectory/submission verifier；trusted verifier 只从 public child 和
   public variant contract 重算 bool 与 canonical type array。
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
  delta 唯一物化；spot operator 只改变一个 child `spot`。
- Parent generator increment 与 public variant 声明在 authoring boundary 检查一次；mutation 幅度使用
  integer tick counts，不存另一份 decimal increment source。
- Underlying OHLC/close 是 underlying increment 的整数倍；option 从 rounded spot 定价，child
  `bid/ask/mid` 是 option increment 的整数倍。
- 每条 option leg 按 `abs(position)`、multiplier 和 `0.50 USD/contract/side` 计费；每条
  underlying trade 按 `0.0005 * abs(traded_notional)` 计费；candidate spread 恰好为零判
  `false`，不使用 tolerance。
- Cross-asset bounds/parity 使用 executable option side、underlying `5 bps` 和实际 funding/carry；
  cross-sectional monotonicity/convexity 使用 executable option side 和费用。
- 当前 catalogue 不生成 calendar truth；测试必须拒绝把 raw same-strike maturity comparison 或
  frictionless BSM dynamic replication 当作 transaction-cost market 的套利证明。

### Integration

- Legacy public snapshot 保持 byte-identical；新 tick-aligned parent 使用新 generator/snapshot identity 并冻结。
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
  array、与 bool 不一致的 type array、缺失 trajectory 或额外的 `maximal_spread`。
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
