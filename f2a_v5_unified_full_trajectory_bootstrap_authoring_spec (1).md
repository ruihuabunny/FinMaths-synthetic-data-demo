# F2A v5.1 BS Inversion + Linked-Diffusion Validation 统一规范

## 0. 文档身份

本文是 active F2A v5.1 full-trajectory 的实现规范。它取代此前关于 Stage 2 的临时设计稿；
历史 v5 pilot schema 只用于只读 replay，新 authoring 只能生成 v5.1。

冻结身份如下：

- variant：`bsm_model_reconstruction_xut_signal_f2a_v5`；
- variant schema：`5.1.0`；
- public DuckDB schema：`f2a-public-duckdb-v5.1.0`；
- output contract：`model-reconstruction-xut-full-trajectory-v2`；
- submission schema：`schemas/submission-v5.1.schema.json`；
- production parent：`DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1`；
- independent execution audit：frozen v4 catalogue oracle。

V5.1 的 X/U/T 是 catalogue-scoped、post-cost、model-relative signal，不是 model-free
arbitrage、完整市场 `NFLVR` 结论或 executable-arbitrage certificate。V4 的现金流候选、交易成本、
calendar stock-flip certificate 和 executable conclusion 不由本规范改写。

## 1. 完整数据流

```text
public P-measure underlying history
  -> Stage 1 three-node physical estimator
  -> fitted instantaneous diffusion nodes and covariance

public solver-visible option midpoint
  -> fixed row-wise BSM inversion
  -> market IV and market d1/d2 diagnostics

Stage 1 diffusion + exact remaining-horizon integrated variance
  -> linked-diffusion BSM validation
  -> linked price/d1/d2/SE and price residual
  -> localisation and post-cost X/U/T model signal

public executable bid/ask market
  -> independent frozen v4 oracle
  -> catalogue-scoped execution audit
```

Market inversion 与 linked validation 是两个不同对象。Market-IV repricing 必须恢复 visible
midpoint；正因为该 residual 在有效 bracket 内按构造为零，它不得替代 linked counterfactual，
也不得进入 localisation 或 X/U/T scanner。

## 2. Stage 1：物理测度路径估计

### 2.1 概率模型

历史 ex-dividend spot close 在物理测度 `P` 下满足 deterministic time-inhomogeneous GBM baseline：

\[
\frac{dS_t}{S_t}=\mu_P(t)dt+\sigma_P(t)dW_t^P,
\qquad S_t>0.
\]

信息集在每次 transition 开始时包含前一个 published close、公开 node locations、日历和固定合同。
Drift 与 diffusion 是三个 shared public node locations 上的未知 node values；沿 calendar-day time
axis 做线性插值并在 support 外平坦外推。参数单位分别是 `Actual365Fixed` 年的 inverse time 与
inverse square-root time。

对任一 public close-to-close interval，conditional transition 使用精确 integrated drift 与 variance：

\[
\log(S_{i+1}/S_i)\mid\mathcal F_{t_i}
\sim N\left(
\int_{t_i}^{t_{i+1}}(\mu_P(u)-\tfrac12\sigma_P^2(u))du,
\int_{t_i}^{t_{i+1}}\sigma_P^2(u)du
\right).
\]

周末与跨 node interval 使用完整 calendar elapsed time。当前 likelihood 是明确冻结的 latent
Gaussian-transition quasi-likelihood；published `0.01 USD` restart-state quantization 不被误称为
连续密度。Stage-1 estimator、bounds、multistarts、stopping、tie-break、covariance 与 half-even
canonicalization 继续使用 public `physical_fitting_contract`。

### 2.2 P/Q 映射

当前 deterministic-diffusion baseline 明确声明：

\[
\sigma_Q(u)=\sigma_P(u).
\]

这是当前模型在 Girsanov drift change 下的显式约定，不是 physical volatility、pricing volatility
与 implied volatility 的一般恒等式。若未来改变 volatility state dynamics 或 volatility risk premium，
必须创建新 variant identity。

## 3. Stage 2A：逐 row market-implied BS inversion

### 3.1 输入与意义

每个 solver-visible row 的 observed price 固定为：

\[
P_i^{mid}=\frac{P_i^{bid}+P_i^{ask}}{2}.
\]

以该 row 的 spot、strike、live maturity、integrated rate、integrated dividend/carry 与 option type
求解 European continuous-carry BSM inverse problem。结果
`market_implied_volatility_by_row_id` 是 quote-specific market-implied quantity。

Annualized market IV 表示从 valuation time 到该 row expiry 的剩余期限 RMS volatility：

\[
\widehat\sigma_i^{IV}
=\sqrt{W_i^{IV}/\tau_i}.
\]

