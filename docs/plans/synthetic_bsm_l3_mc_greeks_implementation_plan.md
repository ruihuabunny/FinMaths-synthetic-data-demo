# Synthetic BSM Agent Task：L3 Monte Carlo Greeks 实施计划与数学推导

> 实现状态（2026-08-12）：本文是 future/design-only 计划。当前仓库只有可导入的
> `src/synthetic_derivatives/solver/mc/` 骨架，没有任何 MC estimator、L3 task/variant、
> draw bank、schema、runtime profile、package、verifier 或验收测试。现有可运行能力止于
> analytic BSM、visible-price IV 与 analytic market-implied Greeks；本文中的“第一版”均指
> 未来 release，不是当前 API。

## 1. 文档目的

本文规划 Synthetic BSM Agent Task 中 L3「Monte Carlo pricing 与 Monte Carlo Greeks」的第一版实现，并给出 verifier、reference solver 与 agent trajectory 共同采用的数学公式。

L3 的核心边界是：

> L3 完全属于风险中性 \(Q\) 测度；使用 European BSM 的 market-implied volatility，通过精确终值采样计算 MC price/Greeks。L3 不拟合、读取或使用 \(P\)-measure piecewise-linear diffusion nodes。

第一版主目标为：

1. plain 与 antithetic MC price；
2. common-random-number central bump Delta/Gamma/Vega；
3. pathwise Delta/Vega；
4. likelihood-ratio Delta/Gamma/Vega；
5. 可复现的标准误、输出单位和 hard verifier；
6. 后续再加入 Rho、Theta 与 portfolio aggregation。

---

## 2. 范围与非目标

### 2.1 L3 范围

- European vanilla call/put；
- constant \(r,q,\sigma_{\mathrm{imp}}\)；
- exact terminal sampling，不进行 Euler time stepping；
- 单张期权为基础任务；
- plain MC、antithetic variates、common random numbers；
- bump-and-revalue、pathwise 与 likelihood-ratio estimators；
- 固定 draw bank 下的严格确定性输出；
- analytic BSM Greeks 只作为开发阶段的 benchmark。

### 2.2 第一版不包含

- \(P\)-measure historical diffusion/drift calibration；
- Monte Carlo implied volatility；
- American、Asian、barrier 或其他 path-dependent options；
- stochastic volatility、stochastic rate 或 dynamic IV surface；
- basket/cross-asset payoff；
- automatic differentiation；
- QuantLib/py_vollib 作为 agent 可调用的解题依赖；
- 用统计 tolerance 替代 hard verifier。

MC-IV 继续保留在 L8。\(P\)-measure diffusion 与 correlation calibration 从 L4 开始。

---

## 3. 统一数学与单位约定

### 3.1 \(Q\) 测度 BSM 动态

第一版只处理 USD cash-settled European vanilla。经济对象是 valuation timestamp 已知的正
ex-dividend spot；numeraire 是 task 明确声明的 USD money-market account，`Q` 是与该
numeraire 对应的风险中性测度。状态变量只有 spot，条件信息是 valuation timestamp 的
`S_0,K,T,r,q,sigma_IV`、合约约定和公开 frozen draws；时间轴为 calendar time，day count
第一版固定 Actual/365 Fixed。对单一 underlying：

\[
\frac{dS_t}{S_t}=(r-q)\,dt+\sigma\,dW_t^Q,
\]

其中 \(\sigma=\sigma_{\mathrm{imp}}\) 为该 option quote 对应的 market-implied volatility。

在常数 \(r,q,\sigma\) 下：

\[
S_T(Z)=S_0\exp\left[
\left(r-q-\frac12\sigma^2\right)T
+\sigma\sqrt{T}Z
\right],
\qquad Z\sim N(0,1).
\]

因此 European option 不需要离散整条路径，只需采样 \(Z\) 并直接生成 \(S_T\)。这消除了 Euler bias，也使 agent 与 verifier 更容易完全复现。

### 3.2 Payoff

\[
g_{\mathrm{call}}(s)=(s-K)^+,
\qquad
g_{\mathrm{put}}(s)=(K-s)^+.
\]

除零概率事件 \(s=K\) 外，定义 payoff 的一阶导：

\[
\eta_{\mathrm{call}}(s)=\mathbf 1_{\{s>K\}},
\qquad
\eta_{\mathrm{put}}(s)=-\mathbf 1_{\{s<K\}}.
\]

价格为：

\[
V=e^{-rT}\mathbb E^Q[g(S_T)].
\]

### 3.3 Greek 定义

内部一律先计算 raw Greek：

\[
\Delta=\frac{\partial V}{\partial S_0},
\qquad
\Gamma=\frac{\partial^2V}{\partial S_0^2},
\qquad
\mathcal V=\frac{\partial V}{\partial\sigma},
\]

\[
\rho=\frac{\partial V}{\partial r},
\qquad
\Theta=\frac{\partial V}{\partial t}
=-\frac{\partial V}{\partial T}.
\]

建议的外部输出单位为：

| 字段 | 定义 | 输出单位 |
|---|---|---|
| `delta` | \(\partial V/\partial S_0\) | 每 1 个 spot 单位 |
| `gamma` | \(\partial^2V/\partial S_0^2\) | 每 \(1^2\) 个 spot 单位 |
| `vega_per_1pct` | \(0.01\,\partial V/\partial\sigma\) | volatility 上升 1 percentage point |
| `rho_per_1bp` | \(10^{-4}\,\partial V/\partial r\) | rate 上升 1 bp |
| `theta_per_day` | \(\Theta/B\) | 经过 1 日，\(B\) 为指定 day-count basis |

