# Synthetic BSM Agent Task：完整 Curriculum

## 1. 文档目标

本文给出 Synthetic BSM Agent Task 的完整能力梯度，覆盖：

1. BSM analytic pricing；
2. analytic implied volatility；
3. analytic market-implied core 与 higher-order Greeks；
4. Monte Carlo pricing、core Greeks 与 higher-order Greeks；
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

| Level | 主任务 | 测度 | Agent 需要恢复或计算的核心量 | 主要输出 |
|---|---|---|---|---|
| L0 | BSM analytic pricing | \(Q\) | 无隐变量拟合 | option price |
| L1 | Analytic implied volatility | \(Q\) | \(\sigma_{\mathrm{imp}}\) | IV |
| L2 | Analytic market-implied Greeks | \(Q\) | IV 或继承 L1；Greek family G0--G4 | core/high-order Greeks、P\&L decomposition、hedge |
| L3 | MC pricing 与 MC Greeks | \(Q\) | 固定规则下的 MC estimator 与 bump stencil | MC price/core/high-order Greeks |
| L4 | Diffusion 与 correlation calibration | \(P\) | diffusion node values、PSD correlation | \(\widehat\sigma^P(t),\widehat R\) |
| L5 | Delta-normal analytic VaR/ES | \(P\) | 预测期均值与协方差 | analytic VaR/ES |
| L6 | Underlying portfolio MC VaR/ES | \(P\) | 情景损失分布 | MC VaR/ES |
| L7 | Option portfolio full-revaluation MC VaR/ES | \(P\to Q\) | \(P\) 情景 + \(Q\) 重估 | full-revaluation MC VaR/ES |
| L8 | 研究型扩展 | 混合 | 依 mutation 而定 | MC-IV、delta-gamma VaR、dynamic IV 等 |

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
\Delta,\Gamma,\mathrm{Vega},\Theta,\rho
\quad\text{以及指定的 higher-order Greeks}.
\]

高阶 Greeks 不另占全局 L0--L8 的 level 编号。它们构成与全局 level
正交的 `greek_family` 轴 G0--G4；同一 Greek family 可以出现在 analytic、
Monte Carlo、portfolio 和 hedging 任务中。

### 6.1 统一变量与导数约定

记单位期权价值为

\[
V=V(S,K,\tau,r,q,\sigma),
\]

其中 \(t\) 为向前增加的 calendar time，\(\tau=T_{\mathrm{expiry}}-t\)
为 time to maturity。因此所有时间衰减 Greek 默认采用

\[
\frac{\partial}{\partial t}
=-\frac{\partial}{\partial\tau}.
\]

每道任务必须明确被求导变量、保持不变的变量以及输出缩放；不得只给出
`charm`、`color` 或 `veta` 名称而不声明 \(t\) / \(\tau\) 的符号约定。

### 6.2 Greek family 轴

#### G0：Core Greeks

| Canonical metric | 定义 | 风险含义 |
|---|---|---|
| `delta` | \(V_S\) | 一阶 spot sensitivity |
| `gamma` | \(V_{SS}\) | spot convexity |
| `vega` | \(V_\sigma\) | volatility sensitivity |
| `theta` | \(V_t=-V_\tau\) | calendar-time decay |
| `rho` | \(V_r\) | risk-free-rate sensitivity |
| `dividend_rho` | \(V_q\) | dividend-yield sensitivity；有些资料称 Phi |

#### G1：常用高阶、交叉与曲率 Greeks

| Canonical metric | 定义 | 风险含义 |
|---|---|---|
| `vanna` | \(V_{S\sigma}=\partial\Delta/\partial\sigma=\partial\mathrm{Vega}/\partial S\) | spot--vol interaction |
| `vomma` | \(V_{\sigma\sigma}=\partial\mathrm{Vega}/\partial\sigma\) | volatility convexity；`volga` 仅作别名 |
| `charm` | \(V_{St}=\partial\Delta/\partial t=-\partial\Delta/\partial\tau\) | delta 的 calendar-time decay |
| `speed` | \(V_{SSS}=\partial\Gamma/\partial S\) | gamma 对 spot 的变化 |
| `zomma` | \(V_{SS\sigma}=\partial\Gamma/\partial\sigma\) | gamma 对 volatility 的变化 |