它不是某个 calendar instant 上的 instantaneous diffusion，不是 Stage-1 node height，也不是跨 rows
共享的 pricing parameter。不得对 annualized IV 做线性插值后声称得到了原 piecewise-linear
instantaneous diffusion。

### 3.2 固定数值合同

`bsm_inversion_contract` 冻结以下规则：

| 项目 | 值 |
|---|---|
| method | `bsm-bisection-float64-80-v1` |
| bracket | `[1e-6, 5.0]` |
| arithmetic | IEEE-754 binary64 |
| iterations | 恰好 80 次 |
| midpoint | `(low + high) / 2` |
| update | `price(mid) < observed` 时更新 `low`，否则更新 `high` |
| early stop | 禁止 |
| fallback | 禁止 |
| final root | `(low_80 + high_80) / 2` |
| invalid result | `invalid_bracket`，不返回端点或中间值 |
| invalid-row policy | 记录 `invalid_bracket`，并从 v5 model-signal candidates 中排除 |

求根前必须检查 call/put 的 discounted lower/upper bounds，再检查 finite volatility bracket 两端的
attainable prices。Solver 自行实现 analytic price 与 bisection；trusted verifier 使用独立实现。
失败时不得发布 `NaN/Inf`、伪造端点或最后一次 midpoint；该 row 不使 authoring 失败，但不进入
v5 X/U/T model-signal candidate enumeration。独立 v4 executable audit 仍按 public bid/ask
全量扫描，因为其 certificate 不依赖 IV。所有内部运算完成后才按十位小数、half-even 量化。

Market `d1`、`d2` 只能由 recovered IV 与同一 row 的公开 pricing inputs 确定性派生。它们不是
targets、regressors、自由参数或可调整 coefficients。

## 4. Stage 2B：linked-diffusion BSM validation

对 valuation time \(t_i\) 与 expiry \(T_i\)，由 Stage-1 diffusion estimator
\(\widehat\beta\) 计算：

\[
W_i^{link}
=\int_{t_i}^{T_i}\widehat\sigma_P^2(u;\widehat\beta)du
=\widehat\beta^\top Q_{t_i,T_i}\widehat\beta.
\]

若一个 linear segment 两端 diffusion 为 \(a,b\)，长度为 \(\Delta t\)，精确积分是：

\[
\Delta t\frac{a^2+ab+b^2}{3}.
\]

不得以 endpoint、arithmetic-average volatility 或 interpolated annualized IV 替代 squared-diffusion
积分。Linked effective annualized volatility 是：

\[
\sigma_i^{link}=\sqrt{W_i^{link}/\tau_i}.
\]

在 `USD-MONEY-MARKET-Q-v1`、`USD-MONEY-MARKET-ACCOUNT-v1`、USD、16:00 UTC 与同一
flat continuous rate/carry context 下：

\[
d_{1,i}^{link}
=\frac{\log(S_i/K_i)+R_i-Q_i+\tfrac12W_i^{link}}
{\sqrt{W_i^{link}}},
\qquad
d_{2,i}^{link}=d_{1,i}^{link}-\sqrt{W_i^{link}}.
\]

由此得到 `linked_counterfactual_price`。Stage-1 diffusion covariance 通过完整 shared-node gradient
传播为 `linked_counterfactual_price_se`。Validation price residual 固定为：

\[
r_i^P=P_i^{mid}-P_i^{link}.
\]

Standardized residual 的 denominator 是 quote-noise scale 与 linked parameter variance 的平方和
开根号。`validation_residual_sse` 只是 validation summary；它不是 optimizer objective，也不产生
Stage-2 estimator-convergence claim。Published option series 的状态是 `COMPLETE`。

Linked `d1`、`d2` 只由 \(W_i^{link}\) 与公开 pricing inputs 派生，不是自由 coefficients。

## 5. Localisation 与 X/U/T signal

Localisation 只读取 linked price residual 与冻结 threshold，并按经济 quote point 分组。输出 clean
reference 字段为 `linked_clean_counterfactual_quotes`。

Model-signal scanner 先应用 `market_iv_eligibility_rule=converged_rows_only`，再读取 eligible
`Stage2RowResult.linked_counterfactual_price` 与 shared-node price gradient。它按原 v5 candidate
catalogue、双方向 stable IDs、canonical `(X,U,T)` 顺序扫描，并计入：

- bid/ask half-spread hurdle；
- 每个 option leg 的 multiplier；
- 每 contract、每 side 的 `0.50 USD` fee；
- frozen underlying proportional cost；
- public funding 与 carry context；
- strict post-quantization `net_signal_edge > 0` boundary。