内部 raw 值和外部 scaled 值不得混用。第一版配置应明确写出 `vega_scale: 0.01`、`rho_scale: 0.0001` 与 `theta_day_count_basis`。

### 3.4 输入 IV 的来源

- L3 基础任务直接给出 \(\sigma_{\mathrm{imp}}\)；
- L3 composite mutation 可以先调用 L1 的 analytic BSM inversion；
- 不允许使用 \(P\)-measure realized/historical diffusion 代替 \(\sigma_{\mathrm{imp}}\)；
- 不在 L3 中用 noisy MC root finding 反解 IV。

---

## 4. MC Price 与 Antithetic Estimator

给定独立 draws \(Z_1,\ldots,Z_N\)，定义单路径 discounted payoff：

\[
Y(Z_i)=e^{-rT}g(S_T(Z_i)).
\]

plain MC price 为：

\[
\widehat V_N=\frac1N\sum_{i=1}^N Y(Z_i).
\]

### 4.1 Antithetic variates

生成 \(M\) 个独立 base draws \(Z_1,\ldots,Z_M\)，每个 draw 与 \(-Z_i\) 配对：

\[
A_i^{V}
=\frac12\left[Y(Z_i)+Y(-Z_i)\right].
\]

总路径数为 \(N=2M\)，antithetic estimator 为：

\[
\widehat V_{\mathrm{AV}}
=\frac1M\sum_{i=1}^M A_i^V.
\]

关键实现约定：

- `num_base_draws = M`；
- `total_paths = 2M`；
- draw bank 只需保存 \(M\) 个 base draws；
- \(A_i^V\) 才是相互独立的统计 replicate；
- 标准误必须基于 \(M\) 个 pair means，而不是错误地把 \(2M\) 条相关路径视为独立。

其标准误为：

\[
\widehat{\operatorname{SE}}(\widehat V_{\mathrm{AV}})
=
\sqrt{
\frac{1}{M(M-1)}
\sum_{i=1}^M
\left(A_i^V-\widehat V_{\mathrm{AV}}\right)^2
}.
\]

---

## 5. Common-Random-Number Bump-and-Revalue

定义：

\[
Y(\theta;Z)=e^{-rT}g(S_T(\theta;Z)),
\]

其中 \(\theta\) 表示被 bump 的参数。对所有 base、up-bump 与 down-bump 估值，必须复用完全相同的 \(Z_i\)。这就是 common random numbers（CRN）。

如果 up/down 两边独立抽样，则两个 noisy price 相减后方差会被 \(1/h^2\) 放大；CRN 通过路径级配对消除大部分共同噪声。

### 5.1 Delta

取固定的 relative spot bump \(\varepsilon_S\)，并定义：

\[
h_S=\varepsilon_S S_0,
\qquad
S_0^+=S_0+h_S,
\qquad
S_0^-=S_0-h_S.
\]

central-difference Delta：

\[
\widehat\Delta_{\mathrm{CRN}}
=\frac1N\sum_{i=1}^N
\frac{Y(S_0^+;Z_i)-Y(S_0^-;Z_i)}{2h_S}.
\]

应先构造每条路径的 Delta contribution：

\[
H_i^\Delta
=\frac{Y(S_0^+;Z_i)-Y(S_0^-;Z_i)}{2h_S},
\]

再对 \(H_i^\Delta\) 求均值与标准误；不能用两个 price standard errors 拼凑 Greek standard error。

### 5.2 Gamma

\[
\widehat\Gamma_{\mathrm{CRN}}
=\frac1N\sum_{i=1}^N
\frac{
Y(S_0^+;Z_i)-2Y(S_0;Z_i)+Y(S_0^-;Z_i)
}{h_S^2}.
\]

路径贡献为：

\[
H_i^\Gamma
=
\frac{
Y(S_0^+;Z_i)-2Y(S_0;Z_i)+Y(S_0^-;Z_i)
}{h_S^2}.
\]

Gamma 对 payoff kink 和 bump size 最敏感。第一版不得让 agent 自行选择 bump；\(\varepsilon_S\) 必须由 task config 明确给出。

### 5.3 Vega

取 absolute volatility bump \(h_\sigma\)，并要求 \(\sigma-h_\sigma>0\)：

\[
\widehat{\mathcal V}_{\mathrm{CRN}}
=\frac1N\sum_{i=1}^N
\frac{Y(\sigma+h_\sigma;Z_i)-Y(\sigma-h_\sigma;Z_i)}{2h_\sigma}.
\]

对 volatility 进行 bump 时，必须同时更新：

\[
-\frac12\sigma^2T
\quad\text{和}\quad
\sigma\sqrt T Z.
\]

最终：

\[
\widehat{\mathrm{Vega}}_{1\%}
=0.01\,\widehat{\mathcal V}_{\mathrm{CRN}}.
\]

### 5.4 Rho（第二阶段）

取 \(h_r=10^{-4}\) 或 task config 指定的 absolute rate bump：

\[
\widehat\rho_{\mathrm{CRN}}
=\frac1N\sum_{i=1}^N
\frac{Y(r+h_r;Z_i)-Y(r-h_r;Z_i)}{2h_r}.
\]

rate bump 必须同时作用于：

1. discount factor \(e^{-rT}\)；
2. terminal distribution 中的 risk-neutral drift \((r-q)T\)。

外部输出为 \(10^{-4}\widehat\rho\)。

### 5.5 Theta（第二阶段）

令 \(h_T=1/B\)，其中 \(B\) 为明确规定的 day-count basis。标准 market Theta 定义为 \(-\partial V/\partial T\)，因此：

