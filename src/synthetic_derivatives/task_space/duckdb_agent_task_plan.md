# 基于现有 DuckDB 合成 Agent Task 的实施计划

## 1. 目标与当前结论

本计划使用已冻结的
`snapshots/public/quantlib_bsm_smoke_v1.duckdb` 作为第一版 agent task 的
authoring 数据源，逐步建立以下闭环：

```text
Frozen DuckDB snapshot
  -> deterministic task selection
  -> immutable task instance
  -> solver-visible input
  -> candidate submission
  -> independent hidden verifier
  -> verification outcome / trajectory
```

现有 DuckDB 足够支持第一版 Black--Scholes--Merton（BSM）vanilla task pilot，
但不能原样作为 Solver 的数据库。它同时包含 `solver_visible` views、private
`market.option_pricing_audit` 和 authoring provenance；数据库内的 view 不是权限边界。

当前快照的可用规模如下：

| 对象 | 数量 |
|:---|---:|
| Option daily quotes | 60,368 |
| `CONVERGED` authoring IV records | 59,836 |
| `NO_FINITE_IV` authoring records | 532 |
| Underlying/date slices | 1,430 |
| Underlying/date/expiry groups | 4,312 |
| 每个完整 expiry group 的 quotes | 14 |
| 全部 14 条 quote 均可反解 IV 的 expiry groups | 3,983 |

这些数字只是 raw candidate universe。最终可发布任务还必须通过模型定义域、根存在性、
数值稳定性、权限隔离和 canonical replay 等质量门。

现有基础 task manifest 仍为 `DRAFT` 且 `publication_eligible=false`。当前
[`task.schema.json`](../../../schemas/task.schema.json) 主要覆盖 task identity；Solver、
verifier、submission、trajectory 和训练数据闭环仍需实现。

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

## 3. 第一批任务族

首批任务只覆盖 BSM vanilla，并按由浅到深的顺序实现：

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

现有 `market.option_pricing_audit.implied_volatility` 可用于 authoring feasibility audit，
但若其方法与本 task method 不同，就不能作为 canonical task answer。Trusted verifier
必须从 canonical mid 重新执行相同的 80-step method。

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

## 4. 数据与权限边界

### 4.1 Authoring database

原始 frozen DuckDB 继续作为 authoring source，允许 authoring/verifier 读取：

- `market.option_pricing_audit`；
- generation model/engine；
- P/Q latent functions；
- seed、RNG 和 random-stream provenance；
- 未量化 theoretical prices；
- private quality-control diagnostics。

原始 frozen snapshot 不做原地修改。

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

### 4.3 D0 与 D4 两种数据模式

同一个数学 task family 支持两种独立 variant：

- D0：task JSON 直接给出全部输入参数，不要求 Solver 查询数据库；
- D4：Solver 获得只读 solver-only DuckDB 和 selector，需要自行执行 SQL、排序和输出 artifact。

D0 用于验证数值合同；D4 用于验证完整 agent workflow。两者不能因为输入方式不同而改变
数学 truth 或 verifier method。

## 5. Task artifact 合同

第一版至少需要以下对象：

### 5.1 Task variant

描述一个不可变任务族及其方法：

- `task_family_id`；
- 六维 coordinates `(L, P, M, A, D, R)`；
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
- mutation lineage（如适用）。

Task instance 不保存 public reference answer。

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

### 6.4 Verifier robustness

每个 variant 至少进行以下 adversarial checks：

- 改变 canonical 数值最后一位必须失败；
- method id 错误必须失败；
- iteration count 错误或提前停止必须失败；
- Vega/Rho/Theta scaling 错误必须失败；
- row/key order 错误必须失败；
- 缺行、重复行和错误 option id 必须失败；
- Solver 使用禁用的 finance/IV/Greek API 必须失败；
- hidden oracle/private schema 对 Solver 可访问时，environment test 必须失败。

## 7. 实施阶段

### 阶段 0：冻结设计合同

- 明确 ex-dividend spot、currency、settlement 和 quote definition；
- 冻结 B0--B2 的 method/output/canonicalization contracts；
- 明确 task variant 与 task instance 的边界；
- 为六维 coordinates 选择兼容坐标并通过 registry；
- 明确 authoring、Solver 和 verifier 的读取权限。

完成标准：同一个输入只对应一个声明方法下的 canonical output，不存在 measure、单位、
算法或 rounding 歧义。