输出字段 `linked_counterfactual_value` 以 USD candidate-notional units 表示。Market IV 与 market
`d1/d2` 不进入这些 cash-flow-unit calculations。

## 6. V5.1 submission contract

Top-level key 是 `option_series_results`。每个 series 至少包含：

- canonical `valuation_row_ids`；
- `bsm_inversion_method_id` 与 `bsm_inversion_iteration_count`；
- `market_implied_volatility_by_row_id` 与 `market_iv_status_by_row_id`；前者只包含 converged rows，
  后者覆盖全部 valuation rows 并允许 `converged/invalid_bracket`；
- `market_d1_by_row_id`、`market_d2_by_row_id`；
- `linked_integrated_variance_by_row_id`；
- `linked_effective_volatility_by_row_id`；
- `linked_d1_by_row_id`、`linked_d2_by_row_id`；
- linked price、parameter SE、price residual、standardized residual maps；
- `validation_residual_sse` 与 `series_status=COMPLETE`；这里 COMPLETE 表示所有 rows 已按固定
  procedure 得到明确 status，不表示每条 row 都存在 finite bracket root。

Schema 使用 `additionalProperties=false`。它不允许自由 `d1/d2` coefficients、以 per-row IV 冒充
shared estimator、非有限数或旧 v5 output fields。Verifier 在 V2 对全部 nested fields 做 canonical
exact comparison，digest 覆盖 v2 output identity 与全部新字段。

旧 `schemas/submission-v5.schema.json` 只保留给历史 pilot replay。生产 authoring 复制并验证
`schemas/submission-v5.1.schema.json`，新 task/snapshot identity 由新 contract rows 重新生成。

## 7. Public/private boundary

Public package 可以包含：

- solver-visible underlying history；
- bid/ask option quotes；
- option contracts、spot、rate/carry inputs；
- drift/diffusion node locations；
- physical、inversion、linked validation 与 model-signal contracts。

Public package 不得包含 node values、generator clean quotes、seeds、mutation lineage、requested
signature、private truth、FP/FN flags 或 canonical answer。Market IV 是 Solver 必须从 visible midpoint
恢复的输出，不由 authoring 预先写入 DuckDB。

## 8. Authoring gates

V5 task generation 默认是 pilot。Authoring boundary 至少要求：

1. production 明确选择 frozen v5-specific parent，不 fallback 到 legacy v3；
2. Stage-1 required underlyings 状态全部为 `CONVERGED`；
3. 每个 option row 得到 `converged` 或 `invalid_bracket`，且 invalid rows 不输出非有限 IV；
4. market IV、market `d1/d2` maps 的 keys 精确等于 converged row set；
5. 每个 option series 为 `COMPLETE`，invalid rows 从 v5 model-signal scan 确定性排除；
6. linked validation residual gate 按 clean/child frozen policy 通过；
7. mutation localisation 与 eligible public/private X/U/T scans 仍使用 linked counterfactual；
8. reference Solver 与 trusted verifier exact-match；
9. independent v4 execution audit 仍全量扫描并单独记录，不参与 v5 scored label。

Release 还必须显式提供 gate-passing、2,000+ independent task-seed cohort report，并重新检查
task/family FP/FN confidence bounds、exact-signature accuracy 与 bootstrap maximum statistic。当前
126-business-day、三节点配置仍是 pilot；在 frozen mutation tick grid 上 `011` parent-wide
unreachable，runnable scope 是 `001/010/100/101/110/111`。

## 9. Verification matrix

| Layer | Canonical verifier target |
|---|---|
| V0 | public identity、schema、contract rows、domain 与 leakage boundary |
| V1 | public history 上的 Stage-1 physical estimator |
| V2 | row-wise inversion、market diagnostics、linked validation 与 localisation |
| V3 | public quotes 对 linked counterfactual 的 post-cost X/U/T model signal |
| V_exec | independent frozen v4 executable catalogue audit |

Solver 不得导入 verifier/authoring modules；verifier 不得导入 Solver implementation。两侧独立实现
BSM price、fixed bisection、linked variance 与 canonical serialization，并在 fixtures 与 pilot rows 上
exact-match。

## 10. 非目标

本版本不做 joint Q-volatility surface calibration，不拟合 annualized IV curve，不从 market-IV
repricing 构造 clean market，不改变 Stage-1 stochastic law，不改变 v4 catalogue/certificates，也不把
model mismatch 提升为完整市场 arbitrage 结论。

若未来需要从 option panel 恢复 shared diffusion nodes，必须创建新 estimator 与新 variant identity，
显式冻结 pooled total-variance 或 price-space objective、weights、cross-fitting 与 mutation-contamination
policy；不得把该工作静默并入 v5.1。
