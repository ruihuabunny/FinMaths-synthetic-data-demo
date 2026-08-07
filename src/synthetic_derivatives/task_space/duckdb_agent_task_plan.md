# 基于现有 DuckDB 合成 Agent Task 的实施计划

## 1. 目标、数据源与当前结论

本计划现在明确分为两条并行、不可混用 identity 的实施 lane：

| Lane | 坐标/任务 | 数据源 | 当前状态 |
|:---|:---|:---|:---|
| Ordinary BSM | 六维 v1 `(L, P, M, A, D, R)`，等价于七维语义中的 `F0` | 已冻结的 legacy public v3 snapshot | Price/IV/Greek/DuckDB task 仍是设计计划，尚无 Solver/verifier/dataset runtime。 |
| F2A arbitrage-finding | 七维 v2 `(L5, P0, M0, A0, D4, R3, F2A)` | 独立 tick-aligned F2A v2 frozen parent 产生的新 child | Parent、legacy replay 与 blocked successor v2/v3 contracts 已落地；calendar proofs、reachability 与端到端 runtime 尚未实现。 |

普通任务第一版显式选择
`snapshots/public/quantlib_bsm_smoke_v1.duckdb` 作为 legacy authoring source。它不是默认 active
development database；后者是
`snapshots/generated/quantlib_bsm_metals_option_chain_smoke_v1_20260807.duckdb`，逻辑 identity
同为 v3、状态为 `DRAFT / r1`，在当前 pipeline 下只读，不能替代要求 frozen source 的任务。

F2A 显式选择：

```text
snapshots/generated/f2a/parents/
  DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/parent.duckdb
snapshot = DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1 / FROZEN
```

两条 lane 都以同一个权限闭环为目标：

```text
Frozen DuckDB snapshot
  -> deterministic task selection
  -> immutable task instance
  -> solver-visible input
  -> candidate submission
  -> independent hidden verifier
  -> verification outcome / trajectory
```

Legacy public v3 DuckDB 足够支持第一版 Black--Scholes--Merton（BSM）vanilla task pilot，
但不能原样作为 Solver 的数据库。它同时包含 `solver_visible` views、private
`market.option_pricing_audit` 和 authoring provenance；数据库内的 view 不是权限边界。
F2A 也不能把 parent 直接交给 Solver：Solver 只读取物化并冻结后的 public child、public task/variant
contract 和 schemas，不能读取 parent、private lineage、mutation/authoring config 或 hidden oracle。

Legacy public v3 的 raw candidate universe 如下：

| 对象 | 数量 |
|:---|---:|
| Option daily quotes | 60,368 |
| `CONVERGED` authoring IV records | 59,836 |
| `NO_FINITE_IV` authoring records | 532 |
| Underlying/date slices | 1,430 |
| Underlying/date/expiry groups | 4,312 |
| 每个完整 expiry group 的 quotes | 14 |
| 全部 14 条 quote 均可反解 IV 的 expiry groups | 3,983 |

这些 legacy IV audit 数字只用于理解旧数据规模，不是当前 authoring 输出，也不是新任务 truth。
当前 pipeline 已退休 authoring-time IV solving；普通 IV truth 必须由 Trusted verifier 从实际
canonical mid 重算。最终可发布任务还必须通过模型定义域、根存在性、数值稳定性、权限隔离和
canonical replay 等质量门。

现有基础 task manifest 仍为 `DRAFT` 且 `publication_eligible=false`。当前
[`task.schema.json`](../../../schemas/task.schema.json) 主要覆盖 task identity；Solver、
verifier、submission、trajectory 和训练数据闭环仍需实现。F2A 已有并行 v2 schemas/configs 和
repo-contract tests，但没有七维 Python dispatch、child、task manifest、oracle、Solver/verifier 或
dataset；frozen parent 不等于端到端任务已经完成。

## 2. 第一阶段数学合同

### 2.1 概率测度、状态与信息

第一批 option pricing、IV 和 Greek tasks 只使用风险中性测度
`Q = USD-MONEY-MARKET-Q-v1`，其 numeraire 为
`USD-MONEY-MARKET-ACCOUNT-v1`。在过滤概率空间

\[
(\Omega,\mathcal F,(\mathcal F_t)_{t\ge 0},Q)
\]

上，当前 valuation time 的条件信息 \(\mathcal F_t\) 包含：

- 当前 spot \(S_t\)；
- 合约的 strike、expiry、call/put、exercise 和 settlement conventions；
- 当前使用的 deterministic discount/dividend curves；
- task contract 明确公开的 deterministic pricing-volatility input；
- calendar、day-count、valuation timestamp 和 canonical quote。

