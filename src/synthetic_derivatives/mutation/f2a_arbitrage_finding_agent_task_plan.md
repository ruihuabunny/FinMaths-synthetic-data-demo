# F2A Arbitrage-Finding Agent Task 计划

> 实现状态（2026-08-10）：本文件是 future/design-only 计划，不是当前可运行 task。
> 七维 runtime、`F2A` enum 和显式 legacy-six-axis migration 已在结构层完成，但仓库尚无
> F2A subset/mutation/oracle/package implementation，也没有 materialized F2A artifacts。
> 当前 accepted BSM Greeks task 固定为 `F0`，并使用独立 config `1.7.0` P/Q parent；本计划
> 指向的 checked-in config `1.5.0` snapshot 保持不可变，仅作为未来 F2A 设计的候选母快照。

## 1. 目标

若实施，F2A 将直接复用现有无套利 frozen DuckDB：

```text
snapshots/public/quantlib_bsm_smoke_v1.duckdb
```

不重新生成 snapshot，不把利率或股息改成 0，也不构造新的 pricing model。每题只做四件事：

```text
从现有 snapshot 抽取一个多 strike、多 expiry 的 clean subset
  -> 复制为 solver-visible child
  -> 至多 mutation 一个 option price 或一个 spot
  -> 独立复算 arbitrage_opportunity 和 arbitrage_type
```

F2A 的难度来自 mutation 位置未知、需要扫描完整 subset，而不是来自同时修改很多数据。

## 2. Task 合同

第一版固定为：

```text
task_family_id     = bsm_arbitrage_finding_f2a_v1
coordinates       = (L5, P0, M0, A0, D4, R3, F2A)
pricing_model     = parent snapshot 已有的 deterministic time-inhomogeneous BSM
transaction_cost  = 0
executable_price  = task_price
output_contract   = arbitrage-opportunity-type-trajectory-v1
```

- Parent 始终只读，child 使用新的 snapshot/task identity。
- `task_price` 从 parent 的 canonical option mid price 投影；F2A 不使用 bid/ask 成本。
- 使用 parent 已声明的 `Q`、money-market numeraire、实际 `r/q`、calendar、day-count、
  settlement 和 deterministic pricing-volatility curve。
- 允许按任务声明交易 underlying、cash account 和 European options，并允许 BSM 动态复制。
- Solver 看不到 parent before-value、mutation 位置、private lineage 或 hidden label。

套利与无套利的通用定义遵循 [AGENTS.md](../../../AGENTS.md)。完整七维框架见
[financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md](../../../docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)。

## 3. Subset

一个 task 的稳定选择键为：

```text
(parent_snapshot_id, parent_revision, valuation_date, underlying_id)
```

从该时刻抽取：

- 一个 underlying spot；
- 同一 valuation date 的完整 live option chain；
- 多个 strikes、call/put pairs 和多个 expiries；
- 对应的 rate、dividend/carry、calendar/day-count 和 Q-volatility 数据。

优先使用当前 snapshot 已有的完整 `4 expiries x 7 strikes x call/put = 56` 行 chain。若 slice
不完整就跳过，不补数据、不重定价 parent。

Solver child 只需四类公开数据：

```text
task_context
underlying_state
option_chain(..., task_price)
pricing_context
```

## 4. 两个单点 mutation operator

### 4.1 Option price

```text
mutate_option_price_point_v1
```

在一个 `(valuation_date, option_id)` 上只改变 `task_price`。其 strike、expiry、option type、
spot、curves、volatility 和其他 option prices 全部保持不变。

Mutation amount 从版本化 decimal grid 中确定性选择。生成器选择第一个足以越过 oracle
判定边界并保留 guard band 的值；不依赖随机 retry。

### 4.2 Underlying spot

```text
mutate_underlying_spot_point_v1
```

在一个 `(valuation_date, underlying_id)` 上只改变 solver-visible `spot`，所有 option prices 和
pricing context 保持不变。这是该时刻的 task-state mutation，不生成或声称生成新的 `P`-measure
underlying path。

### 4.3 正负样本

- Positive：应用上述一个 operator，且 independent oracle 必须复算为 `true`。
- Negative：直接使用同分布的 clean subset，且 independent oracle 必须复算为 `false`。
- Public schema 不暴露是否做过 mutation；private manifest 保存 target、before/after、operator、
  parent/child ids 和预期受影响的 invariant。

