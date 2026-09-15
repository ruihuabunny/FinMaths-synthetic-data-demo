# Synthetic BSM Agent Task：完整 Curriculum

> 实现状态（2026-08-12）：本文是目标 curriculum，不是当前可运行 task catalogue。
> 这里的 L0--L8 是本文件的能力梯度，不等于仓库七维坐标中的 reasoning axis `L`。当前代码
> 已实现 analytic BSM price/Greeks、scalar visible-price IV、market-implied analytic Greeks
> 及其独立 verifier；checked-in agent package 只覆盖 L2 型 composite task。L3 只有
> `solver/mc` 空骨架，L4--L8 尚无 task contract、solver、verifier、package 或数据制品。

## 1. 文档目标

本文给出 Synthetic BSM Agent Task 的完整能力梯度，覆盖：

1. BSM analytic pricing；
2. analytic implied volatility；
3. analytic market-implied Greeks；
4. Monte Carlo pricing 与 Greeks；
5. \(P\) 测度下 piecewise-linear diffusion 与相关矩阵校准；
6. delta-normal analytic VaR/ES；
7. underlying portfolio Monte Carlo VaR/ES；
8. option portfolio full-revaluation Monte Carlo VaR/ES。

核心设计原则是严格分离两条统计链路：

\[
\underbrace{\text{option quote}\rightarrow\sigma_{\mathrm{imp}}\rightarrow
\text{pricing/Greeks}}_{Q\text{ measure}}
\]

与

\[
\underbrace{\text{historical returns}\rightarrow
\widehat\sigma^P(t),\widehat R\rightarrow
\text{risk scenarios}\rightarrow\text{VaR/ES}}_{P\text{ measure}}.
\]

期权组合的 full-revaluation VaR/ES 是两条链路的汇合点：用 \(P\) 测度生成风险情景，再用 \(Q\) 测度的 BSM 对期权进行情景重估。

---

## 2. 统一建模约定

### 2.1 \(P\) 测度下的 underlying generator

对资产 \(i\)：

\[
\frac{dS_{i,t}}{S_{i,t}}
=\mu_i^P(t)\,dt+\sigma_i^P(t)\,dW_{i,t}^P,
\qquad
dW_{i,t}^P dW_{j,t}^P=\rho_{ij}\,dt.
\]

其中：

- drift node dates 与 drift node values 直接提供给 agent；
- diffusion node dates 直接提供；
- diffusion node values 由 agent 从历史收益拟合；
- \(\sigma_i^P(t)\) 在 nodes 之间线性插值；
- 相关矩阵由标准化 residual 估计，再按指定算法投影为 PSD correlation matrix；
- 基础 curriculum 不要求从 65 日单路径估计 drift，也不要求识别 change points。

短期 VaR/ES 的最低难度变体可直接规定 \(\mu_i^P(t)=0\)。非零 drift 变体必须把 drift nodes 作为已知输入。

### 2.2 \(Q\) 测度下的 BSM pricing model

在 constant implied volatility 的 BSM 模型下：

\[
\frac{dS_{i,t}}{S_{i,t}}
=(r_t-q_{i,t})\,dt+\sigma_{\mathrm{imp}}\,dW_{i,t}^Q.
\]

这里的 volatility 是由市场报价反解得到的 quote-level implied volatility，不是 \(P\) 测度下的 piecewise-linear diffusion。

### 2.3 Drift 的处理原则

当前项目不把无条件 drift estimation 作为主任务：

- 基础风险任务：\(\mu^P=0\)；
- 中级风险任务：直接给出 drift nodes；
- 未来独立模块：估计 conditional drift
  \[
  \mu_{i,t}^P=f(X_{i,t};\beta),
  \]
  并验证 out-of-sample conditional-mean prediction。

不应要求 agent 从 65 日价格路径恢复 generator 的 deterministic drift nodes。

---

## 3. Curriculum 总览

