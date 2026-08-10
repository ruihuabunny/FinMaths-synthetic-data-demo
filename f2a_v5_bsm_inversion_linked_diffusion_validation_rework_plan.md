# F2A v5.1：BS Inversion + Linked-Diffusion Validation 完成记录与发布待办

## 0. 目标、范围与当前状态

本文记录 [Issue #2](https://github.com/ruihuabunny/FinMaths-synthetic-data-demo/issues/2)
对应的本地实现结果，并列出仍未满足的 release 条件。它不再把已落地的 v5.1 runtime、schema、
测试和 pilot 描述成未来工作；Issue comment、PR 状态与 Issue closure 属于仓库外协作状态，不能由
本地 Markdown 推断。

| 范围 | 当前状态 |
|:---|:---|
| Stage-2 contract/code | 已完成：逐 row market-IV inversion 与 linked-diffusion validation 已拆分。 |
| Output/schema | 已完成：schema `5.1.0`、output contract v2、`submission-v5.1.schema.json`。 |
| Solver/verifier | 已完成 reference 实现与 V0/V1/V2/V3 exact comparison；生产 sandbox 尚未完成。 |
| Authoring | 已完成 pilot package、显式 invalid-row handling、private audits 与独立 v4 execution audit。 |
| Release | 未完成：`011` 不可达，单-seed FP/FN/localisation/ambiguity diagnostics 不过门，且无 gate-passing 2,000+ cohort。 |

返修后的冻结决定如下：

1. 删除所有把 BSM \(d_1,d_2\)、\(N(d_1),N(d_2)\) 或两个 discounted price terms 表述成 regression targets/free coefficients 的内容。
2. 对每条 solver-visible option midpoint 执行固定的逐 row BSM implied-volatility inversion，得到 market-implied IV。
3. 继续使用 Stage-1 拟合的 piecewise-linear diffusion nodes，通过精确 integrated variance 构造 linked-diffusion BSM counterfactual。
4. market-IV repricing 不得作为 counterfactual；X/U/T model signal 仍由 observed executable quote 与 linked counterfactual 的差异定义。
5. \(d_1,d_2\) 只保留为由 volatility 与公开定价输入确定性派生的 diagnostics，永远不参与自由回归。
6. v4 executable-arbitrage oracle、candidate catalogue、cash-flow certificate 与结论保持不变。

---

## 1. 历史问题与当前实现

返修前的 `src/synthetic_derivatives/{solver,verifier}/f2a_v5_stage2.py` 并没有真正进行
option-pricing parameter fitting：

- `fit_option_series(...)` 的 docstring 明确说明其 `without fitting option volatility`；
- Stage 2 读取 Stage-1 的 `fitted_diffusion_node_values`；
- 通过 \(\widehat\beta^\top Q_{t,T}\widehat\beta\) 计算 integrated variance；
- 随后计算 BSM counterfactual price、\(d_1,d_2\)、parameter delta-method price SE 和 residual；
- `objective_value` 只是事后汇总的 standardized-residual SSE，没有被 optimizer 最小化；
- `fit_status="CONVERGED"` 因而不是合法的 estimator convergence claim。

因此，旧代码本质上已经接近 **linked-diffusion BSM counterfactual validation**，主要缺陷是文档、
接口、schema 与运行时字段把 validation 错称为 fitting/regression。

当前实现已经使用 `Stage2RowResult`、`OptionSeriesResult` 和 `evaluate_option_series(...)`，并把
`bsm_inversion_contract` 与 `linked_diffusion_validation_contract` 分开。Market 与 linked
`d1/d2` 都由各自 volatility object 和公开 pricing inputs 确定性派生；不存在自由的 pricing-term
coefficient 或 option-volatility optimizer。

---

## 2. 冻结后的数学合同

### 2.1 Stage 1：piecewise-linear instantaneous diffusion

令三个公开 node locations 上的 Stage-1 diffusion estimator 为

\[
\widehat\beta=(\widehat\beta_1,\widehat\beta_2,\widehat\beta_3),
\]

并令 \(\widehat\sigma_P(u;\widehat\beta)\) 按冻结的 calendar-time linear interpolation 与 flat extrapolation 定义。当前 generator 的显式 measure mapping 保持：

\[
\sigma_Q(u)=\sigma_P(u).
\]

该等式是当前 deterministic-diffusion baseline 的模型合同，不是 physical volatility 与 implied volatility 的一般恒等式。

### 2.2 Linked-diffusion integrated variance

对 valuation time \(t_i\) 和 expiry \(T_i\)，精确计算

\[
W_i^{\mathrm{link}}
=\int_{t_i}^{T_i}\widehat\sigma_P^2(u;\widehat\beta)\,du
=\widehat\beta^\top Q_{t_i,T_i}\widehat\beta.
\]

若一个线性 segment 的端点 diffusion 为 \(a,b\)，segment 长度为 \(\Delta t\)，则必须使用

\[
\int \sigma^2(u)\,du
=\Delta t\frac{a^2+ab+b^2}{3}.
\]

不得使用 endpoint volatility、arithmetic-average volatility 或对 annualized IV 做 piecewise-linear interpolation 来替代该积分。

linked effective annualized volatility 为

\[
\sigma_i^{\mathrm{link}}
=\sqrt{\frac{W_i^{\mathrm{link}}}{\tau_i}},
\qquad \tau_i=T_i-t_i.
\]

### 2.3 Linked BSM counterfactual

使用公开的 spot、strike、integrated rate、integrated dividend/carry 与 \(W_i^{\mathrm{link}}\) 计算

\[
d_{1,i}^{\mathrm{link}}
=\frac{
\log(S_i/K_i)+R_i-Q_i+\tfrac12W_i^{\mathrm{link}}
}{\sqrt{W_i^{\mathrm{link}}}},
\qquad
d_{2,i}^{\mathrm{link}}
=d_{1,i}^{\mathrm{link}}-\sqrt{W_i^{\mathrm{link}}}.
\]

由此得到

\[
P_i^{\mathrm{link}}
=\operatorname{BSM}
\left(S_i,K_i,R_i,Q_i,W_i^{\mathrm{link}},\text{call/put}\right).
\]

这里的 \(d_1,d_2\) 是 derived values，不是 regressors、targets、parameters 或 coefficients。

### 2.4 Market-implied BS inversion

visible option price 固定为

\[
P_i^{\mathrm{mid}}=\frac{P_i^{\mathrm{bid}}+P_i^{\mathrm{ask}}}{2},
\]

并逐 row 求解

\[
\widehat\sigma_i^{\mathrm{IV}}
=\operatorname{BSM}^{-1}
\left(P_i^{\mathrm{mid}};S_i,K_i,\tau_i,R_i,Q_i,\text{call/put}\right).
\]

market \(d_1,d_2\) 只能在求得 \(\widehat\sigma_i^{\mathrm{IV}}\) 后按公式派生：

\[
d_{1,i}^{\mathrm{mkt}}=d_1(\widehat\sigma_i^{\mathrm{IV}}),
\qquad
d_{2,i}^{\mathrm{mkt}}=d_2(\widehat\sigma_i^{\mathrm{IV}}).
\]

market IV 是一条 quote-specific inverse-pricing result，不等于 generator 的 instantaneous diffusion node，也不单独识别完整 BSM trajectory/surface。

### 2.5 Validation residual 与 model signal

Stage-2 price residual 保持

\[
r_i^P=P_i^{\mathrm{mid}}-P_i^{\mathrm{link}}.
\]

可以同时输出非评分诊断

\[
r_i^{\sigma}
=\widehat\sigma_i^{\mathrm{IV}}-\sigma_i^{\mathrm{link}},
\]

但 localisation 和 X/U/T 的经济 edge 继续在 price/cash-flow units 中定义，不能把 IV difference 直接当成 USD edge。

严禁使用

\[
P_i^{\mathrm{mid}}
-\operatorname{BSM}(\widehat\sigma_i^{\mathrm{IV}})=0
\]

作为 counterfactual residual。否则每个 bracket-valid quote 都会被自身的 IV 完全吸收，model signal 将退化为恒零，mutation localisation 也会失效。

### 2.6 对不同 truth 的影响边界

- v5 public-child model-based X/U/T signal：由 linked counterfactual 决定，必须保持其定义。
- v5 private FP/FN audit：比较 generator counterfactual truth 与 public linked signal；已重跑的
  single-seed diagnostic 暴露大量 FP/FN，不能再预设 bitmask 或 release gate 不变。
- v4 executable-arbitrage truth：只依赖 public bid/ask、costs 与 frozen cash-flow certificate；v5.1
  estimator 不参与该 oracle，但 mutation 前后的不同 public markets 可以得到不同 v4 signatures。
- market IV/Greeks：以实际 solver-visible midpoint 为输入，通过固定 inversion contract 派生。

---

## 3. 冻结的 BS inversion 数值合同

直接复用仓库现有 IV/Greeks 示例中的 method contract：

| 项目 | 冻结值 |
|---|---|
| Method ID | `bsm-bisection-float64-80-v1` |
| Input price | `bid_ask_midpoint` |
| Volatility bracket | `[1e-6, 5.0]` |
| Arithmetic | IEEE-754 binary64 |
| Iterations | 恰好 80 次 |
| Midpoint | `mid = (low + high) / 2`，按冻结顺序计算 |
| Update | 若 `BSM(mid) < observed_price`，更新 `low=mid`；否则更新 `high=mid` |
| Early stop | 禁止 |
| Newton/Brent fallback | 禁止 |
| Final root | `(low_80 + high_80) / 2` |
| Invalid bracket | 输出 `invalid_bracket`；禁止返回端点或最后一次中间值 |
| Canonical output | 完成全部内部计算后，按 v5 output precision 与 half-even 规则量化 |

当前额外要求：

1. inversion 前按当前 European continuous-carry BSM 合同检查 discounted lower/upper bounds；
2. bracket-invalid row 必须输出 `invalid_bracket`，不返回 market IV 或 market `d1/d2`，并从
   model-signal candidate scan 排除；它不会仅因此使 series 或 pilot authoring 失败；
3. schema/verifier 仍应能够明确拒绝伪造的 `converged`、错误 iteration count、错误 method ID 或非有限输出；
4. Solver 自行实现 analytic BSM price 与 bisection，不得调用 prebuilt IV API；
5. Trusted verifier 独立实现 analytic BSM price 与同一 bisection wrapper，不导入 Solver
   implementation；fixture、unit tests 与 pilot 必须验证 canonical exact equality。

---

## 4. 当前 Stage-2 数据流

| Layer | 输入 | 输出 | 是否参与 X/U/T |
|---|---|---|:---:|
| V1 Stage-1 fit | public underlying history + node locations | fitted drift/diffusion nodes、covariance | 是 |
| V2a market inversion | public option midpoint + BSM inputs | market IV、market \(d_1,d_2\)、status | 否，诊断/market-implied output |
| V2b linked validation | Stage-1 diffusion + exact \(Q_{t,T}\) | linked variance/vol、linked price、linked \(d_1,d_2\)、price SE、residual | 是 |
| V2c localisation | linked price residuals | mutation diagnosis | 是 |
| V3 signal scan | executable quotes + linked counterfactual + costs | post-cost X/U/T model signal | 是 |
| V_exec | public executable market + v4 catalogue | executable-arbitrage audit | 独立，不由 v5 estimator 改写 |

---

## 5. 接口与 schema 重命名

不得继续使用暗示 option calibration 实际发生的 `fit`/`fitted` 名称。

| 返修前名称 | 当前名称 |
|---|---|
| `PricingCounterfactualContract` | 拆为 `BSMInversionContract` 与 `LinkedDiffusionValidationContract` |
| `CounterfactualRow` | `Stage2RowResult` |
| `OptionFit` | `OptionSeriesResult` |
| `fit_option_series(...)` | `evaluate_option_series(...)` |
| top-level `option_fits` | `option_series_results` |
| `fitted_counterfactual_price` | `linked_counterfactual_price` |
| `fitted_counterfactual_price_se` | `linked_counterfactual_price_se` |
| `integrated_variance_by_row_id` | `linked_integrated_variance_by_row_id` |
| `average_volatility` | `linked_effective_volatility` |
| `d1_values_by_row_id` | 拆为 `market_d1_by_row_id` 与 `linked_d1_by_row_id` |
| `d2_values_by_row_id` | 拆为 `market_d2_by_row_id` 与 `linked_d2_by_row_id` |
| `optional_iv_diagnostics` | 删除；改为 required `market_implied_volatility_by_row_id` 与 `market_iv_status_by_row_id` |
| `objective_value` | `validation_residual_sse` |
| `fit_status` | `series_status`；`COMPLETE` 允许包含按合同标记并排除的 `invalid_bracket` rows |
| `fitted_clean_counterfactual_quotes` | `linked_clean_counterfactual_quotes` |
| signal `fitted_counterfactual_value` | `linked_counterfactual_value` |
| config `fitted_counterfactual_rule` | `linked_counterfactual_rule` |

每个 `OptionSeriesResult` 至少应包含：

- contract identity 与 canonical valuation row ordering；
- `market_implied_volatility_by_row_id`；
- `market_iv_status_by_row_id`；
- market \(d_1,d_2\) derived values；
- linked integrated variance/effective volatility；
- linked \(d_1,d_2\) derived values；
- linked counterfactual price 与 parameter SE；
- price residual 与 standardized residual；
- `validation_residual_sse`；
- `series_status`。

不得增加以下字段：

- free `d1_coefficient` / `d2_coefficient`；
- free coefficients multiplying discounted spot/strike terms；
- per-row IV 被命名为 shared pricing parameter；
- 使用 market IV repricing 得到的所谓 clean counterfactual。

---

## 6. 已落地的 versioning 决策

本次更改会改变 required output fields 与字段语义，不能静默复用旧 output contract。

- v5 task family 与 variant 的大版本 identity 保留；
- variant 当前 `schema_version` 为 `5.1.0`；
- `output_contract_id` 已从 `model-reconstruction-xut-full-trajectory-v1` 更新为
  `model-reconstruction-xut-full-trajectory-v2`；
- `schemas/submission-v5.1.schema.json` 已新增；旧 `submission-v5.schema.json` 仅用于历史 pilot replay；
- dataset config 已指向新 schema/output contract；
- 所有新 materialized task IDs、digests、public manifests 与 private lineage 必须由新合同重新生成；
- 不得用旧 task identity 包装新 schema 结果。

旧 `submission-v5.schema.json` 当前保留为历史 pilot replay 边界；新 authoring 只生成 v5.1，不能用
旧 task identity 包装新 schema 结果。

---

## 7. 文件级实现记录

### 7.1 核心 Solver / Verifier

#### `src/synthetic_derivatives/solver/f2a_v5_stage2.py`

- 已新增 solver-side `BSMInversionContract`；
- 已实现 analytic call/put BSM price 与固定 80-step bisection；
- 已保留 exact linked integrated variance 与 delta-method price uncertainty；
- 当前入口为 `evaluate_option_series`；
- 当前输出分离 market-IV results 与 linked-validation results；
- 已删除虚假的 option-fitting convergence 语义；
- Solver 不导入 verifier/authoring modules。

#### `src/synthetic_derivatives/verifier/f2a_stage2.py`

- 已独立实现同一 inversion contract；
- Verifier 不导入 Solver implementation；
- 已独立复算 market IV、derived \(d_1,d_2\)、linked counterfactual 与 residual；
- 已对 method ID、iteration count、status、ordering 与 canonical precision 做 exact verification。

#### `src/synthetic_derivatives/{solver,verifier}/f2a_v5.py`

- 已更新 contract loading、`evaluate_option_series` 调用与 top-level `option_series_results`；
- 已更新 V2 semantic-layer comparison；
- verifier digest 覆盖新字段与新 output-contract identity。

#### `src/synthetic_derivatives/{solver,verifier}/f2a_v5_model_signal.py`

- 输入类型已改为 `Stage2RowResult`；
- runtime 字段已使用 `linked_counterfactual_*`；
- market-IV repricing 不参与 signal；
- signal family/candidate enumeration、costs、strict edge rule 与 ordering 保持不变。

#### `src/synthetic_derivatives/verifier/f2a_contract.py`

- 已删除未被引用且命名误导的旧 quote type；没有增加无实际调用需要的 speculative replacement。

### 7.2 Public contract / database / authoring

#### `src/synthetic_derivatives/verifier/f2a_database.py`

- 已将旧的单一 counterfactual contract 拆成：
  - `bsm_inversion_contract`；
  - `linked_diffusion_validation_contract`；
- 已更新 solver-visible `f2a_contracts` rows 与 `load_public_contracts(...)`；
- 已移除旧 optional-IV/fitting 语义；
- measure、numeraire、currency、rate/carry integration 与 \(\sigma_Q=\sigma_P\) mapping 保持不变。

#### `src/synthetic_derivatives/authoring/f2a_v5.py`

- 当前 audit 入口为 `_estimator_audit`；
- Stage-2 gate 验证每条 row 的 status/value-map 一致性；`invalid_bracket` rows 明确排除，series 可保持
  `COMPLETE`，linked validation residual gate 仍按冻结 policy 执行；
- task prompt、private truth scan、localisation audit、uncertainty audit 与 canonical-answer paths 已更新；
- mutation selector 不通过 market-IV repricing 读取或泄漏 clean quotes；
- 已生成 v5.1 pilot canonical output 与单-seed FP/FN diagnostics。

#### `configs/variants/bsm_model_reconstruction_xut_signal_f2a_v5.json`

- 已更新到 schema version `5.1.0`；
- 已拆分 inversion + linked validation contracts；
- 已冻结 bisection method、bracket、iterations、dtype 与 invalid-bracket behavior；
- 当前规则名为 `linked_counterfactual_rule`，output contract 为 v2；
- model-signal costs、catalogue、canonical order 与 forbidden claims 保持不变。

#### `authoring/configs/f2a_dataset_v5.json`

- 已指向新 variant schema/output contract/submission schema；
- parent identity、8-underlying sampling、pilot reachability 与 cohort calibration policy 保持不变；
- private truth 仍是 generator counterfactual vs final child quotes，不是 market-IV repricing truth。

### 7.3 Schemas

#### `schemas/submission-v5.1.schema.json`（已新增）

- `option_fits` 改为 `option_series_results`；
- 强制 required market-IV maps/status maps；
- 分离 market 与 linked \(d_1,d_2\)；
- `validation_residual_sse` 取代 `objective_value`；
- `series_status` 取代 `fit_status`；
- mutation diagnosis/model signal 使用 `linked_*` 字段；
- 保持 `additionalProperties=false` 与 exact canonical contract。

#### `schemas/trajectory.schema.json`

- additive v5 semantic output 更新到新 key；
- 不修改冻结的 v4 ORM answer schema；
- description 明确 v5 是 model-relative signal，不是 executable-arbitrage truth。

#### `schemas/f2a-lineage-v5.schema.json`

- 当前 structure 保持 v5 lineage shape，并把 output-contract identity 更新为 v2；
- private truth/public signal 字段不得包含 market-IV repricing counterfactual。

### 7.4 文档（已更新）

本地文档更新范围包括：

- `README.md`
- `AGENTS.md`
- `f2a_node_based_dynamics_bsm_inversion_linked_validation_rework_handoff.md`
- `f2a_v5_full_trajectory_inversion_linked_validation_handoff.md`
- `f2a_v5_unified_full_trajectory_bootstrap_authoring_spec (1).md`

当前结果：

1. 旧 regression handoff 已由 inversion/linked-validation handoff 取代；
2. active contract 不再把 `d1/d2` 描述成 pricing-term regression targets；
3. 明确 annualized IV 是剩余期限上的 RMS volatility，不是 piecewise-linear instantaneous diffusion；
4. 明确 market inversion 与 linked counterfactual 的不同角色；
5. 标记旧的“逐 row IV 仅为 optional diagnostic”决定已被本 v5.1 合同取代：market IV 成为 required derived output，但仍不是 shared model estimator；
6. 普通 BSM/IV/Greeks 文档中正确的 \(d_1,d_2\) 公式不删除；只删除把它们当回归变量的表述。

Issue #2 的 decision comment 与 closure 仍是外部协作待办：应说明选择
**linked-diffusion validation** 作为 shared counterfactual estimator，并把逐 row BS inversion 作为
required market-implied output，而不是 counterfactual estimator。

---

## 8. 测试与 pilot 记录

### 8.1 `tests/unit/test_f2a_v5_stage2_inversion.py`（已新增）

当前覆盖：

1. call round-trip：给定 volatility 生成 price，再按固定合同 inversion，canonical IV exact match；
2. put round-trip；
3. call/put 在相同输入和 coherent prices 下恢复相同 IV；
4. 恰好 80 iterations，禁止 early stop；
5. invalid lower/upper bracket 返回规范 status；
6. 错 method ID、bracket、iteration count 被拒绝；
7. market \(d_1,d_2\) 由 recovered IV 确定；
8. linked \(d_1,d_2\) 由 linked integrated variance 确定；
9. 不存在 free \(d_1,d_2\) coefficients；
10. binary64 + half-even canonicalization 的边界案例。

### 8.2 `tests/unit/test_f2a_v5_stage2_signal.py`（已更新）

- 保留 call/put discounted parity；
- 保留 linked price-gradient finite-difference check；
- 新增“market IV 改变但 linked counterfactual 不被重置”的测试；
- 明确 market-IV repricing residual 为零，但该值不得传给 model-signal scanner；
- clean linked residual 下 signature 保持 `000`；
- mutation 后 post-cost edge 与 bit activation 仍按 linked counterfactual 计算。

### 8.3 `tests/unit/test_f2a_v5_contracts.py`（已更新）

- 校验新 config、output contract 与 schema identity；
- 保留 solver 不导入 verifier/authoring 的独立性检查；
- V2 nested perturbation path 改为 `option_series_results/.../linked_counterfactual_prices_by_row_id`；
- 对 market IV、status、market/linked \(d_1,d_2\) 和 nonfinite values 做拒绝测试。

### 8.4 Repo contract test（已更新）

Active-v5 scoped string/AST checks 当前验证：

- active F2A v5 文档与公共合同中不得出现把 \(d_1,d_2\) price terms 称为 regression targets 的表述；
- Stage-2 dataclasses/schema 中不得出现 free d1/d2 coefficient fields；
- `evaluate_option_series` 不得调用任何 optimizer 来拟合 d1/d2；
- 允许普通公式、pricing primitive 与 derived diagnostics 中出现 `d1`、`d2`。

### 8.5 Integration / end-to-end（本地 pilot 已运行）

2026-08-10 的 deterministic local pilot 使用 distinct frozen v5 parent，物化 8-underlying public/private
package；reference Solver 只读取 public DuckDB，trusted verifier 独立复算，submission schema 与
V0/V1/V2/V3、private lineage、独立 `V_exec_audit` 均通过。Public artifact 只包含 allowlisted relations，
未发布 node values、clean quotes、seeds、mutation lineage 或 private truth。

该 run 不是 release evidence：clean/child 分别有 761/760 个规范 `invalid_bracket` rows；Stage-1 与
Stage-2 estimator gates 通过，但 child localisation gate 与 active-signal ambiguity gate 未通过。
单-task slice diagnostic 有 1,007 个 FP slices，candidate diagnostic 有 139,995 FP 与 28 FN；这不是
2,000+ cohort confidence-bound report。Child 的独立 v4 execution audit 为 `110`；clean v4 audit 为
`000`，二者是 mutation 前后不同 public markets，不能称为“返修前后 audit 不变”。

---

## 9. 实施状态与剩余顺序

### Phase 1：先冻结合同

- [ ] 在 Issue #2 补充最终 decision comment（外部协作）。
- [x] 更新 variant JSON 与 output-contract identity。
- [x] 冻结 inversion numerical contract、field names 和 failure behavior。
- [x] 完成 schema v5.1。

### Phase 2：实现独立 Stage-2 engines

- [x] 实现 verifier-side inversion + linked validation。
- [x] 实现 solver-side 独立版本。
- [x] 加入 round-trip、bounds、derived-value 与 exact-canonical tests。
- [x] 用 `evaluate_option_series`、`OptionSeriesResult` 和 status/value-map 语义取代旧接口。

### Phase 3：接回 pipeline

- [x] 更新 solver/verifier orchestration。
- [x] 更新 model-signal scanner 的 linked field names。
- [x] 更新 public contracts/database loader。
- [x] 更新 authoring audits、task prompt、canonical answer 与 lineage paths。

### Phase 4：更新文档与 repo contracts

- [x] 以 inversion/linked-validation handoffs 取代旧 regression handoffs。
- [x] 更新 unified spec、full-trajectory handoff、README、AGENTS。
- [x] 添加 scoped forbidden-terminology test。

### Phase 5：完整验收

- [x] 运行 Stage-2/unit/contract targeted tests。
- [x] 当前合并工作树完整 pytest：`218 passed`。
- [x] 物化 pilot task 并运行 Solver/verifier exact match。
- [x] 重新生成 single-task FP/FN、localisation、uncertainty 与独立 v4 execution diagnostics。
- [ ] 修复或重新冻结造成大规模 FP、localisation failure 与 ambiguity failure 的 estimator/signal contract。
- [ ] 解决 `011` reachability 或明确 version 新的 release scope；不能虚构该 signature。
- [ ] 生成并通过至少 2,000 个 independent task seeds 的 private cohort report。
- [ ] 完成 production Solver adapter/sandbox acceptance。
- [ ] 全部 Issue acceptance criteria 满足后再关闭 Issue #2。

---

## 10. 明确的非目标

本次返修不做以下工作：

- 不从 option quotes joint-calibrate 新的 shared Q-volatility surface；
- 不对 annualized IV 本身做 piecewise-linear regression；
- 不通过 row-wise market IV 重建 clean counterfactual；
- 不改变 Stage-1 physical path estimator；
- 不改变 generator 的 exact piecewise-linear variance integration；
- 不改变 v4 executable-arbitrage catalogue/certificates；
- 不把 v5 model signal 改称 model-free arbitrage、NFLVR 或 executable-arbitrage proof；
- 不顺带重构不相关的 authoring、mutation、curriculum 或 legacy v1--v4 contracts。

如果未来需要从 option panel 恢复 shared diffusion nodes，应另开 variant，并拟合 total variance 或直接做 pooled price-space calibration：

\[
\tau_i(\widehat\sigma_i^{\mathrm{IV}})^2
\approx \beta^\top Q_{t_i,T_i}\beta,
\]

或

\[
\min_\beta\sum_i w_i
\left[
P_i^{\mathrm{obs}}
-\operatorname{BSM}
\left(\sqrt{\beta^\top Q_i\beta/\tau_i}\right)
\right]^2.
\]

该 estimator 会受到 mutation contamination、weights、vega 与 cross-fitting policy 影响，不应偷偷并入本次 v5.1 修复。

---

## 11. Definition of Done

本地 v5.1 contract 返修的完成状态：

- [x] Active F2A v5 docs/config/schema/code 不再声称存在 \(d_1,d_2\) pricing-term regression。
- [x] 逐 row market IV 按冻结的 80-step BS inversion contract 计算。
- [x] Linked counterfactual 由 Stage-1 piecewise-linear diffusion 的 exact integrated variance 得到。
- [x] Market 与 linked \(d_1,d_2\) 均为 derived values，且名称分离。
- [x] Market-IV repricing 没有进入 residual、localisation 或 X/U/T signal definition。
- [x] Solver、verifier、variant config、public contracts、schemas、authoring 与 active docs 使用 v5.1
  identity 和字段。
- [x] Local pilot 的 Solver/verifier canonical exact-match、schema validation、private-lineage validation 与
  public projection checks 通过。

Release/Issue closure 仍需满足：

- [x] 当前合并工作树 full pytest 通过（`218 passed`）。
- [ ] Localisation 与 active-signal ambiguity gates 通过，FP/FN 不再由当前失败 pilot 代替 cohort evidence。
- [ ] 发布 scope 的全部 signatures 具备冻结 grid 上的真实 reachability；当前 `011` 不可达。
- [ ] 至少 2,000 个 independent task seeds 的 private cohort 同时通过 family/task FP/FN confidence
  bounds、exact-signature accuracy 与 bootstrap maximum-statistic gate。
- [ ] Production Solver adapter、import/runtime audit 与 sandbox acceptance 完成。
- [ ] Issue #2 的外部 decision/closure 状态已由维护者确认。