第一版不使用 stochastic rate、stochastic volatility、jumps、regime switching 或
Q-measure multi-asset dependence。Task contract 在实现前必须明确 \(S_t\) 的经济含义；
BSM baseline 应把它声明为同币种 ex-dividend spot，而不能只写成含义不明的 `spot`。

### 2.2 Q-measure dynamics 与精确区间缩减

在第一版 deterministic time-inhomogeneous BSM baseline 中，Q-measure dynamics 为

\[
\frac{dS_u}{S_u}=(r(u)-q(u))\,du+\sigma_Q(u)\,dW_u^Q.
\]

当前 snapshot 的 rate/dividend shorthand 为 flat、continuously compounded curves。
对 valuation time \(t\) 和 expiry \(T\)，European vanilla pricing 使用

\[
\sigma_{Q,\mathrm{eff}}(t,T)
=\sqrt{\frac{1}{T-t}\int_t^T\sigma_Q^2(u)\,du}.
\]

这个 RMS volatility 是当前声明的 deterministic time-inhomogeneous GBM 对区间
\([t,T]\) 的精确 reduction，不是 endpoint-volatility 或平均 volatility 近似。

时间、单位和状态约定为：

- 时间轴：calendar time；
- day-count：Actual/365 Fixed；
- \(T-t\)：从 valuation date 到 expiry 的实际日历日数除以 365；
- \(r,q\)：年化 instantaneous continuously compounded rates，单位为 inverse year；
- \(\sigma\)：年化 instantaneous return standard deviation，单位为 inverse square-root year；
- price：合约声明 currency 下、每单位 underlying/notional 的价格；
- working dtype：float64，除非具体 method contract 另有声明。

### 2.3 P-measure history 的边界

现有 `underlying_daily` close paths 属于物理测度 P。它们不能把 physical drift 或
physical volatility 用于 option pricing，也不能把 P-measure dependence 当成
Q-measure joint pricing dependence。

第一批 tasks 不使用 P-measure history。未来若增加 historical return、VaR、ES 或
dependence-statistic tasks，必须另行冻结：

- P-measure stochastic process 与经济对象；
- conditioning state；
- actual calendar interval 和 observation grid 的关系；
- return definition、fixed risk horizon 和 quantile convention；
- published rounded close 作为 restart state 的现有离散 path law。

### 2.4 F2A 的概率空间、策略类与套利作用域

F2A 与普通 BSM 数值任务共享 parent 声明的 deterministic time-inhomogeneous BSM marginal、
`Q = USD-MONEY-MARKET-Q-v1`、money-market numeraire、实际非零 rate/dividend curves、
calendar/day-count 和 cash settlement conventions，但输出不是“是否偏离 BSM”。套利定义在物理测度
`P` 及其 null sets 下；`P ~ Q`、每段 full positive conditional support、volatility node 的
`2026-08-03T16:00:00Z` origin/calendar-day offsets/annualized units、以及 `t/T1/T2` settlement order
由 blocked successor public variant v2 显式声明，不追溯归因给 frozen parent 或 legacy variant。

单期限 cross-sectional/cross-asset candidate 的 horizon 是共同 expiry `T`。Blocked catalogue v3
中的 non-executable calendar target horizon 是较晚 expiry `T2`，较早 expiry `T1` 是公开的中间 settlement/
rebalancing time。Option 只在 valuation time 交易并持有到 cash settlement；underlying 的允许交易
时点和 predictable rule 由各 candidate template 冻结；cash account 按声明 curve 累计。策略必须
self-financing、terminal liquidation wealth 逐状态非负且 discounted wealth 有统一下界。

F2A 结论必须区分：

1. quote 相对一个选定 pricing model 的 mispricing/model inconsistency；
2. 考虑 executable sides 与全部费用后的 static/semi-static arbitrage certificate；
3. frozen F2A finite catalogue 内是否存在 positive exact certificate；
4. 完整 admissible strategy class 中的 full-market dynamic/replication arbitrage。

ORM truth 只对应第 3 项。`false/[]` 只表示 public variant 的有限 catalogue 没有 positive candidate，
不证明全市场 no-arbitrage；quote 偏离 BSM 也只直接建立第 1 项，不能自动升级成第 2--4 项。

## 3. 第一批任务族与 F2A 并行支线

Ordinary `F0` 首批任务只覆盖 BSM vanilla，并按由浅到深的顺序实现：