#### G2：进阶时间衰减与 volatility-curvature Greeks

| Canonical metric | 定义 | 风险含义 |
|---|---|---|
| `color` | \(V_{SSt}=\partial\Gamma/\partial t=-\partial\Gamma/\partial\tau\) | gamma 的 calendar-time decay |
| `veta` | \(V_{\sigma t}=\partial\mathrm{Vega}/\partial t=-\partial\mathrm{Vega}/\partial\tau\) | vega 的 calendar-time decay |
| `ultima` | \(V_{\sigma\sigma\sigma}=\partial\mathrm{Vomma}/\partial\sigma\) | vomma 对 volatility 的变化 |

G0--G3 是按 curriculum 难度与实务用途分组，不是按偏导阶数机械分组；例如
Speed/Zomma 是三阶偏导，而 Veta 是二阶交叉偏导。

#### G3：Strike / Dual Greeks

| Canonical metric | 定义 | 风险含义 |
|---|---|---|
| `dual_delta` | \(V_K\) | option value 对 strike 的一阶敏感度 |
| `dual_gamma` | \(V_{KK}\) | option value 对 strike 的二阶敏感度 |

`volga` 与 `vomma` 表示同一个数学量，不能作为两行重复输出；canonical
schema 统一使用 `vomma`。同理，`dividend_rho` 为 canonical 名称，避免
`phi` 与标准正态密度 \(\phi\) 混淆。

加上 L1 的 `implied_volatility`，完整 Greek/output 轴共 17 项：

```text
implied_volatility,
delta, gamma, vega, theta, rho, dividend_rho,
vanna, vomma, charm, speed, zomma,
color, veta, ultima,
dual_delta, dual_gamma
```

#### G4：Greek 应用任务

G4 不新增 Greek 名称，而是把 G0--G3 用于局部 P\&L 解释和对冲。基础
spot--vol Taylor decomposition 为

\[
\Delta V\approx
\Delta\,\Delta S
+\frac12\Gamma(\Delta S)^2
+\mathrm{Vega}\,\Delta\sigma
+\mathrm{Vanna}\,\Delta S\,\Delta\sigma
+\frac12\mathrm{Vomma}(\Delta\sigma)^2
+\Theta\,\Delta t.
\]

每个 task 必须冻结实际纳入的项、展开点、\(\Delta t\) 符号和 remainder
的定义，不能把未声明的高阶项偷偷加入 reference answer。

Cross-Greek hedge task 给定目标组合和候选 hedge instruments，要求：

1. 按指定的 Greek 集合构造 exposure matrix；
2. 用指定的 exact solve 或 constrained least squares 求 hedge positions；
3. 输出 hedge 前后 exposures、hedge positions 与 residual exposure；
4. 固定约束、权重、solver、tie-breaking 和 position rounding。

推荐从 delta--vega hedge 开始，再扩展到 vanna/vomma 或
delta--gamma--vega 多目标 hedge。

### 6.3 Units 与 scaling contract

所有 volatility、rate 和 dividend-yield 输入在内部均使用 decimal：例如
20% volatility 写作 \(0.20\)。Greek 输出必须声明 derivative 的 raw unit
与 quoted unit，缩放在 portfolio aggregation 和最终 rounding 之前完成。