\[
\widehat\Theta_{\mathrm{CRN}}
=\frac1N\sum_{i=1}^N
\frac{Y(T-h_T;Z_i)-Y(T+h_T;Z_i)}{2h_T}.
\]

该式输出 annualized raw Theta；`theta_per_day` 为：

\[
\widehat\Theta_{\mathrm{per\ day}}
=\frac{\widehat\Theta_{\mathrm{CRN}}}{B}.
\]

基础任务应限制 \(T>h_T\)。临近到期的一侧差分单独作为后续 mutation，不能隐式改变规则。

### 5.6 Antithetic + CRN 的正确组合

对任一 Greek 的单路径 contribution \(H(Z)\)，先形成：

\[
A_i^G=\frac12\left[H(Z_i)+H(-Z_i)\right],
\]

再计算：

\[
\widehat G=\frac1M\sum_{i=1}^M A_i^G,
\]

\[
\widehat{\operatorname{SE}}(\widehat G)
=
\sqrt{
\frac{1}{M(M-1)}
\sum_{i=1}^M(A_i^G-\widehat G)^2
}.
\]

---

## 6. Pathwise Estimators

pathwise 方法把 terminal spot 写成标准正态 \(Z\) 的可微函数，然后在期望内求导。

### 6.1 Terminal spot 的导数

\[
\frac{\partial S_T}{\partial S_0}=\frac{S_T}{S_0},
\]

\[
\frac{\partial S_T}{\partial\sigma}
=S_T\left(\sqrt T Z-\sigma T\right),
\]

\[
\frac{\partial S_T}{\partial r}=TS_T.
\]

对 maturity：

\[
\frac{\partial S_T}{\partial T}
=S_T\left[
r-q-\frac12\sigma^2
+\frac{\sigma Z}{2\sqrt T}
\right].
\]

### 6.2 Pathwise Delta

由于 call/put payoff 几乎处处可微：

\[
\Delta_{\mathrm{PW}}
=e^{-rT}\mathbb E\left[
\eta(S_T)\frac{S_T}{S_0}
\right].
\]

MC estimator 为：

\[
\widehat\Delta_{\mathrm{PW}}
=\frac{e^{-rT}}{N}\sum_{i=1}^N
\eta(S_T^{(i)})\frac{S_T^{(i)}}{S_0}.
\]

### 6.3 Pathwise Vega

\[
\mathcal V_{\mathrm{PW}}
=e^{-rT}\mathbb E\left[
\eta(S_T)S_T
\left(\sqrt T Z-\sigma T\right)
\right].
\]

因此：

\[
\widehat{\mathrm{Vega}}_{1\%}^{\mathrm{PW}}
=0.01\frac{e^{-rT}}{N}\sum_{i=1}^N
\eta(S_T^{(i)})S_T^{(i)}
\left(\sqrt T Z_i-\sigma T\right).
\]

### 6.4 Pathwise Rho（第二阶段）

rate 同时进入 discount factor 与 terminal spot：

\[
\rho_{\mathrm{PW}}
=e^{-rT}\mathbb E\left[
T\left(\eta(S_T)S_T-g(S_T)\right)
\right].
\]

### 6.5 Pathwise Theta（第二阶段）

记：

\[
B_T(Z)=r-q-\frac12\sigma^2+
\frac{\sigma Z}{2\sqrt T}.
\]

因为 \(\Theta=-\partial V/\partial T\)：

\[
\Theta_{\mathrm{PW}}
=e^{-rT}\mathbb E\left[
r\,g(S_T)-\eta(S_T)S_TB_T(Z)
\right].
\]

### 6.6 为什么不能使用 naive pathwise Gamma

vanilla payoff 的一阶导 \(\eta(s)\) 在 \(K\) 处跳跃，其二阶导是集中在 strike 上的广义函数。若在有限样本路径上直接把 payoff 二阶导写成 0，会错误地得到：

\[
\widehat\Gamma_{\mathrm{naive\ PW}}=0.
\]

因此 L3 禁止 naive pathwise Gamma。Gamma 必须使用以下之一：

- CRN central bump；
- pure likelihood-ratio Gamma；
- mixed pathwise–likelihood-ratio Gamma。

---

## 7. Likelihood-Ratio Estimators

likelihood-ratio method（LRM）不对 payoff 求导，而是对 terminal density 求导，因此适合处理 payoff kink。

### 7.1 Score identity

若 \(S_T\) 的 density 为 \(p_\theta(s)\)，且 discount factor 不依赖 \(\theta\)，则：

\[
\frac{\partial V}{\partial\theta}
=e^{-rT}\mathbb E\left[
g(S_T)\frac{\partial}{\partial\theta}
\log p_\theta(S_T)
\right].
\]

二阶导满足：

\[
\frac{\partial^2 V}{\partial\theta^2}
=e^{-rT}\mathbb E\left[
g(S_T)
\left{
(\partial_\theta\log p)^2
+\partial_{\theta\theta}\log p
\right}
\right].
\]

定义：

\[
a=\sigma\sqrt T.
\]

### 7.2 LRM Delta

对 \(S_0\) 的 score 为：

\[
w_\Delta(Z)=\frac{Z}{S_0a}.
\]

所以：

\[
\widehat\Delta_{\mathrm{LR}}
=\frac{e^{-rT}}{N}\sum_{i=1}^N
g(S_T^{(i)})\frac{Z_i}{S_0\sigma\sqrt T}.
\]

### 7.3 LRM Gamma

对 \(S_0\) 的二阶 likelihood weight 为：