| Batch | Task family | Solver-visible input | Canonical output | 主要能力 |
|:---|:---|:---|:---|:---|
| B0 | `bsm_price_scalar_v1` | \(S,K,T,r,q,\sigma_{Q,\mathrm{eff}}\) 和合约约定 | 一个 price/status | BSM analytic pricing |
| B1 | `bsm_iv_scalar_v1` | \(S,K,T,r,q\) 和 canonical mid | IV/status | deterministic root finding |
| B2 | `bsm_greeks_scalar_v1` | 单合约与明确的 pricing volatility | Delta/Gamma/Vega/Theta/Rho | analytic Greeks 和单位换算 |
| B3 | `duckdb_bsm_iv_batch_v1` | Solver-only DuckDB、snapshot selector 和 task contract | 有序 IV result table | SQL、筛选、实现、执行与 artifact |
| B4 | `put_call_parity_audit_v1` | 同 valuation/expiry/strike 的 call/put 和 curves | parity error/status | join、discounting 和 invariant audit |
| B5 | `iv_term_structure_v1` | 同 valuation time 的多个 expiry slices | IV term-structure artifact | 多期限反演和期限结构 |

B0--B2 用于先冻结数学、方法、单位和 verifier。B3 在保持相同数值合同的前提下，
把数据入口升级为 DuckDB agent workflow。B4--B5 在基础闭环稳定之后进入下一批。
F2A 不是 B5 的自然升级，也不复用 IV authoring answer；它是以独立 frozen parent、七维 task-space
v2、point-mutation child 和有限套利 catalogue 构成的并行 D4 task family。

### 3.1 Price task

Price task 可以把 private authoring audit 中的 `q_effective_volatility` 投影成一个明确、
measure-qualified 的 task input。它在该 task 中是必要的 pricing parameter，不是隐藏答案。
Target 是由公开 contract 指定公式得到并按 quote precision canonicalize 的价格。

Price task 不得把 `physical_volatility` 当作未标记 measure 的通用 `volatility`。

### 3.2 IV task

IV task 的 target 必须从 Solver 实际看到的 canonical `mid` 反解，不能直接发布：

- latent `q_effective_volatility`；
- 未量化 theoretical price 对应的 volatility；
- 使用不同算法生成的 authoring-side QuantLib IV。

第一版建议固定：

- model：European BSM with continuous \(r,q\)；
- target quote：canonical `mid`；
- root bracket：`[1e-6, 4.0]`；
- method id：`bsm-bisection-float64-80-v1`；
- initial endpoint check：确认 target 位于两端价格之间；
- update rule：执行恰好 80 次 float64 bisection；
- stopping：不提前停止，不切换 Newton/Brent；
- result：第 80 次更新后的两个端点中点；
- invalid bracket：返回明确、canonical 的 failure status；
- canonicalization：固定小数位、round-half-even、禁止科学计数法和负零。

Authoring pipeline 不再求解或写入 IV。Feasibility gate 和 canonical answer 都由
Trusted verifier 从 canonical mid 执行同一个 80-step method；已有 schema 2.4
snapshot 的 legacy `market.option_pricing_audit.implied_volatility` 不能作为新任务答案。

### 3.3 Greek task

Greek task 必须明确：

- Delta/Gamma 是 spot Greeks；
- Vega 输出 raw \(\partial V/\partial\sigma\) 还是 per one volatility point；
- Theta 是 annual 还是 per calendar day；
- Rho 是 raw \(\partial V/\partial r\) 还是 per one percentage point；
- 计算 Greek 时固定哪些市场输入；
- 使用 analytic formulas 还是指定 finite-difference stencil。

第一版 B2 只做 analytic Greeks。Finite-difference Greeks 必须作为独立 variant，不能与
analytic method 混用同一个 method id。

### 3.4 F2A arbitrage-finding task

Blocked successor identity 为：

```text
task_family_id       = bsm_arbitrage_finding_f2a_v1
variant_id           = bsm-arbitrage-finding-f2a-v2
coordinates          = (L5, P0, M0, A0, D4, R3, F2A)
execution_contract   = us-options-underlying-5bps-options-flat-050-v2
candidate_catalogue  = bsm-f2a-candidate-catalogue-v3       # blocked
output_contract      = arbitrage-opportunity-type-trajectory-v3
```

Public execution contract 不是零交易成本市场：option 按 directional bid/ask 成交，每条 option 腿按
`abs(position)` 收取 `0.50 USD/contract/side`；underlying 每次成交按绝对 traded notional 收取
单边 `5 bps`；cash account 无交易费。费用是实际经济 cashflow，不是 verifier tolerance，也不能
为了目标 label 临时调整。

目标 catalogue 包含：