| Level | 主任务 | 测度 | 主要输出 | 当前仓库状态 |
|---|---|---|---|---|
| L0 | BSM analytic pricing | \(Q\) | option price | 数值 primitive 已实现；无独立 packaged Agent task |
| L1 | Analytic implied volatility | \(Q\) | IV | scalar solver/contract/verifier 已实现；无 checked-in 独立 package |
| L2 | Analytic market-implied Greeks | \(Q\) | delta/gamma/vega/theta/rho | 已实现，含一条 `ACCEPTED` composite package |
| L3 | MC pricing 与 MC Greeks | \(Q\) | MC price/Greeks | 仅目录骨架与实施计划 |
| L4 | Diffusion 与 correlation calibration | \(P\) | \(\widehat\sigma^P(t),\widehat R\) | 未实现 |
| L5 | Delta-normal analytic VaR/ES | \(P\) | analytic VaR/ES | 未实现 |
| L6 | Underlying portfolio MC VaR/ES | \(P\) | MC VaR/ES | 未实现 |
| L7 | Option portfolio full-revaluation MC VaR/ES | \(P\to Q\) | full-revaluation MC VaR/ES | 未实现 |
| L8 | 研究型扩展 | 混合 | MC-IV、delta-gamma VaR、dynamic IV 等 | 未实现 |

Level 表示推理依赖与方法类别；同一 level 内的资产数量、option 数量、期限、moneyness、噪声水平和组合规模属于 mutation，不应仅因规模变大就创建新 level。

---

## 4. L0：BSM Analytic Pricing

### 输入

\[
S,K,T,r,q,\sigma,\text{option type}.
\]

### 输出

\[
C_{\mathrm{BSM}}\quad\text{或}\quad P_{\mathrm{BSM}}.
\]

### 子层与 mutations

- L0a：单张 European call；
- L0b：European call/put；
- L0c：不同 moneyness 与 maturity；
- L0d：非零 dividend yield；
- L0e：小型 option chain；
- L0f：multi-asset option chain。

### 主要错误点

- 年化期限与交易日换算；
- 连续复利 rate/dividend convention；
- call/put 分支；
- normal CDF 与 PDF；
- rounding 时点。

---

## 5. L1：Analytic Implied Volatility

给定市场报价 \(V_{\mathrm{mkt}}\)，求解：

\[
V_{\mathrm{BSM}}(S,K,T,r,q,\sigma_{\mathrm{imp}})
=V_{\mathrm{mkt}}.
\]

### 子层

- L1a：单张 near-ATM option；
- L1b：ITM/OTM option；
- L1c：多 strike、多 maturity option chain；
- L1d：带 quote noise；
- L1e：报价违反静态上下界，返回规定的 error/status code。

### 必须固定

- root-finding algorithm；
- bracket、初值与 volatility bounds；
- stopping criterion 与 maximum iterations；
- 无解、到期或极低 vega 情形的处理；
- dtype 与 rounding digits。

IV 主干使用 analytic BSM inversion。Monte Carlo IV 属于 noisy nested inversion，只放在 L8。

---

## 6. L2：Analytic Market-Implied Greeks

先使用给定 IV，或从 L1 的市场报价反解 \(\sigma_{\mathrm{imp}}\)，再计算：

\[
\Delta,\Gamma,\mathrm{Vega},\Theta,\rho.
\]

### 子层

- L2a：直接给 IV 的单期权 Greeks；
- L2b：先 inversion，再计算 Greeks；
- L2c：option chain Greeks；
- L2d：position-weighted portfolio Greeks；
- L2e：multi-asset portfolio Greeks aggregation。

### 强制约定

- Greeks 使用 market-implied IV；
- 不读取或使用 \(P\)-measure diffusion nodes；
- 明确 theta 的按年/按日单位；
- 明确 vega/rho 对 1.00 还是 1 percentage point 的变化；
- portfolio Greeks 必须乘 position、contract multiplier 与必要的 FX conversion。

---

## 7. L3：Monte Carlo Pricing 与 Greeks

L3 仍属于 \(Q\) 测度。在 constant-IV BSM 下可直接模拟终值：

\[
S_T=S_0\exp\left[
\left(r-q-\frac12\sigma_{\mathrm{imp}}^2\right)T
+\sigma_{\mathrm{imp}}\sqrt{T}Z
\right].
\]

### 推荐子层

| 子层 | 方法 |
|---|---|
| L3a | plain MC price |
| L3b | antithetic MC price |
| L3c | bump-and-revalue delta |
| L3d | common-random-number delta/gamma/vega |
| L3e | pathwise delta/vega |
| L3f | likelihood-ratio estimator |
| L3g | portfolio MC Greeks |

### Hard verifier 固定项

- PRNG algorithm 与 seed；
- normal draws 的形状、顺序和生成方式；
- path count；
- antithetic pairing rule；
- common random numbers；
- bump type（absolute/relative）与 bump size；
- pathwise/LR estimator 的精确定义；
- discounting、dtype 与 reduction order；
- 输出 rounding。