| 导数维度 | Raw derivative | 常用 quoted scaling |
|---|---|---|
| Spot | 对 \(+1.00\) spot-price unit 求导 | delta 每 spot unit；gamma 每 spot-unit\(^2\)；speed 每 spot-unit\(^3\) |
| Strike | 对 \(+1.00\) strike unit 求导 | dual delta 每 strike unit；dual gamma 每 strike-unit\(^2\) |
| Volatility | 对 \(\sigma+1.00\) 求导 | 每个 vol derivative 维度乘 \(0.01\)：vega/vanna/zomma 乘 \(10^{-2}\)，vomma 乘 \(10^{-4}\)，ultima 乘 \(10^{-6}\) |
| Rate/yield | 对 \(r+1.00\) 或 \(q+1.00\) 求导 | per percentage point 乘 \(10^{-2}\)；per basis point 乘 \(10^{-4}\) |
| Calendar time | 对以年计的 \(t\) 求导 | per-day 输出乘该 task 的精确 year fraction；不得默认混用 1/365 与 1/252 |

若 Greek 同时包含多种维度，缩放因子相乘。例如按 1 vol point、1 calendar
day 报价的 veta，应同时应用 volatility scaling 与该日的 year fraction。
组合级 Greek 再乘 position、contract multiplier 和必要的 FX conversion。

### 6.4 推荐子层

- L2a：G0，直接给 IV 的单期权 core Greeks；
- L2b：G0，先做 IV inversion，再计算 core Greeks；
- L2c：G1，Vanna/Vomma/Charm/Speed/Zomma；
- L2d：G2，Color/Veta/Ultima；
- L2e：G3，Dual Delta/Dual Gamma；
- L2f：含多个 Greek families 的 option chain；
- L2g：position-weighted portfolio Greeks；
- L2h：multi-asset portfolio Greeks aggregation；
- L2i：G4，Greek P\&L decomposition；
- L2j：G4，cross-Greek hedge 与 post-hedge residual report。

### 6.5 强制约定

- Greeks 使用 market-implied IV；
- 不读取或使用 \(P\)-measure diffusion nodes；
- 明确 theta/charm/color/veta 的 calendar-time 或 time-to-maturity 符号；
- 明确每个 volatility derivative dimension 对 1.00 还是 1 vol point；
- 明确 rho/dividend-rho 对 1.00、1 percentage point 还是 1 basis point；
- 明确 spot Greeks 还是 forward Greeks；基础 BSM 主干默认 spot Greeks；
- portfolio Greeks 必须乘 position、contract multiplier 与必要的 FX conversion。

本节的偏导定义属于 curriculum、authoring contract 与 verifier specification。
Public solver prompt 可以给出 metric 名称、紧凑偏导定义、符号和单位契约以消除
歧义，但不得暴露闭式 BSM Greek 公式、\(d_1/d_2\) 展开式、reference 数值、
solver pseudocode 或 reference trajectory。上述答案资产也不得放进 solver
可访问的 public JSON、database、sandbox mount 或 prompt 目录。

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
| L3g | G0 portfolio MC Greeks |
| L3h | CRN vanna/vomma（spot--vol mixed bump 与 vol-curvature bump） |
| L3i | CRN speed/zomma |
| L3j | calendar-roll charm/color/veta |
| L3k | configured higher-order portfolio MC Greeks |

L3h--L3k 是 L3 的高阶扩展。第一版优先采用 frozen finite-difference
stencil 加 common random numbers；不得让 solver 自行选择 forward、backward
或 central stencil。高阶 pathwise/LR estimator、payoff kink 的 smoothing 与
distributional derivative 处理保留在 L8，避免把不同 estimator 的结果混成
同一个 hard-verifier target。

### Hard verifier 固定项

- PRNG algorithm 与 seed；
- normal draws 的形状、顺序和生成方式；
- path count；
- antithetic pairing rule；
- common random numbers；
- bump type（absolute/relative）与 bump size；
- 单变量或 mixed-partial finite-difference stencil、evaluation order 与 denominator；
- time bump 时 valuation date、maturity roll-down、rate/dividend/IV 的保持规则；
- pathwise/LR estimator 的精确定义；
- Greek family、canonical metric name 与 units/scaling contract；
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

对第 \(t\) 个 log return，定义：

\[
m_t(\theta)=
\int_t^{t+\Delta}
\left(\mu^P(u)-\frac12\sigma^P(u;\theta)^2\right)du,
\]