- `cross-sectional`：同到期 strike monotonicity 与 nonuniform strike convexity；
- `cross-asset`：discounted executable price bounds 与 executable put-call parity；
- `calendar`：transaction-cost-aware two-expiry terminal-spot bridge，允许在 `T1` 执行一次公开、
  有限、state-contingent underlying rebalance，并在 `T2` 检查 terminal certificate。

Calendar 不能用 raw same-strike maturity price ordering、quote 与 BSM theoretical value 的差、
frictionless dynamic replication、Monte Carlo path 或有限 spot grid 代替。目标 verifier 必须逐现金流
计入两段 underlying execution cost 与 option fees，再通过 piecewise-affine cell vertices、one-sided
boundaries 和 unbounded-cell tail slopes 证明 terminal wealth 在 \((0,\infty)^2\) 上非负。

Canonical type order 固定为 `cross-sectional`、`cross-asset`、`calendar`。目标 feasibility set 是：

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

每个 positive child 仍至多应用一个 logical option-quote point 或 spot point mutation。Private
authoring selector 按稳定 target/sign/tick 顺序搜索，只接受 realized signature 与 requested signature
完全相同、active/inactive 双边 guards 都通过的第一个 child；不可达时 deterministic skip。Trusted
verifier 必须从最终 public child 全量重扫所有 enabled families，不能读取或复制 requested signature。

Legacy [`bsm_arbitrage_finding_f2a_v1.json`](../../../configs/variants/bsm_arbitrage_finding_f2a_v1.json)
/ catalogue v2 与 dataset v1 保持 replay。Blocked successor [`variant v2`](../../../configs/variants/bsm_arbitrage_finding_f2a_v2.json)
/ catalogue v3、[`mutation v2`](../../../configs/mutations/f2a_point_v2.json)、
[`dataset v2`](../../../authoring/configs/f2a_dataset_v2.json) 与 lineage/submission v2 已使用新 ID；它冻结
candidate-specific predicate、single-expiry formulas、public support/timeline、selector shape 与 calendar
review spec，但明确 `runtime_enabled=false`、`calendar_family=null`。Calendar 完成后还必须再升
variant/catalogue identity。八种 signatures 是待 reachability audit 的 target，不是当前运行能力。
完整公式、operation order 与验收顺序以
[`f2a_arbitrage_finding_agent_task_plan.md`](../mutation/f2a_arbitrage_finding_agent_task_plan.md) 为准。

## 4. 数据与权限边界

### 4.1 Authoring databases

Ordinary tasks 的 legacy public v3 和 F2A 的独立 v2 parent 都只能作为 read-only source，不能原地
修改。Ordinary Authoring/Trusted verifier 可按 task contract 读取 legacy source 中的：

- `market.option_pricing_audit`；
- generation model/engine；
- P/Q latent functions；
- seed、RNG 和 random-stream provenance；
- 未量化 theoretical prices（若 source 中存在）；
- private quality-control diagnostics。

F2A 权限更窄：Authoring 可读取 frozen parent 并生成 child/private lineage；Trusted verifier 只能从
public child 和 public variant 独立复算，不得读取 parent、private lineage、mutation intention、
requested signature、authoring-side result 或 stored label。

默认 active development v3 是 `DRAFT` 且在当前 pipeline 下只读；不能 silent fallback 成普通任务的
frozen source，也不能替代 F2A parent。新 ordinary authoring 使用 config schema `1.6.0`、generator
`0.8.0` 和 v4 snapshot identity，写入新文件。F2A child 必须从 frozen v2 parent copy-on-write，使用
新的 child identity/revision，经 authoring gates 后再 freeze。

### 4.2 Solver-only database

在生产 task 前，需要构建独立的 solver-only DuckDB。它只能包含：

- task 明确允许的 underlying state；
- contract and quote inputs；
- public curves、calendar、day-count、currency、settlement 和 precision；
- task selector 所需的 stable business keys；
- task contract 明确公开的参数。

它不得包含：

- `market.option_pricing_audit`；
- reference answer 或 oracle columns；
- private generation model/engine；
- physical/pricing latent volatility functions，除非某个 price task 把具体
  measure-qualified effective volatility 明确列为输入；
- seed、RNG 或 random-stream provenance；
- hidden verifier tests 和 diagnostics。

只创建 `solver_visible` view 但仍让 Solver 打开包含 private tables 的同一个文件，不满足
安全边界。

F2A solver-only child 还必须排除 parent、private lineage、mutation config、authoring config、
requested/realized signature、selector failure history、family margins 和 stored reference answer。
Public task manifest 只引用 child snapshot id/revision、registry/rule 与 public contract IDs；task/child
identity 不编码 operator、target、signature 或 positive/negative status。