## 5. Independent oracle

Oracle 只读取 child 的 solver-visible 数据，不读取预存 label 或 mutation intention。它使用与
parent 一致的数值合同检查：

1. European discounted lower/upper bounds；
2. 同 expiry、同 strike 的 put-call parity；
3. 同 expiry 的 strike monotonicity；
4. 同 expiry 的非等距 strike convexity；
5. 跨 expiry 的 BSM replication consistency。

第 5 项是本阶段的 calendar 检查。它必须：

- 用 snapshot 的实际 calendar/day-count 计算每个 expiry 的 `tau`；
- 用实际 `r/q` 和从 valuation date 到 expiry 的 integrated Q variance；
- 对每个到期日按 parent 声明的同一个 BSM contract 计算可复制价格；
- 检查整个多期限 subset 是否与这些复制价格一致。

它不使用“同 strike 的长期限 option price 必须更高”这条错误捷径。此处 option 理论值之所以
可用于套利判定，是因为任务明确声明 BSM 完备市场和动态复制：claim price 与其复制成本不同时，
可以买入较便宜的一侧并卖出较贵的一侧。

最终 truth 为：

```text
arbitrage_opportunity = any(declared invariant violation)
arbitrage_type = ordered union of violated categories
```

其中第 1--4 类路由为 `cross-sectional`，第 5 类路由为 `calendar`。`arbitrage_type`
使用固定顺序 `cross-sectional`、`calendar` 的 enum array：

```text
false -> []
true  -> ["cross-sectional"]
      | ["calendar"]
      | ["cross-sectional", "calendar"]
```

Verifier 必须保证 `arbitrage_opportunity == bool(arbitrage_type)`。数组表示 AND/OR，不接受
顺序不定的 set、重复值或自由文本；类型也不得从 private mutation intention 复制。

Authoring 端只选择远离 rounding boundary 的 mutations；Verifier 使用冻结的 dtype、操作顺序、
canonicalization 和 exact decision rule，不靠宽松 tolerance 把模糊样本判成正例。

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
  "arbitrage_type": ["cross-sectional", "calendar"]
}
```

`arbitrage_opportunity` 与 `arbitrage_type` 都必须 exact-match hidden verifier 从 public child 重算的
canonical 值。无套利答案为
`{"arbitrage_opportunity": false, "arbitrage_type": []}`。F2A 不生成、不提交、也不验收
`maximal_spread`；该字段从 F2B 开始。

## 7. 实施顺序

1. [已完成结构层] task coordinates、schema 和 registry 已加入 string enum `F2A`；旧六维
   task 只能通过唯一显式 adapter 迁移到 `F0`，runtime 不在普通调用点静默补默认值。
2. [待实现业务层] 新增聚焦模块 `src/synthetic_derivatives/mutation/f2a.py`，只负责 subset、两个单点 operator、
   child export 和 private lineage。
3. [待实现] 新增独立 verifier，从 child public tables 重算第 5 节的 bool 与 canonical type array。
4. [待实现] 先 materialize 一个小 smoke set，正负样本平衡，option-price/spot operator 均有覆盖。
5. [待实现] Smoke 通过后再扩充样本量；仍保持每个 positive 只有一个 logical mutation。

## 8. 最小验收测试

- Parent 以 read-only 打开且内容不变；child 有新 identity。
- 同 selector/operator/seed 可 byte-identical replay。
- Option operator 只改变一个 `task_price`；spot operator 只改变一个 `spot`。
- Clean subset 的 verifier 结果为 `false`，两个 operator 都能稳定产生 `true`。
- Calendar 使用实际 `r/q`、calendar/day-count 和 integrated variance，不做 raw same-strike
  maturity comparison。
- Solver child 不含 before-value、mutation location、hidden label 或 audit table。
- ORM 接受完整 trajectory 中的 bool 与 canonical `arbitrage_type`，拒绝缺失/错序/错分的
  type array、与 bool 不一致的 type array、缺失 trajectory 或额外的 `maximal_spread`。
- 现有 snapshot、task-space 和 mutation regression tests 继续通过。

## 9. F2A 暂不做

- 多点 mutation；
- `maximal_spread` 或最优套利组合；
- bid/ask execution cost 或非零交易成本；
- 多 pricing-model snapshot；
- LP/PDE/Monte Carlo arbitrage search；
- 修改现有 frozen parent。