\[
w_\Gamma(Z)
=\frac{Z^2-1-aZ}{S_0^2a^2}
=\frac{Z^2-1-\sigma\sqrt T Z}
{S_0^2\sigma^2T}.
\]

因此：

\[
\widehat\Gamma_{\mathrm{LR}}
=\frac{e^{-rT}}{N}\sum_{i=1}^N
g(S_T^{(i)})
\frac{Z_i^2-1-\sigma\sqrt T Z_i}
{S_0^2\sigma^2T}.
\]

LR Gamma 没有 finite-difference bump bias，但在短期限、低波动或极端 moneyness 下可能方差很高，所以第一版必须同时输出 estimator standard error。

### 7.4 LRM Vega

对 volatility 的 score 为：

\[
w_{\mathcal V}(Z)
=\frac{Z^2-1}{\sigma}-Z\sqrt T.
\]

因此：

\[
\widehat{\mathcal V}_{\mathrm{LR}}
=\frac{e^{-rT}}{N}\sum_{i=1}^N
g(S_T^{(i)})
\left[
\frac{Z_i^2-1}{\sigma}-Z_i\sqrt T
\right].
\]

外部 vega 输出仍需乘 \(0.01\)。

### 7.5 LRM Rho（第二阶段）

terminal density 对 \(r\) 的 score 为 \(Z\sqrt T/\sigma\)，同时 discount factor 对 \(r\) 的导数为 \(-Te^{-rT}\)。因此：

\[
\rho_{\mathrm{LR}}
=e^{-rT}\mathbb E\left[
g(S_T)
\left(
\frac{Z\sqrt T}{\sigma}-T
\right)
\right].
\]

### 7.6 LRM Theta（可选扩展）

令：

\[
b=r-q-\frac12\sigma^2.
\]

terminal density 对 \(T\) 的 score 为：

\[
w_T(Z)=
\frac{Z^2-1}{2T}
+\frac{bZ}{\sigma\sqrt T}.
\]

由于 \(\Theta=-\partial V/\partial T\)：

\[
\Theta_{\mathrm{LR}}
=e^{-rT}\mathbb E\left[
g(S_T)\left(r-w_T(Z)\right)
\right].
\]

LR Theta 在短期限下可能非常高方差，不进入第一版强制验收。

### 7.7 推荐的 mixed Gamma

对 pathwise Delta 再应用 likelihood-ratio identity，可得：

\[
\Gamma_{\mathrm{mixed}}
=e^{-rT}\mathbb E\left[
\eta(S_T)\frac{S_T}{S_0^2}
\left(
\frac{Z}{\sigma\sqrt T}-1
\right)
\right].
\]

该 estimator 通常比 pure LR Gamma 使用了更多 payoff 结构信息，可作为 L3f 的高级 mutation；但第一版仍以 CRN Gamma 为默认 estimator。

---

## 8. Analytic BSM Benchmark

analytic Greek 不作为 MC task 的提交答案，而用于：

- unit/integration test；
- estimator bias 与 variance QA；
- 离线选择 bump size、path count 和 mutation 范围；
- 检查 call/put sign、rate/dividend 与单位约定。

当前 repo 的 analytic benchmark 实现与数学/权限说明位于
[`src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/`](../../src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/README.md)。
MC 验收只能把它用于 QA；不得用其解析输出替代 frozen-draw estimator realization。

定义：

\[
d_1=\frac{\log(S_0/K)+(r-q+\frac12\sigma^2)T}
{\sigma\sqrt T},
\qquad
d_2=d_1-\sigma\sqrt T.
\]

则：

\[
\Delta_{\mathrm{call}}=e^{-qT}\Phi(d_1),
\qquad
\Delta_{\mathrm{put}}=e^{-qT}(\Phi(d_1)-1),
\]

\[
\Gamma=\frac{e^{-qT}\phi(d_1)}{S_0\sigma\sqrt T},
\]

\[
\mathcal V=S_0e^{-qT}\phi(d_1)\sqrt T.
\]

Rho：

\[
\rho_{\mathrm{call}}=KTe^{-rT}\Phi(d_2),
\qquad
\rho_{\mathrm{put}}=-KTe^{-rT}\Phi(-d_2).
\]

Theta：

\[
\Theta_{\mathrm{call}}
=-
\frac{S_0e^{-qT}\phi(d_1)\sigma}{2\sqrt T}
-rKe^{-rT}\Phi(d_2)
+qS_0e^{-qT}\Phi(d_1),
\]

\[
\Theta_{\mathrm{put}}
=-
\frac{S_0e^{-qT}\phi(d_1)\sigma}{2\sqrt T}
+rKe^{-rT}\Phi(-d_2)
-qS_0e^{-qT}\Phi(-d_1).
\]

---

## 9. L3 子层与正式交付顺序

沿用总 curriculum 中的 L3a–L3g，但将实际实现拆成三个 release。

| 子层 | Task | 默认 estimator | 第一版状态 |
|---|---|---|---|
| L3a | plain MC price | sample mean | 必做 |
| L3b | antithetic MC price | pair mean | 必做 |
| L3c | bump-and-revalue Delta | central CRN | 必做 |
| L3d | Delta/Gamma/Vega | central CRN + antithetic | 必做 |
| L3e | pathwise Delta/Vega | pathwise + antithetic | 必做 |
| L3f | LR Delta/Gamma/Vega | likelihood ratio + antithetic | 必做 |
| L3g | portfolio MC Greeks | position aggregation | 第二阶段 |

### Release L3-A：确定性 MC 基础