\[
v_t(\theta)=
\int_t^{t+\Delta}\sigma^P(u;\theta)^2du.
\]

使用指定 Gaussian negative log-likelihood：

\[
\ell(\theta)=\frac12\sum_t
\left[
\log v_t(\theta)
+\frac{(r_t-m_t(\theta))^2}{v_t(\theta)}
\right].
\]

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
| L7g | VaR/ES + configured G0--G3 portfolio Greeks 联合报告 |

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
- delta-gamma-vega-vanna-vomma Taylor VaR/ES；
- higher-order pathwise/LR MC estimators；
- payoff smoothing 与 distributional Greek estimators；
- historical simulation 与 filtered historical simulation；
- sticky-strike 或 sticky-delta IV；
- stochastic/dynamic IV surface；
- stochastic interest rate；
- time-varying correlation；
- importance sampling tail estimation；
- stress VaR/ES；
- liquidity-adjusted VaR；
- transaction cost；
- 带 transaction cost、integer lots 或稀疏约束的 cross-Greek hedge；
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
| Greek family | G0 core、G1 cross/curvature、G2 third-order/time decay、G3 dual、G4 application |
| Greek units | raw/per vol point/per bp/per calendar day |
| Greek application | unit/chain/portfolio、P\&L decomposition、hedge |
| Risk setup | horizon、confidence level、scenario count |
| Data quality | missing rows、invalid quotes、boundary cases |

Mutation 只能改变当前 level 已定义的输入规模或难度，不得偷偷引入更高 level 才需要的建模假设。

---

## 14. Task Isolation 与组合原则

Curriculum 的依赖关系不意味着每道任务必须从 L0 一路执行到 L7。建议同时生成两类 task：

### 14.1 Isolated tasks

上游中间量直接给出，只验证一个主能力。例如：

- 给定 IV，计算 MC Greeks；
- 给定 IV，计算指定 G1--G3 analytic Greeks；
- 给定 unit Greeks 与 positions，完成 portfolio aggregation；
- 给定 scenario shocks，完成指定项的 Greek P\&L decomposition；
- 给定 hedge universe，求指定 Greek targets 的 hedge positions；
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

### 15.2 Greek-specific hard-verifier contract

所有含 Greeks 的任务还必须固定：

- canonical metric names 与允许的 input aliases；
- differentiation variable 与 held-fixed arguments；
- calendar time \(t\) / time to maturity \(\tau\) 的符号；
- volatility、rate、yield 的 decimal input convention；
- 每个 derivative dimension 的 quoted scaling；
- unit-option、per-contract 与 position-weighted output 的区分；
- analytic formula version，或 MC/finite-difference estimator 与完整 stencil；
- portfolio/chain 的 row keys、aggregation keys 与 sort order；
- invalid quote、failed IV、expiry 与 near-zero-vega 的 status propagation；
- scaling、aggregation、rounding 与 decimal serialization 的精确顺序。

Verifier 使用独立 private oracle 计算 target，并对 canonical decimal strings 或
其他冻结的 canonical representation 做 exact equality。禁止把 oracle 公式、
reference outputs 或 trajectory 复制到 public schema 或 solver-visible database。

### 15.3 推荐验证层级

| 层级 | 检查内容 |
|---|---|
| Schema | 字段、类型、行数、排序、缺失值 |
| Contract | Greek 名称、偏导方向、units/scaling、position convention |
| Method | 指定 analytic/MC/calibration 方法是否被遵守 |
| Intermediate | IV、Greeks、nodes、correlation、scenario moments |
| Final | price、VaR、ES 与规定的解释标签 |
| Policy | 禁用 package、文件和答案路径是否未被访问 |

### 15.4 MC 特别注意

仅固定 seed 不足以保证跨实现完全一致；还必须固定 PRNG family、随机数调用顺序、矩阵分解、向量布局、antithetic 排列、quantile 实现和浮点归约顺序。