### 4.3 D0 与 D4 两种数据模式

同一个数学 task family 支持两种独立 variant：

- D0：task JSON 直接给出全部输入参数，不要求 Solver 查询数据库；
- D4：Solver 获得只读 solver-only DuckDB 和 selector，需要自行执行 SQL、排序和输出 artifact。

D0 用于验证数值合同；D4 用于验证完整 agent workflow。两者不能因为输入方式不同而改变
数学 truth 或 verifier method。当前 F2A family 固定为 D4；它不通过 D0 JSON 绕过完整 public child
扫描，也不把 parent 的 latent pricing truth 暴露成 task input。

## 5. Task artifact 合同

第一版至少需要以下对象：

### 5.1 Task variant

描述一个不可变任务族及其方法：

- `task_family_id`；
- ordinary v1 使用六维 coordinates `(L, P, M, A, D, R)`；F2A v2 使用
  `(L, P, M, A, D, R, F)`，且 `F` 是 string enum；
- compatibility rule id；
- market convention id；
- method contract id；
- output contract id；
- solver capability/denylist policy；
- verifier contract id；
- canonicalization id。

### 5.2 Task instance

描述从 snapshot 选择出的一个不可变实例：

- `task_id`；
- `variant_id`；
- `snapshot_id` 和 `snapshot_revision`；
- stable selectors，例如 valuation date、underlying id、option id 或 expiry；
- input projection/query id；
- expected row order；
- instance QC status；
- split/scenario group；
- 允许公开的 snapshot parentage/grouping reference（如适用）。

Task instance 不保存 public reference answer。F2A 的 mutation lineage 是 private authoring artifact，
不能进入 public task instance；旧六维 manifests 也不能在加载时被隐式补成 `F0`，否则会改变
serialization 和 deterministic identity。

### 5.3 Private reference run

只允许 verifier 读取：

- package/backend/version；
- method id 和完整 numerical settings；
- canonical answer；
- convergence/root/bracket diagnostics；
- method stability and replay evidence；
- verifier wrapper version。

### 5.4 Submission 与 verification report

Submission contract 固定字段、dtype、单位、row/key order 和 serialization。Verification
report 至少记录：

- schema/method/import checks；
- canonical exact-equality results；
- financial invariant checks；
- first failing assertion；
- terminal pass/fail 和二值 reward。

Trajectory schema 在 task/submission/verifier 闭环稳定后接入，不能先用人工 reasoning
文本代替真实 state--action--observation records。

F2A 仍提交完整 trajectory，但 ORM 只投影并 exact-compare：

```text
arbitrage_opportunity: bool
arbitrage_type: canonical ordered enum array
```

Verifier 必须检查 `arbitrage_opportunity == bool(arbitrage_type)`。F2A 不接受 `maximal_spread`，
也不要求 Solver 提交 mutation target、portfolio legs 或 hidden certificate；trajectory 文本的变化不能
改变同一 canonical ORM answer 的 reward。

## 6. Candidate selection 与质量门

### 6.1 Stable business selection

Candidate 必须通过 stable business keys 选择并按显式顺序输出。Task identity 至少由以下
声明字段确定：

- task variant id；
- snapshot id/revision；
- valuation date/timestamp；
- underlying id；
- option id 或 expiry/strike/call-put selector；
- method contract id；
- output contract id。

追加新 rows 或改变 SQL physical row order 不得改变既有 instance 的选择和随机采样结果。
F2A selector 还必须冻结 execution contract/profile、operator order、target enumeration、sign order 和
absolute integer-tick grid；这些字段参与 private replay/identity，但 public IDs 不得泄露最终 target、
requested signature 或 realized label。

### 6.2 普通 price/IV/Greek task QC

普通数值任务至少要求：

1. \(S>0\)、\(K>0\)、\(T>0\)；
2. rate/dividend/calendar/day-count 完整；
3. quote 满足相应 discounted BSM bounds；
4. IV target 位于声明 bracket 的 attainable price interval；
5. root 在 bracket 内唯一；
6. price/IV/Greek method 在目标 pinned environment 中可重放；
7. canonical output 不落在跨 backend/platform 不稳定的 rounding boundary 附近；
8. quote rounding 没有把 IV inversion 推入极端 ill-conditioned 区域；
9. output 无 NaN/Inf，status 与 failure behavior 明确；
10. Solver input 不泄露 canonical answer 或 latent pricing truth。