1. exact terminal sampler；
2. call/put payoff；
3. frozen normal draw bank；
4. plain/antithetic MC price；
5. pair-level standard error；
6. fixed-rounding output。

### Release L3-B：核心 MC Greeks

1. CRN central Delta/Gamma/Vega；
2. pathwise Delta/Vega；
3. LR Delta/Gamma/Vega；
4. mixed Gamma 作为可选 estimator；
5. estimator-specific standard errors；
6. analytic benchmark QA。

### Release L3-C：扩展 Greeks 与 portfolio

1. Rho 与 Theta 的 CRN estimator；
2. pathwise/LR Rho；
3. Theta 高方差 mutation；
4. position、contract multiplier 与 portfolio aggregation；
5. multi-asset portfolio 输出 Greek vector。

L3-C 不得顺带引入 \(P\)-measure correlation。对于 additive vanilla portfolio：

\[
G_{\mathrm{portfolio}}
=\sum_j n_jm_jG_j,
\]

其中 \(n_j\) 是 position，\(m_j\) 是 contract multiplier。不同 underlying 的 Delta 应输出为向量，而不是错误地压成一个没有单位意义的标量。

---

## 10. 推荐代码结构

以下是与当前仓库权限边界一致的目标结构。已有 analytic/IV 内核保持在
`solver/analytic_and_implied_greeks_iv/`；未来 MC 数值实现进入 `solver/mc/`，并使用独立
package materializer 与 package-test suite，不能复用 analytic/visible-IV family 的数值代码
或 package identity。当前实际状态见根
[Solver MC scaffold 说明](../../src/synthetic_derivatives/solver/mc/README.md)。

```text
src/synthetic_derivatives/
├── tasks/
│   └── l3_mc_greeks.py
├── authoring/
│   └── l3_mc_greeks.py
├── solver/
│   ├── l3_mc_greeks.py
│   └── mc/
│       ├── draws.py
│       ├── terminal_bsm.py
│       ├── payoffs.py
│       ├── statistics.py
│       ├── pricing.py
│       ├── finite_difference.py
│       ├── pathwise.py
│       ├── likelihood_ratio.py
│       └── portfolio.py
├── packaging_l3_mc_greeks/
│   └── ...
└── verifier/
    └── l3_mc_greeks.py

tests/
├── unit/
│   ├── test_mc_terminal_bsm.py
│   ├── test_mc_antithetic.py
│   ├── test_mc_finite_difference.py
│   ├── test_mc_pathwise.py
│   ├── test_mc_likelihood_ratio.py
│   └── test_mc_units.py
├── integration/
│   ├── test_l3_fixed_draw_snapshots.py
│   └── test_l3_against_analytic_bsm.py
├── packaging_l3_mc_greeks/
│   └── test_l3_task_pack.py
└── verifier_robustness/
    └── test_l3_hard_verifier.py
```

### 10.1 核心函数契约

```python
terminal_spot_exact(S0, T, r, q, sigma, z) -> float64_array

discounted_payoff(option_type, ST, K, r, T) -> float64_array

antithetic_pair_reduce(contrib_plus_z, contrib_minus_z)
    -> estimate, standard_error

estimate_crn_greeks(task, base_z)
    -> price, delta, gamma, vega, standard_errors

estimate_pathwise_greeks(task, base_z)
    -> delta, vega, standard_errors

estimate_lr_greeks(task, base_z)
    -> delta, gamma, vega, standard_errors
```

所有 estimator 应返回未 rounding 的 `float64` raw values；单位缩放和 canonical serialization 只在统一输出层执行。

---

## 11. DuckDB 数据与 Draw Bank 设计

### 11.1 为什么推荐冻结 draw bank

仅写 `seed` 仍会受到以下因素影响：

- PRNG algorithm；
- NumPy/语言版本；
- normal transform implementation；
- draw shape 与生成顺序。

为了 hard verifier 与跨 agent runtime 的一致性，推荐在生成数据集时产生有限个共享 draw sets，并把 base normal draws 冻结在 DuckDB 中。draws 是公开输入，不是答案，因此不会泄漏 reference Greek。
DuckDB 由 trusted adapter 读取；除非新的 L3 runtime contract 明确授权，Agent 不获得 raw
connection。当前 analytic/IV runtime 禁止 Agent 直接导入 `duckdb`，L3 不能沿用其 profile
却在 prompt 中假定 raw SQL 可用。

### 11.2 推荐表结构

```sql
CREATE TABLE mc_draw_sets (
    draw_set_id        VARCHAR PRIMARY KEY,
    generator_name     VARCHAR NOT NULL,
    generator_version  VARCHAR NOT NULL,
    seed                UBIGINT NOT NULL,
    num_base_draws      INTEGER NOT NULL,
    antithetic          BOOLEAN NOT NULL,
    dtype               VARCHAR NOT NULL
);

CREATE TABLE mc_base_normal_draws (
    draw_set_id  VARCHAR NOT NULL,
    draw_index   INTEGER NOT NULL,
    z            DOUBLE NOT NULL,
    PRIMARY KEY (draw_set_id, draw_index)
);

CREATE TABLE l3_mc_greek_tasks (
    task_id                 VARCHAR PRIMARY KEY,
    sublevel                VARCHAR NOT NULL,
    estimator               VARCHAR NOT NULL,
    option_type             VARCHAR NOT NULL,
    spot                    DOUBLE NOT NULL,
    strike                  DOUBLE NOT NULL,
    maturity_years          DOUBLE NOT NULL,
    rate                    DOUBLE NOT NULL,
    dividend_yield          DOUBLE NOT NULL,
    implied_volatility      DOUBLE NOT NULL,
    draw_set_id             VARCHAR NOT NULL,
    spot_bump_relative      DOUBLE,
    volatility_bump_abs     DOUBLE,
    rate_bump_abs           DOUBLE,
    time_bump_years         DOUBLE,
    vega_scale              DOUBLE NOT NULL,
    rho_scale               DOUBLE NOT NULL,
    theta_day_count_basis   INTEGER,
    rounding_digits         INTEGER NOT NULL
);
```