---

## 16. 推荐发布顺序

### Release A：Pricing 与 Greeks

- Release A1：L0 pricing、L1 IV、L2-G0 core analytic Greeks、L3a--L3g core MC；
- Release A2：L2-G1/G2/G3 higher-order analytic Greeks；
- Release A3：L2-G4 P\&L/hedging 与 L3h--L3k higher-order MC Greeks。

这一顺序先稳定 core pipeline，再扩展同一 BSM/constant-IV 假设下的高阶
敏感度，不需要等待 L4--L7 风险模块完成。

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

```text
task_pack/
├── database/
│   ├── market.duckdb
│   └── schema.md
├── prompt/
│   └── task.md
├── verifier/
│   └── test_answer.py
├── reference_trajectory/
│   └── trajectory.jsonl
├── policy/
│   ├── allowlist.yaml
│   └── sandbox_policy.md
├── task_config.yaml
└── README.md
```

`verifier/`、`reference_trajectory/` 和任何带 reference outputs 的 authoring
artifact 都是 private evaluator assets，不得挂载到 solver sandbox。交付 task
package 不代表这些路径在 rollout 时对 agent 可见。

### 示例 `task_config.yaml`

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

### 高阶 Greek task 的配置示例

```yaml
task_id: synthetic_bsm_l2_g1_greeks_0001
level: L2
measure_flow: Q
primary_objective: analytic_market_implied_greeks

greek_family: G1
metrics:
  - vanna
  - vomma
  - charm
  - speed
  - zomma

greek_contract:
  spot_greek_type: spot
  time_derivative_variable: calendar_time
  volatility_input_unit: decimal
  volatility_output_unit: one_vol_point
  volatility_unit_size: 0.01
  time_output_unit: calendar_day
  day_count: ACT/365F
  output_scope: unit_option

dtype: float64
rounding_digits: 6
output_sort_keys:
  - option_id
  - metric

solver_visible:
  metric_and_unit_contract: true
  closed_form_formulas: false
  reference_outputs: false
  reference_trajectory: false
```

---

## 18. 最终设计决策

第一版正式 curriculum 为 L0--L7，L8 作为研究扩展。其中：

1. MC VaR 与 MC ES 是主干能力，分别进入 L6 与 L7；
2. \(P\)-measure drift 直接给出或设为 0，不从 65 日路径拟合；
3. \(P\)-measure diffusion node dates 给出、node values 拟合；
4. Greeks 与 IV 使用 \(Q\)-measure market-implied volatility；
5. L2 正式覆盖 G0 core、G1 cross/curvature、G2 third-order/time-decay、G3 dual Greeks 与 G4 P\&L/hedging；
6. canonical Greek/output 轴共有 17 项，`volga` 不与 `vomma` 重复计数；
7. 所有高阶 Greek 必须冻结导数方向、\(t/\tau\) 符号及逐维 units/scaling；
8. L3 通过固定 CRN、bump stencil 与 evaluation order 扩展 higher-order MC Greeks；
9. underlying MC VaR/ES 使用精确积分后的联合 log-return sampling；
10. option MC VaR/ES 使用 \(P\) 情景和 \(Q\) 测度 BSM full revaluation；
11. 基础 L7 使用 frozen IV；
12. 所有 MC 任务通过固定完整数值协议实现 pytest hard verification；
13. public prompt/schema 只暴露任务与单位契约，不暴露闭式公式、reference outputs 或 trajectory；
14. conditional drift、dynamic IV 与更复杂尾部模型留作独立高阶模块。

由此，Synthetic BSM Agent Task 的最终能力主线为：

\[
\boxed{
\text{Analytic Pricing/IV/Core \& Higher-Order Greeks}
\rightarrow
\text{MC Pricing/Core \& Higher-Order Greeks}
\rightarrow
\text{Diffusion/Correlation Calibration}
\rightarrow
\text{Analytic VaR/ES}
\rightarrow
\text{MC VaR/ES}
}
\]