### 阶段 1：公共市场状态与私有 provenance 分离

- 从当前 `solver_visible.pricing_metadata` 移除 DGP functions、engine、seed 和 RNG；
- 保留 curves、calendar、timestamp、currency、settlement 和 precision；
- 定义 solver-only DuckDB schema/export contract；
- 保持原 frozen authoring snapshot 不变。

完成标准：Solver 无法通过 SQL 或文件访问 private audit 和 latent answer information。

### 阶段 2：Task schema 与 deterministic selector

- 扩展 task/variant/instance schema；
- 为 B0--B2 建立只读 candidate queries；
- 固定 selector、row order、instance id 和 lineage；
- 运行 domain/root/stability QC；
- 生成小型 candidate manifest。

完成标准：重复 authoring 得到相同 task instances 和排序，且 task manifest 不复制 oracle。

### 阶段 3：Independent verifier 与 canonicalizer

- 建立 pinned package-backed verifier；
- 对齐 model、curve、day-count、root、Greek、dtype 和 operation order；
- 固定 Decimal quantization 和 serialization；
- 增加 verifier robustness tests；
- 确认 Solver 不能 import banned pricing packages。

完成标准：正确 submission byte-exact 通过，数值/方法/单位/schema 的定向错误全部被拒绝。

### 阶段 4：小批量 end-to-end pilot

- 先生成约 100 个 B0--B2 tasks；
- 覆盖 call/put、多个 underlyings、expiries 和 moneyness bands；
- 运行 task -> Solver -> verifier -> outcome；
- 记录 pass/fail、first error 和 replay consistency；
- 修正 contract 或 QC，而不是放宽 hard verifier tolerance。

完成标准：固定环境重复运行得到相同 task set、canonical answers 和 verifier outcomes。

### 阶段 5：DuckDB agent workflow

- 将已验证的 IV 数学合同升级为 `duckdb_bsm_iv_batch_v1`；
- Agent 使用 solver-only DuckDB 执行 SQL；
- 固定 query scope、row order、result schema 和 artifact paths；
- 保存真实 tool observations 和 execution artifacts；
- 接入 trajectory 与 terminal ORM outcome。

完成标准：Agent 能完成查询、筛选、数值实现、结果保存和 hidden verification，且无 private
data access。

### 阶段 6：扩展与 dataset split

- 扩展到 parity、term structure 和 failure-status tasks；
- 再接 mutation 与 curriculum；
- 新增多个独立 frozen snapshots/scenarios；
- 按 snapshot/scenario group 做 train/validation/test split。

当前只有一个 latent market snapshot，因此它适合开发和 pilot，不足以构建严格防泄漏的
正式 train/validation/test benchmark。

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

## 9. 第一版完成定义

第一版 DuckDB agent-task pilot 只有同时满足以下条件才算完成：

- 原 authoring snapshot 保持 frozen；
- Solver 使用独立、只读、无 private tables 的 DuckDB；
- Task variant/instance 完整记录 snapshot id/revision 和六维坐标；
- Q measure、numeraire、state、clock、units 和 method 无歧义；
- IV truth 从实际 canonical mid 按指定算法重建；
- Solver 与 verifier 使用同一数学对象、算法、dtype、顺序和 canonicalization；
- Reference answer 与 hidden tests 对 Solver 不可见；
- task selection 和 answers 固定环境可重放；
- verifier 使用 exact canonical equality，不用 tolerance 放宽合同；
- failure-status samples 与普通数值任务隔离；
- pilot 的正确 submission 全部通过，定向错误 submission 全部被拒绝。

## 10. 推荐的下一份具体产物

下一步先冻结 `bsm_iv_scalar_v1` 的完整设计，不立即批量生成任务。该设计应包含：

1. 一个 task variant 示例；
2. 一个从 DuckDB business key 选出的 task instance 示例；
3. 完整 input schema；
4. `bsm-bisection-float64-80-v1` method contract；
5. submission/output schema；
6. private verifier manifest；
7. canonicalization 和 failure-status examples；
8. solver-only input projection；
9. authoring QC checklist。

该 scalar 合同通过评审和 verifier pilot 后，再复用同一数学 truth 构建
`duckdb_bsm_iv_batch_v1`，避免同时调试数据库权限、SQL、root method 和 output
canonicalization 多个边界。