读取 draws 时必须显式：

```sql
SELECT z
FROM mc_base_normal_draws
WHERE draw_set_id = ?
ORDER BY draw_index;
```

不得依赖 DuckDB 未声明的物理 row order。

### 11.3 Draw bank 复用

- 一个 draw set 可以被多张 option task 复用；
- 基础版本可准备多个 `draw_set_id`，防止所有题共享同一个误差方向；
- 每个 task 只保存 draw set reference，不重复保存数组；
- antithetic task 只保存 base \(Z_i\)，\(-Z_i\) 在 solver 内确定性构造。

---

## 12. Task Config 示例

```yaml
task_id: l3d_call_atm_0001
level: L3d
measure: Q
contract_type: european_vanilla
option_type: call

volatility_source: provided_market_implied
terminal_sampler: exact_lognormal
estimator: crn_central_difference

draw_source: duckdb_frozen_draw_bank
draw_set_id: pcg64_seed_20260812_m50000
num_base_draws: 50000
antithetic: true
total_paths: 100000
dtype: float64

spot_bump:
  type: relative_additive
  size: 0.001
volatility_bump:
  type: absolute
  size: 0.0001
common_random_numbers: required

outputs:
  - mc_price
  - delta
  - gamma
  - vega_per_1pct
  - standard_errors

units:
  vega_scale: 0.01
  rho_scale: 0.0001
  theta_day_count_basis: 365

serialization:
  rounding_digits: 8
  numeric_format: fixed_decimal_string
  rounding_mode: half_even
```

示例 bump 是初始默认值，不应在没有离线 QA 的情况下视为所有 moneyness/maturity 的永恒最优值。task config 才是每题唯一权威来源。

---

## 13. Agent Prompt 与输出 Schema

### 13.1 Public source ownership

沿用当前 accepted package 的单一事实来源原则：prompt 只陈述任务目标并路由到 public
method contract、ordered inputs/draws、submission schema 与 effective runtime contract。
以下内容必须由 versioned method contract 唯一定义，prompt 不再维护第二份副本：

1. `Q`/numeraire、exact terminal BSM 与 market-IV 语义；
2. draw set、`draw_index`、base/total path 与禁止重采样规则；
3. antithetic、CRN、bump 与 estimator 定义；
4. Greek 单位和 pair-level standard error；
5. dtype、逐项 operation/reduction order 与 canonicalization；
6. analytic benchmark 只用于 QA；
7. private verifier/reference 隔离。

Runtime contract 单独拥有 imports、trusted adapters、call budgets、filesystem、network 与资源
限制；submission schema 只拥有输出形状。Rendered prompt、method contract、runtime 与 schema
必须一起进入新的 L3 solver-interface identity。

### 13.2 建议输出

```json
{
  "task_id": "l3d_call_atm_0001",
  "estimator": "crn_central_difference",
  "mc_price": "8.12345678",
  "delta": "0.54321098",
  "gamma": "0.01987654",
  "vega_per_1pct": "0.38765432",
  "standard_errors": {
    "mc_price": "0.01234567",
    "delta": "0.00123456",
    "gamma": "0.00012345",
    "vega_per_1pct": "0.00198765"
  }
}
```

固定小数位字符串比自由浮点 JSON 更适合 exact hard verifier。

---

## 14. Hard Verifier 协议

### 14.1 被验证的对象

L3 hard verifier 比较的是：

> 给定公开 task inputs、公开 frozen draw bank 与规范化算法后，唯一确定的 MC estimator realization。

它不是在 tolerance 内比较 agent 输出是否接近 analytic Greek。由于固定样本下 MC estimator 通常不恰好等于 analytic Greek，直接套 analytic formula 应无法通过。

### 14.2 Canonical 计算顺序

1. 从 task record 读取参数；
2. 按 `draw_index` 升序读取 base \(Z_i\)；
3. 使用 `float64` 计算 \(S_T(Z_i)\) 与 \(S_T(-Z_i)\)；
4. 在路径级形成 price/Greek contributions；
5. 每个 base draw 先形成 antithetic pair mean；
6. 按 `draw_index` 升序执行 contract 指定的 binary64 reduction；第一版建议明确冻结为
   left-to-right scalar accumulation，不能只写成会随 NumPy/backend 改变的“sum”；
7. 计算 pair-level sample standard error，使用 `ddof=1`；
8. 在 reduction 完成后做 Greek unit scaling；
9. 最后一步 canonical rounding；
10. 逐字段 exact compare。

### 14.3 Verifier 分层

- public schema verifier：检查字段、类型、有限值与小数位；
- private numerical verifier：从 inputs/draw bank 重新计算 reference estimator；
- leakage guard：private answers 和 verifier source 不进入 agent sandbox；
- method guard：analytic answer 因不等于 frozen MC realization而自然失败。

### 14.4 Allowlist 建议

当前 analytic/IV Agent profile 只允许 Python standard library 和 counted trusted adapters，
并显式拒绝 `numpy` 与 `duckdb` direct import。L3 在实现前必须二选一并版本化：