MC price/Greeks 不使用 \(P\)-measure diffusion nodes。

---

## 8. L4：\(P\)-Measure Diffusion 与 Correlation Calibration

L4 是 VaR/ES 的风险参数入口。

### 已知与未知

| 项目 | Agent 是否需要估计 |
|---|---:|
| Drift node dates | 否，直接给出 |
| Drift node values | 否，直接给出；基础层可设为 0 |
| Diffusion node dates | 否，直接给出 |
| Diffusion node values | 是 |
| Change points | 否 |
| Raw residual correlation | 是 |
| PSD correlation matrix | 是，按规定投影 |

### Diffusion likelihood

当前 authoring process 不是“未舍入 close 的连续 GBM observation”。它从已发布的 tick-aligned
close 重启，先按 time-inhomogeneous GBM 生成连续候选值，再把下一条 close 量化到声明 tick。
因此 solver-visible rounded close 的精确 transition 是离散化 lognormal mass；直接把
observed log return 塞进 Gaussian density 不是精确 generator likelihood。

对区间 \([t_i,t_{i+1}]\) 定义：

\[
m_i(\theta)=
\int_{t_i}^{t_{i+1}}
\left(\mu^P(u)-\frac12\sigma^P(u;\theta)^2\right)du,
\]

\[
v_i(\theta)=
\int_{t_i}^{t_{i+1}}\sigma^P(u;\theta)^2du.
\]

若上一 published state 为 \(s_i\)，下一 published close 为 \(y_i\)，tick 为 \(\delta\)，
令 \(L_i=\max(y_i-\delta/2,0)\)、\(U_i=y_i+\delta/2\)。忽略连续分布下概率为零的
half-even tie 后，精确单资产 mass 为

\[
p_i(\theta)=
\Phi\!\left(
\frac{\log(U_i/s_i)-m_i(\theta)}{\sqrt{v_i(\theta)}}
\right)
-
\Phi\!\left(
\frac{\log(L_i/s_i)-m_i(\theta)}{\sqrt{v_i(\theta)}}
\right),
\]

其中 \(L_i=0\) 时第二个标准化边界定义为 \(-\infty\)，所以第二项为 0。Exact rounded-state MLE 应最小化
\(-\sum_i\log p_i(\theta)\)，并用数值稳定的 CDF-difference 实现。

第一版若为了简化而使用下式：

\[
\ell_{\mathrm{quasi}}(\theta)=\frac12\sum_i
\left[
\log v_i(\theta)
+\frac{(\log(y_i/s_i)-m_i(\theta))^2}{v_i(\theta)}
\right].
\]

则 task 必须命名并声明为 Gaussian quasi-likelihood approximation，说明其适用条件是 tick
相对 spot 和区间标准差足够小；canonical truth 是冻结 optimizer 对该 objective 的输出，
不是 hidden diffusion nodes，也不能声称是 rounded-state exact MLE。Residual correlation
同样属于该近似流程，必须冻结标准化与 PSD projection 方法。

### 推荐子层

- L4a：单资产 constant diffusion；
- L4b：单资产 2-node piecewise-linear diffusion；
- L4c：单资产 65 日、3 个已知日期的 diffusion nodes；
- L4d：多资产分别拟合 diffusion nodes；
- L4e：根据标准化 residual 估计 raw correlation；
- L4f：按指定算法投影为 PSD correlation matrix；
- L4g：输出预测期 integrated covariance。

### 必须固定

- loss/likelihood 的精确定义；
- time grid 与 day-count；
- optimizer、初值、bounds、tolerance、max iterations；
- residual standardisation；
- pairwise/listwise missing-data rule；
- PSD projection algorithm、eigenvalue floor 与 renormalisation；
- 参数和矩阵的输出顺序。

---

## 9. L5：Delta-Normal Analytic VaR/ES

这一层的准确命名应为 `delta_normal_var` 与 `delta_normal_es`，而不是笼统的“closed-form option VaR”。

### 预测期协方差

\[
\Sigma_{ij,h}^P
=\int_t^{t+h}
\rho_{ij}(u)\sigma_i^P(u)\sigma_j^P(u)\,du.
\]

令组合对 log-return 风险因子的美元敏感度为

\[
g_i=\frac{\partial V}{\partial\log S_i}
=S_i\frac{\partial V}{\partial S_i},
\]

则：

\[
\Delta V\approx g^\top\Delta X,
\qquad
s_P^2=g^\top\Sigma_h^P g.
\]