仅有 `iv_status = CONVERGED` 不足以成为发布条件。当前 raw snapshot 中存在 deep/intrinsic
options：八位 price rounding 可能造成很大的 IV 变化。Authoring QC 需要使用 bracket margin、
vega/conditioning 和 canonical replay 筛除不稳定普通题。

### 6.3 Failure-status tasks

现有 532 个 `NO_FINITE_IV` records 暂不混入普通 IV 数值任务。它们可以在基础闭环稳定后
形成独立 task family：

- 目标明确为 root-existence/status classification；
- 记录具体失败边界；
- 不伪造一个有限 IV；
- 不泄漏到普通 pricing、Greek 或 smile tasks。

### 6.4 F2A authoring 与 oracle gates

F2A 在物化首个 child 前至少要求：

1. parent path、DB metadata 和 manifest 都精确指向 F2A v2 `FROZEN / r1`，缺失时失败且不 fallback；
2. public variant version 化全部 executable sides、fee/cost、funding、curve、timeline、candidate
   formulas、position grids、dtype、cast points、enumeration/reduction order 与 predicate；
3. cross-sectional/cross-asset candidates 有独立的小市场 cashflow/self-financing proof tests；
4. calendar bridge 逐项验证两个 segment primitives、`T1` state-contingent rebalance、`T2`
   liquidation、cell boundaries 和 tail slopes；
5. authoring selector 对每个 requested signature 只接受 realized bitmask exact match，并让 active
   families 远离正边界、inactive families 远离触发边界；guard 只筛样本，不改变 exact predicate；
6. mutation 前 clean slice 重算为 `false/[]`，否则 deterministic skip；spot mutation 前后断言
   `X_after == X_before`，只有该 clean policy 才推出 accepted spot child 的 `X_after=false`；
7. authoring selector 与 Trusted verifier 不共享 oracle 实现；verifier 只读 public child/variant；
8. parent byte-identity 不变，child 使用新 identity、revision、manifest 和 private lineage；
9. public task/child/schema 不泄露 requested signature、mutation count、operator、target 或 selector trace；
10. Solver image 通过 dependency allowlist、network/dynamic-install/subprocess deny 与 import/runtime audit。

Blocked catalogue v3 也尚未满足 calendar proof、reachability 与 runtime gates，不能据此物化或发布 child。

### 6.5 Verifier robustness

每个 variant 至少进行以下 adversarial checks：

- 改变 canonical 数值最后一位必须失败；
- method id 错误必须失败；
- iteration count 错误或提前停止必须失败；
- Vega/Rho/Theta scaling 错误必须失败；
- row/key order 错误必须失败；
- 缺行、重复行和错误 option id 必须失败；
- Solver 使用禁用的 finance/IV/Greek API 必须失败；
- hidden oracle/private schema 对 Solver 可访问时，environment test 必须失败。

F2A 还必须拒绝：错 bool、缺失/错序/重复/错分 `arbitrage_type`、bool/type 不一致、旧 skeleton 下
不可达的 calendar type、额外 `maximal_spread`、错误 execution/candidate contract id、错误 child
revision，以及用 raw maturity ordering 或 model mismatch 冒充 executable certificate 的 submission。

## 7. 实施阶段

### 7.1 Ordinary F0 lane

#### 阶段 0：冻结设计合同

- 明确 ex-dividend spot、currency、settlement 和 quote definition；
- 冻结 B0--B2 的 method/output/canonicalization contracts；
- 明确 task variant 与 task instance 的边界；
- 为六维 v1 coordinates 选择兼容坐标并通过 registry；若在七维文档中描述则显式写 `F0`，不改写
  旧 manifest；
- 明确 authoring、Solver 和 verifier 的读取权限。

完成标准：同一个输入只对应一个声明方法下的 canonical output，不存在 measure、单位、
算法或 rounding 歧义。

#### 阶段 1：公共市场状态与私有 provenance 分离

- 从 frozen source 构建新的 solver-only export/projection，排除 DGP functions、engine、seed 和 RNG；
- 保留 curves、calendar、timestamp、currency、settlement 和 precision；
- 定义 solver-only DuckDB schema/export contract；
- 保持原 frozen authoring snapshot 不变。

完成标准：Solver 无法通过 SQL 或文件访问 private audit 和 latent answer information。

#### 阶段 2：Task schema 与 deterministic selector

- 扩展 task/variant/instance schema；
- 为 B0--B2 建立只读 candidate queries；
- 固定 selector、row order、instance id 和 lineage；
- 运行 domain/root/stability QC；
- 生成小型 candidate manifest。

完成标准：重复 authoring 得到相同 task instances 和排序，且 task manifest 不复制 oracle。