- 保持 stdlib-only，以 trusted adapter 返回 ordered draws；或
- 新建 L3 environment/profile/lock，明确固定 NumPy/DuckDB 版本及其 reduction/normal-value
  contract。

不能只修改说明文档或复用旧 profile 来获得新能力。

Agent 侧禁止：

- QuantLib、py_vollib 或其他直接返回 Greeks 的库；
- private verifier/reference output；
- 外部网络；
- 未在 allowlist 中的预计算 pricing service。

Verifier 可以独立使用 trusted analytic implementation 做 QA，但正式 exact target 应由规范化 MC reference solver 重算。

---

## 15. 测试计划

### 15.1 Unit tests

#### Terminal sampler

- \(Z=0,\pm1\) 的手算 snapshot；
- \(q\neq0\)；
- `float64` dtype；
- \(\sigma\le0\)、\(T\le0\) 等非法输入处理。

#### Payoff

- call/put ITM、ATM、OTM；
- \(S_T=K\) 时 payoff 为 0；
- pathwise indicator 在 equality 的约定不影响连续分布结果。

#### Antithetic

- `num_base_draws` 与 `total_paths` 一致；
- 每个 \(Z_i\) 只与 \(-Z_i\) 配对；
- standard error 基于 pair means；
- 奇数 total path 配置被拒绝。

#### CRN bumps

- base/up/down 使用同一 draw array；
- spot bump 为 additive \(h_S=\varepsilon_SS_0\)；
- volatility bump 同时改变 drift correction 与 diffusion term；
- rate bump 同时改变 drift 与 discount；
- vega/rho/theta scaling 只发生一次。

#### Pathwise/LR

- call/put Delta sign；
- pathwise Vega 公式 snapshot；
- LR weights 逐项手算 snapshot；
- 禁止 naive pathwise Gamma；
- mixed Gamma 与公式一致。

### 15.2 Integration tests

在固定参数网格上比较 MC estimator 与 analytic benchmark：

- call 与 put；
- ITM/ATM/OTM；
- short/medium/long maturity；
- \(q=0\) 与 \(q>0\)；
- low/medium/high IV。

统计 QA 可以使用：

\[
|\widehat G-G_{\mathrm{analytic}}|
\le c\,\widehat{\operatorname{SE}}(\widehat G)
+\text{finite-difference bias allowance},
\]

其中 \(c\) 仅用于离线 QA，不进入 agent hard verifier。对 pathwise/LR 等无 bump estimator，可用固定的宽松多标准误 sanity gate；不能写成容易随机波动的脆弱 CI test。

### 15.3 Deterministic snapshot tests

至少冻结以下 snapshot：

1. L3a plain call price；
2. L3b antithetic put price；
3. L3d CRN Delta/Gamma/Vega；
4. L3e pathwise Delta/Vega；
5. L3f LR Delta/Gamma/Vega；
6. 相同 task 在 solver 与 verifier 中输出逐字符一致。

### 15.4 Property tests

- call Delta 通常位于 \([0,e^{-qT}]\)；
- put Delta 通常位于 \([-e^{-qT},0]\)；
- analytic Gamma/Vega 非负；
- position 取负时 portfolio Greeks 符号同步翻转；
- 相同 frozen draws 下重复运行输出完全一致；
- MC module 不接受或访问 \(P\)-measure diffusion nodes。

注意：有限样本 LR estimator 可能偶尔违反理论符号，因此符号性质适合 analytic/高路径 QA，不应盲目作为每个 stochastic estimator realization 的硬断言。

---

## 16. Mutation 设计

同一 L3 子层内可以沿以下维度 mutation：

| 维度 | 基础 | 中级 | 高级 |
|---|---|---|---|
| option type | call | call/put | long/short position |
| moneyness | near ATM | moderate ITM/OTM | deep ITM/OTM |
| maturity | medium | short/long | near-expiry |
| dividend | \(q=0\) | constant \(q>0\) | heterogeneous \(q\) |
| estimator | CRN bump | pathwise | LR/mixed |
| variance reduction | none | antithetic | 后续 control variate |
| path count | small | medium | large |
| outputs | one Greek | Greek triplet | price + Greeks + SE |
| aggregation | single option | option chain | multi-asset portfolio vector |

第一版 mutation 范围应避免同时出现：deep OTM、near-expiry、low IV、LR Gamma 和小 path count。该组合极易产生几乎全零 payoff 与不稳定高方差，不适合作为基础能力测试。

### 16.1 推荐难度原则

- level 由 estimator/推理依赖决定；
- path count 主要改变计算量和 sampling error，不应单独升 level；
- estimator 名称必须出现在 prompt/config 中，不让 agent 猜方法；
- 一题尽量设置一个 primary target，减少多输出错误传播；
- composite task 可以输出 price + Greek triplet，但应留到各单项通过后。

---

## 17. 关键风险与处理

| 风险 | 后果 | 处理 |
|---|---|---|
| 把 \(P\)-diffusion 用于 L3 | 测度混乱、答案错误 | schema 与 module 边界禁止传入 |
| 只给 seed 不冻结实现 | runtime 间 draws 不一致 | 推荐共享 frozen draw bank |
| up/down 独立抽样 | Greek 方差随 bump 缩小而爆炸 | 强制 CRN |
| 把 \(2M\) antithetic paths 当独立 | standard error 偏小 | pair means 是独立单位 |
| naive pathwise Gamma | Gamma 错误为 0 | 只允许 CRN/LR/mixed |
| bump 太小 | cancellation/high variance | config 固定并离线 QA |
| bump 太大 | truncation bias | 按 maturity/moneyness 分层校准 |
| Vega/Rho 单位混乱 | 相差 100 或 10,000 倍 | raw 与 scaled 字段分离 |
| Theta 符号混乱 | \(\partial_TV\) 与 market Theta 相反 | 固定 \(\Theta=-\partial_TV\) |
| 过早 rounding | 结果漂移 | 仅最终 serialization rounding |
| analytic formula 代替 MC | 没有执行指定算法 | frozen realization exact verifier |
| 极端 OTM 全零 payoff | 任务退化 | curriculum 后置并提高 path count |