若 \(\Delta V\sim N(m_P,s_P^2)\)，损失 \(L=-\Delta V\)，则：

\[
\operatorname{VaR}_\alpha=-m_P+z_\alpha s_P,
\]

\[
\operatorname{ES}_\alpha
=-m_P+s_P\frac{\phi(z_\alpha)}{1-\alpha}.
\]

### 推荐子层

- L5a：single-underlying portfolio；
- L5b：multi-underlying portfolio；
- L5c：单资产 option portfolio 的 delta-normal approximation；
- L5d：multi-asset option portfolio；
- L5e：不同 horizon 与 confidence level；
- L5f：long/short mixed positions。

### 输入依赖

- drift：直接给出或设为 0；
- diffusion 与 correlation：来自 L4，或在隔离任务中作为已知输入；
- option delta：来自 market-implied BSM Greeks；
- positions、contract multipliers、risk horizon 与 confidence level：直接给出。

---

## 10. L6：Underlying Portfolio Monte Carlo VaR/ES

L6 将 MC VaR 与 MC ES 纳入正式主干。

因为 \(\mu^P(t)\)、\(\sigma^P(t)\) 和 \(R\) 在预测区间内按已知规则演化，联合 log return 可写为：

\[
\log\frac{S_{t+h}}{S_t}
\sim N(m_h,\Sigma_h^P).
\]

其中：

\[
m_{i,h}=\int_t^{t+h}
\left(\mu_i^P(u)-\frac12\sigma_i^P(u)^2\right)du.
\]

对于 deterministic piecewise-linear diffusion，应精确积分 \(m_h\) 与 \(\Sigma_h^P\)，再一次性采样 multivariate normal；基础版本不使用 Euler discretisation。

这句话只对“从当前 state 到 horizon endpoint、区间内不设置发布量化 checkpoint”的连续
scenario law 成立。当前 authoring history 是每个 observation endpoint 都量化并从 rounded
state 重启的另一条 Markov law。L6 task 必须给 scenario process 新的版本化 identity，并在
以下两者中明确选择：一次性连续 endpoint transition，或逐 observation transition 后逐步
tick quantization；不能把前者描述成后者的精确重放。

### MC 风险流程

\[
\widehat\sigma^P(t),\widehat R,\mu^P(t)
\rightarrow S_{t+h}^{(m)}
\rightarrow V_{t+h}^{(m)}
\rightarrow L^{(m)}
\rightarrow \operatorname{VaR}_\alpha,\operatorname{ES}_\alpha.
\]

### 推荐子层

- L6a：单资产 MC VaR；
- L6b：单资产 MC ES；
- L6c：multi-asset MC VaR/ES；
- L6d：PSD-correlated long/short portfolio；
- L6e：不同 horizon/confidence level；
- L6f：比较 delta-normal 与 MC VaR/ES。

### Hard verifier 固定项

- PRNG algorithm、seed 与 scenario count；
- standard-normal draw array 的 shape 与资产顺序；
- covariance factorisation 方法；
- Cholesky failure fallback 或 eigenvalue clipping；
- portfolio value 与 loss sign convention；
- quantile method/interpolation；
- ES tail inclusion 与 boundary-tie rule；
- dtype、reduction order 与 rounding。

### 推荐的离散 VaR/ES 定义

对固定的 scenario loss vector \(L_1,\ldots,L_M\)：

\[
\operatorname{VaR}_\alpha
=Q_\alpha(L_1,\ldots,L_M),
\]

\[
\operatorname{ES}_\alpha
=\operatorname{mean}\{L_m:L_m\geq\operatorname{VaR}_\alpha\}.
\]

如果使用这一规则，必须同时固定 quantile interpolation 以及等于 VaR 的样本是否全部计入 ES。更适合 hard verifier 的替代方案是直接固定 order-statistic index，并取固定数量的最大损失样本均值。

---

## 11. L7：Option Portfolio Full-Revaluation MC VaR/ES

L7 是 Synthetic BSM 的综合主任务，也是 MC VaR/ES 的最终层。

### 两阶段测度结构

第一阶段，在 \(P\) 测度下生成 risk-horizon 情景：

\[
S_{t+h}^{(m)}
\xleftarrow{P}
\mu^P,\widehat\sigma^P,\widehat R.
\]

第二阶段，在每个情景中用 \(Q\) 测度 BSM 重估期权：

\[
V_{t+h}^{(m)}
=V_{\mathrm{BSM}}
\left(
S_{t+h}^{(m)},K,T-h,r_h,q_h,
\sigma_{\mathrm{imp},h}
\right).
\]