#### 阶段 3：Independent verifier 与 canonicalizer

- 建立 pinned package-backed verifier；
- 对齐 model、curve、day-count、root、Greek、dtype 和 operation order；
- 固定 Decimal quantization 和 serialization；
- 增加 verifier robustness tests；
- 确认 Solver 不能 import banned pricing packages。

完成标准：正确 submission byte-exact 通过，数值/方法/单位/schema 的定向错误全部被拒绝。

#### 阶段 4：小批量 end-to-end pilot

- 先生成约 100 个 B0--B2 tasks；
- 覆盖 call/put、多个 underlyings、expiries 和 moneyness bands；
- 运行 task -> Solver -> verifier -> outcome；
- 记录 pass/fail、first error 和 replay consistency；
- 修正 contract 或 QC，而不是放宽 hard verifier tolerance。

完成标准：固定环境重复运行得到相同 task set、canonical answers 和 verifier outcomes。

#### 阶段 5：DuckDB agent workflow

- 将已验证的 IV 数学合同升级为 `duckdb_bsm_iv_batch_v1`；
- Agent 使用 solver-only DuckDB 执行 SQL；
- 固定 query scope、row order、result schema 和 artifact paths；
- 保存真实 tool observations 和 execution artifacts；
- 接入 trajectory 与 terminal ORM outcome。

完成标准：Agent 能完成查询、筛选、数值实现、结果保存和 hidden verification，且无 private
data access。

#### 阶段 6：扩展与 dataset split

- 扩展到 parity、term structure 和 failure-status tasks；
- 再接 mutation 与 curriculum；
- 新增多个独立 frozen snapshots/scenarios；
- 按 snapshot/scenario group 做 train/validation/test split。

仓库虽有 legacy public v3、active development v3 与 F2A v2 等多个 identity，但它们不是足够多的
独立 latent market scenarios。普通任务仍不足以构建严格防泄漏的正式 benchmark；F2A children
更必须按共同 parent snapshot grouping，不能把同一 parent 的近邻 mutations 随机拆到不同 split。

### 7.2 F2A lane

F2A 不等待 ordinary B0--B5 全部完成，但必须沿自己的依赖顺序推进：

1. **已完成：parent 与并行 contract identities。** 保留 F2A v2 frozen parent；legacy v1/v2 skeleton
   不改，blocked successor variant v2/catalogue v3/mutation/dataset/lineage v2 已落位。
2. **完成 calendar proofs。** Blocked v3 已冻结 cash ledger、target grids、cell/boundary/ray order；仍需
   实现 exact evaluator 和逐现金流 self-financing/interim admissibility proof tests。完成后分配新的
   variant/catalogue ID；在此之前保持 calendar disabled。
3. **完成 deterministic reachability audit。** 逐 signature 输出 reachable target/operator/tick windows
   或不可达诊断；不可达时缩小发布 scope 或继续 blocking，不调整 parent/grid/fee。
4. **实现七维 runtime 与独立 oracle。** 保持六维 v1 serialization/IDs 不变；Verifier 从 public
   child/variant 重算三类 candidate 与 canonical bitmask，不导入 Solver 或 authoring selector。
5. **实现 mutation 与 child materializer。** `mutation/f2a.py` 只生成 immutable spec/identity/
   lineage；Authoring 以 copy-on-write 方式派生、门控和 freeze 新 child，不重新运行 QuantLib repricing。
6. **实现 Solver/verifier/environment。** Solver 手工枚举公开 catalogue；verifier 只投影 ORM answer；
   实际 dependency lock、DuckDB adapter、import/API gate 和 sandbox acceptance 全部通过后才发布 image。
7. **物化 feasibility/smoke 与 dataset。** 只覆盖 audit 已证明 reachable 的 scope；若七种 positives
   全部已证明才可把全覆盖写成 acceptance。随后生成 public task/split manifests 和 verified training
   export；private lineage、signature 和 selector traces 不导出。

任一步发现数学合同与现有 skeleton 冲突时，应 version config/schema/identity，而不是修改套利定义、
放宽 exact predicate 或把成本当成 tolerance。

## 8. 暂缓范围

以下任务不进入第一批：

### 8.1 Volatility smile

当前 deterministic Q-volatility 对固定 valuation/expiry 不随 strike 变化。同一期限的
横截面 IV 差异主要来自 canonical price rounding 和 inversion conditioning，不代表有意义
的生成 smile。第一版可以做 expiry term structure，但不应把量化噪声包装成 smile truth。

### 8.2 Q-measure multi-asset derivatives