---

## 18. 实施步骤与验收门

### Step 1：冻结 conventions

交付：

- Greek definitions；
- units/scaling；
- bump semantics；
- draw/pair/path-count semantics；
- rounding 与 output schema。

验收：同一字段在 authoring、solver、verifier 和文档中定义一致。

### Step 2：实现 exact terminal MC core

交付：

- terminal sampler；
- payoff；
- plain estimator；
- antithetic pair reducer；
- pair-level standard error。

验收：L3a/L3b snapshot tests 与 analytic price convergence QA 通过。

### Step 3：实现 CRN bump Greeks

交付：

- Delta/Gamma/Vega；
- per-path contribution API；
- antithetic reduction；
- bump validation。

验收：固定 draw snapshots、call/put grid 与 analytic QA 通过。

### Step 4：实现 pathwise/LR estimators

交付：

- pathwise Delta/Vega；
- LR Delta/Gamma/Vega；
- optional mixed Gamma；
- estimator-specific SE。

验收：三类 estimator 在固定测试网格上方向一致，并在预设 sampling envelope 内接近 analytic benchmark。

### Step 5：建立 DuckDB draw bank 与 authoring

交付：

- draw-set metadata；
- ordered base draws；
- L3 task records；
- mutation generator；
- public inputs/private outputs 分离。

验收：任意 task 可只凭 task record + draw bank 完整重算，不依赖隐藏 global state。

### Step 6：实现 hard verifier

交付：

- schema validator；
- private reference solver；
- canonical serializer；
- exact field comparison；
- answer/verifier isolation tests。

验收：

- 正确 reference trajectory 通过；
- analytic Greek 替代 MC realization 时失败；
- 修改 draw order、bump、unit 或 antithetic SE convention 时失败；
- 重复运行逐字符一致。

### Step 7：加入 Rho/Theta 与 portfolio

交付：

- CRN Rho/Theta；
- pathwise/LR Rho；
- portfolio positions/multipliers；
- multi-underlying Greek vector schema。

验收：单项与聚合的线性关系、单位和 sign tests 通过。

---

## 19. 第一版推荐参数

第一版 baseline 可从以下配置开始，再由离线误差实验调整：

| 参数 | 推荐初值 |
|---|---:|
| `num_base_draws` | 50,000 |
| `total_paths` | 100,000 |
| antithetic | true |
| dtype | float64 |
| relative spot bump | \(10^{-3}\) |
| absolute volatility bump | \(10^{-4}\) |
| absolute rate bump | \(10^{-4}\) |
| time bump | \(1/365\) year |
| vega output scale | \(10^{-2}\) |
| rho output scale | \(10^{-4}\) |
| output digits | 8 |

这些是工程 baseline，不是待估计参数。正式 task 中必须将它们写入 config/prompt。

---

## 20. Definition of Done

L3 第一版只有在以下条件全部满足时才算完成：

- [ ] L3 代码路径没有读取 \(P\)-measure drift/diffusion/correlation；
- [ ] European BSM 使用 exact terminal sampling，无 Euler bias；
- [ ] plain 与 antithetic MC price 已实现；
- [ ] CRN Delta/Gamma/Vega 已实现；
- [ ] pathwise Delta/Vega 已实现；
- [ ] LR Delta/Gamma/Vega 已实现；
- [ ] naive pathwise Gamma 被显式禁止；
- [ ] antithetic standard error 基于 pair means；
- [ ] raw/scaled Greek units 唯一且有测试；
- [ ] frozen draw bank、draw order 和 dtype 已固定；
- [ ] agent output 是 fixed-draw MC realization，不是 analytic Greek；
- [ ] solver/verifier 对固定 task 的序列化结果逐字符一致；
- [ ] analytic BSM grid QA 通过；
- [ ] public database/prompt 不包含 reference answers；
- [ ] task pack 包含 database、prompt、pytest verifier、reference trajectory 与 config；
- [ ] allowlist/sandbox 明确阻止直接调用 Greeks package 或读取 private verifier。

截至 2026-08-12，上述条目全部未完成；现有 `solver/mc/__init__.py` 仅声明未来模块边界。

---

## 21. 最终实施决策摘要

第一版应先实现以下稳定主干：

\[
\text{provided market IV}
\rightarrow
\text{exact }Q\text{-terminal draws}
\rightarrow
\begin{cases}
\text{CRN Delta/Gamma/Vega},\\
\text{pathwise Delta/Vega},\\
\text{LR Delta/Gamma/Vega}
\end{cases}
\rightarrow
\text{pair-level SE}
\rightarrow
\text{exact rounded output}.
\]

其中：

- CRN bump 是第一版默认 estimator；
- pathwise/LR 是同一 L3 内的方法 mutation；
- Gamma 不允许 naive pathwise；
- Rho/Theta 与 portfolio 在核心链路稳定后加入；
- frozen normal draw bank 是 hard verifier 的推荐随机数协议；
- analytic BSM Greeks 只用于 QA，不是 task target；
- \(P\)-measure piecewise-linear diffusion 从 L4 才开始出现。