然后计算：

\[
\Delta V^{(m)}=V_{t+h}^{(m)}-V_t,
\qquad
L^{(m)}=-\Delta V^{(m)},
\]

并从损失样本得到 MC VaR 与 MC ES。

### 基础版 IV 演化规则

\[
\sigma_{\mathrm{imp},h}=\sigma_{\mathrm{imp},0},
\]

即 frozen IV。基础版不要求 agent 预测 smile/surface dynamics。

### 推荐子层

| 子层 | 组合类型 |
|---|---|
| L7a | 单 underlying + 单 option |
| L7b | 单 underlying + 多 options |
| L7c | 单资产完整 option chain |
| L7d | multi-asset option portfolio |
| L7e | underlyings + options mixed portfolio |
| L7f | delta-normal 与 full-revaluation MC 对比 |
| L7g | VaR/ES + portfolio Greeks 联合报告 |

### 必须固定

- 当前 option valuation 与 horizon valuation 的一致公式；
- maturity roll-down：\(T_h=T_0-h\)；
- risk horizon 内到期 option 的 settlement rule；
- frozen IV 的索引规则（per-contract/per-strike）；
- horizon rate/dividend rule；
- position、multiplier 与 cash treatment；
- 与 L6 相同的随机数、分解、quantile 和 ES convention。

---

## 12. L8：研究型与高难度 Mutations

下列任务暂不进入第一版基础验收主干：

- Monte Carlo implied volatility；
- delta-gamma-normal VaR/ES；
- historical simulation 与 filtered historical simulation；
- sticky-strike 或 sticky-delta IV；
- stochastic/dynamic IV surface；
- stochastic interest rate；
- time-varying correlation；
- importance sampling tail estimation；
- stress VaR/ES；
- liquidity-adjusted VaR；
- transaction cost；
- ES attribution；
- conditional drift model；
- model-risk comparison。

这些扩展会改变模型假设或新增隐变量，不应仅作为基础 L7 的随机 mutation 混入。

---

## 13. 横向 Mutation 维度

每个 level 可沿以下维度变异：

| 维度 | 示例 |
|---|---|
| Instrument | call/put、underlying、mixed portfolio |
| Scale | 1/多资产、1/多期权、chain 长度 |
| Moneyness/maturity | ITM/ATM/OTM、near/far expiry |
| Market inputs | rates、dividends、quote noise |
| Dynamics | constant/2-node/3-node diffusion |
| Dependence | identity、block、dense PSD correlation |
| Portfolio | long/short、hedged/unhedged、multipliers |
| Numerical method | analytic/plain MC/antithetic/pathwise |
| Risk setup | horizon、confidence level、scenario count |
| Data quality | missing rows、invalid quotes、boundary cases |

Mutation 只能改变当前 level 已定义的输入规模或难度，不得偷偷引入更高 level 才需要的建模假设。

---

## 14. Task Isolation 与组合原则

Curriculum 的依赖关系不意味着每道任务必须从 L0 一路执行到 L7。建议同时生成两类 task：

### 14.1 Isolated tasks

上游中间量直接给出，只验证一个主能力。例如：

- 给定 IV，计算 MC Greeks；
- 给定 fitted diffusion nodes 与 PSD correlation，计算 MC VaR/ES；
- 给定 risk scenarios，完成 option full revaluation。

这类任务便于定位模型具体不会哪一步。

### 14.2 Composed tasks

要求 agent 完成多个依赖步骤，例如：

\[
\text{fit diffusion/correlation}
\rightarrow\text{simulate scenarios}
\rightarrow\text{full revaluation}
\rightarrow\text{MC VaR/ES}.
\]

Composed tasks 适合最终 agent benchmark，但 verifier 应同时检查关键中间产物，以区分 calibration、simulation、pricing 和 tail aggregation 错误。

---

## 15. Hard Verifier 规范

### 15.1 确定性要求

所有任务必须固定：

- input database snapshot；
- method 与公式版本；
- calendar/day-count convention；
- PRNG algorithm 与 seed；
- dtype；
- optimizer 与 stopping rules；
- MC path/scenario count；
- quantile 与 ES convention；
- output schema、排序与 rounding。

Hard verifier 不应依赖“数值足够接近”的开放式判断。若 MC 结果要求逐值一致，则 prompt、reference solver 和 pytest 必须使用相同 PRNG、draw order、linear-algebra path 与 reduction rule。