当前 snapshot 中的 underlying dependence 属于 P-measure spot-path DGP。没有声明的
Q-measure multi-asset driver dependence 和 joint payoff pricing process，因此不能据此生成
basket、index、spread、best/worst-of 或 correlated option-pricing tasks。

### 8.3 OHLC/range tasks

当前 high/low 来自 separate synthetic range rule，并非从同一 intraperiod continuous path
或 bridge/range law 生成。第一批不得把它用于路径极值、barrier、realized range 或
intraday volatility tasks。

### 8.4 Volume/open-interest realism tasks

Volume、open interest 和 liquidity dynamics 还没有足以支撑真实市场微观结构推断的跨日
状态合同。第一批只可把它们视为显式给出的字段，不把它们当成 GBM output 或真实交易行为。

### 8.5 VaR/ES 与 historical risk

在实现前需要另行声明 P-measure return object、固定 horizon、周末区间处理、position
valuation、quantile convention、scenario order 和 interpolation。Business-daily rows 不能
被默认为等长 calendar-day transitions。

### 8.6 F2A 后续等级与未约束搜索

当前 F2A 不包含多点 mutation、`maximal_spread`/argmax、多个 pricing-model sources、真实交易所
完整 fee/margin/borrow/market-impact 规则，或超出 frozen two-expiry bridge catalogue 的 global
LP/PDE/Monte Carlo arbitrage search。扩展其中任何一项都需要新的 F level、candidate/variant identity
和相应 admissibility/normalization contract，不能原地扩大 F2A v1 的 truth scope。

## 9. 第一版完成定义

Ordinary F0 DuckDB agent-task pilot 只有同时满足以下条件才算完成：

- 原 authoring snapshot 保持 frozen；
- Solver 使用独立、只读、无 private tables 的 DuckDB；
- Task variant/instance 完整记录 snapshot id/revision 和六维 v1 坐标；
- Q measure、numeraire、state、clock、units 和 method 无歧义；
- IV truth 从实际 canonical mid 按指定算法重建；
- Solver 与 verifier 使用同一数学对象、算法、dtype、顺序和 canonicalization；
- Reference answer 与 hidden tests 对 Solver 不可见；
- task selection 和 answers 固定环境可重放；
- verifier 使用 exact canonical equality，不用 tolerance 放宽合同；
- failure-status samples 与普通数值任务隔离；
- pilot 的正确 submission 全部通过，定向错误 submission 全部被拒绝。

F2A pilot 另有独立完成定义：

- F2A v2 parent 始终保持 `FROZEN / r1` 且只读，所有 child 使用新 identity/revision；
- 新的 executable catalogue identity、execution contract、calendar evaluator/proofs、signature selector
  与全部 operation order 已 versioned；不得原地打开 blocked catalogue v3；
- 七维 v2 dispatch 不改变六维 v1 manifest serialization 或 deterministic IDs；
- deterministic reachability audit 已先证明发布 scope；若发布范围声称七种 positives，则七种都从
  public child 被 independent verifier 精确重算；
- option/underlying costs 在每个 candidate 的实际 cashflow 中逐腿、逐交易时点应用；
- `arbitrage_opportunity` 与 canonical ordered `arbitrage_type` exact match，且不接受
  `maximal_spread`；
- Solver 只能读取 public child/task/variant/schemas，无法访问 parent、private lineage、requested
  signature、authoring selector 或 hidden verifier；
- feasibility/smoke、integration、public、environment 与 verifier-robustness gates 全部通过；
- task/split manifests 按 parent snapshot grouping，training export 不含 private lineage/oracle traces。

## 10. 推荐的下一份具体产物

在当前 F2A branch 上，下一份产物应是**合同升级，不是 child materialization**：

1. 保持新落位的 blocked v2/v3 contracts 不变；
2. 实现 calendar exact evaluator、self-financing 与 interim admissibility proof tests；
3. 对 baseline execution profile 运行 deterministic signature reachability audit；
4. 根据 proof/audit 结果分配新的 executable variant/catalogue identity，并同步 lineage/submission；
5. 实现 independent verifier 与 authoring selector，确认 public artifacts 不泄露 private state；
6. 保持 parent byte-identity 不变，并继续禁止物化 child，直到上述 gates 全部通过。

Ordinary lane 的下一份具体产物仍是完整的 `bsm_iv_scalar_v1` method/input/output/verifier contract，
随后才复用同一数学 truth 构建 `duckdb_bsm_iv_batch_v1`。两条 lane 可以并行评审，但不能共享 hidden
answer、identity 或为方便实现而互相替换 snapshot。