### 15.2 推荐验证层级

| 层级 | 检查内容 |
|---|---|
| Schema | 字段、类型、行数、排序、缺失值 |
| Method | 指定 analytic/MC/calibration 方法是否被遵守 |
| Intermediate | IV、Greeks、nodes、correlation、scenario moments |
| Final | price、VaR、ES 与规定的解释标签 |
| Policy | 禁用 package、文件和答案路径是否未被访问 |

### 15.3 MC 特别注意

仅固定 seed 不足以保证跨实现完全一致；还必须固定 PRNG family、随机数调用顺序、矩阵分解、向量布局、antithetic 排列、quantile 实现和浮点归约顺序。

---

## 16. 推荐发布顺序

### Release A：Pricing 与 Greeks

- L0 BSM analytic pricing；
- L1 analytic IV；
- L2 analytic market-implied Greeks；
- L3 MC pricing/Greeks。

### Release B：Risk Calibration

- L4 diffusion nodes；
- L4 residual correlation 与 PSD projection；
- integrated covariance output。

### Release C：VaR/ES

- L5 delta-normal analytic VaR/ES；
- L6 underlying MC VaR/ES；
- L7 option full-revaluation MC VaR/ES。

### Release D：Research Mutations

- L8 MC-IV、delta-gamma、dynamic IV、tail simulation、conditional drift 等。

这样可以在不阻塞现有 Synthetic BSM 全量生成的情况下，逐步接入风险模块。

---

## 17. Task Pack 结构

当前 accepted L2 package 的实际边界如下；未来 L3--L8 可以复用四个 source area 与三种
release view 的模式，但每个 family 必须使用自己的 versioned method/runtime/interface
identity：

```text
<task_id>/
├── manifest.json
├── public/
│   ├── task.duckdb
│   ├── prompt.md
│   ├── runtime_contract.json
│   └── submission.schema.json
├── verifier/
│   ├── runtime.py
│   ├── oracle_config.json
│   ├── requirements.lock
│   └── test_*.py
├── reference/
│   ├── final_submission.json
│   ├── trajectory.jsonl
│   └── artifacts/
├── authoring_private/
│   └── identity/oracle/build/leakage manifests
└── views/
    ├── evaluation/
    ├── train_dev/
    └── authoring/
```

### 示例 `task_config.yaml`

以下仍是未实现的 L7 设计示例，不对应当前 config parser 或文件格式：

```yaml
task_id: synthetic_bsm_l7_mc_es_0001
level: L7
measure_flow: P_to_Q
primary_objective: full_revaluation_mc_var_es

drift_source: provided
diffusion_source: fitted_piecewise_linear_nodes
diffusion_node_dates_source: provided
correlation_source: fitted_psd

scenario_engine: exact_integrated_log_return
pricing_model: bsm
implied_vol_rule: frozen_per_contract
risk_horizon_days: 10
confidence_level: 0.99

prng: PCG64
seed: 20260812
scenario_count: 100000
covariance_factorization: cholesky
quantile_method: specified_order_statistic
es_tail_rule: top_k_mean

dtype: float64
rounding_digits: 6
output_sort_keys:
  - portfolio_id
  - metric
```

---

## 18. 最终设计决策

第一版正式 curriculum 为 L0--L7，L8 作为研究扩展。其中：

1. MC VaR 与 MC ES 是主干能力，分别进入 L6 与 L7；
2. \(P\)-measure drift 直接给出或设为 0，不从 65 日路径拟合；
3. \(P\)-measure diffusion node dates 给出、node values 拟合；
4. Greeks 与 IV 使用 \(Q\)-measure market-implied volatility；
5. underlying MC VaR/ES 使用精确积分后的联合 log-return sampling；
6. option MC VaR/ES 使用 \(P\) 情景和 \(Q\) 测度 BSM full revaluation；
7. 基础 L7 使用 frozen IV；
8. 所有 MC 任务通过固定完整数值协议实现 pytest hard verification；
9. conditional drift、dynamic IV 与更复杂尾部模型留作独立高阶模块。

由此，Synthetic BSM Agent Task 的最终能力主线为：

\[
\boxed{
\text{Analytic Pricing/IV/Greeks}
\rightarrow
\text{MC Pricing/Greeks}
\rightarrow
\text{Diffusion/Correlation Calibration}
\rightarrow
\text{Analytic VaR/ES}
\rightarrow
\text{MC VaR/ES}
}
\]
