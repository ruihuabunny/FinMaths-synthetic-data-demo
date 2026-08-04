# 金融数学与衍生品市场数据的确定性 ORM Hard-Verifier 框架

**可合成并冻结的市场快照、手工实现 Greeks/IV/Smile、固定 Seed 与 Pytest 调包验收**

*A Deterministic ORM Hard-Verifier Framework for Financial Mathematics and Derivatives Markets*

- 作者：Ruihua Luo
- 日期：August 2026
- 版本：`Final derivatives ORM design`

---

## 摘要

本文给出金融数学与衍生品市场任务的独立数据方案。与普通统计/数据科学领域不同，出题端以固定版本的 QuantLib 作为统一市场数据生成器：在固定 generator configuration、seed 与 RNG 下生成 underlying daily panel、完整 option daily chain 及 pricing metadata，materialize 后立即冻结为只读 market snapshot。Greeks、implied volatility、smile/surface、VaR 与 ES 不再分别造数据，而是从同一快照派生 task variants。随后固定定价模型、day-count、Greek convention、IV 求根算法、smile/surface 拟合方法、VaR/ES 定义、数值精度、舍入模式与 canonical output schema，使 solver 与 verifier 在同一 method contract 下生成逐字段唯一的规范答案。

Solver 的核心训练目标是自己实现 Greeks、implied volatility 与 volatility smile/surface 计算。其执行环境不得调用 QuantLib、py_vollib、mibian、rateslib 等现成衍生品定价接口，也不得用封装好的 IV/Greek/smile API 绕过推导；允许的基础数值原语由 task contract 明确列出。Trusted verifier 不受这一限制：它直接以 `pytest` 调用固定版本且与题目方法一致的权威数值/金融包复算标准答案，再按题目规定的精度、舍入与序列化规则生成 canonical strings。Hard verifier 不设置绝对或相对 tolerance，而是逐字段执行 exact equality；全部测试通过时 outcome reward 为 $1$，否则为 $0$。

**关键词：** financial derivatives；Greeks；implied volatility；volatility smile；synthetic market data；fixed seed；pytest；package oracle；outcome reward model；hard verifier

## 最终设计结论

#### 最终方案

金融衍生品市场单独建域：authoring 端用 pinned QuantLib 统一生成 underlying daily prices、option daily prices 与定价元数据；每个 variant 固定 generator、seed 与全部市场约定，生成后冻结 snapshot；Greeks/IV/smile/surface/VaR/ES 都从该快照派生。Task contract 同时约束 solver 与 verifier 的公式/算法、数值精度、舍入和输出；solver 禁止调用现成 Greeks/IV/smile 包，必须从允许的基础原语实现；trusted verifier 用 `pytest` 调固定版本、同方法的金融包复算，canonicalize 后以 `==` 精确验收。全部 tests pass 才有 $R_{\mathrm{ORM}}=1$。

### 为什么金融子域允许造数据

Greeks、IV 与 volatility surface 任务的 ground truth 可以由已知参数与定价模型反向构造。相比真实市场数据，合成数据具有三项优势：

- 可精确控制 moneyness、maturity、rate、volatility、skew 与 curvature；
- 可覆盖极端但合法的边界区域，并保存真正的 latent parameters；
- 可由 seed 完整重放，天然适合 executable ORM。

“允许造数据”仅指 variant authoring 阶段不必继承同一个公开 $D_0$。一旦某个 variant 的 snapshot 被 materialize，solver rollout 期间仍必须保持不变。

## 双环境权限模型

### Authoring / solver / verifier 三个边界

*表 1：三个环境的职责与权限*

| 环境             | 可以做什么                                                              | 不可以做什么                                                                      |
|:-----------------|:------------------------------------------------------------------------|:----------------------------------------------------------------------------------|
| Authoring        | 合成 market snapshot；固定 seed、模型与约定；生成 reference contract。  | 发布不稳定、无定义或无法唯一复算的 variant。                                      |
| Solver           | 读取冻结输入；用允许的基础数值原语自行实现公式、求根、拟合与输出。      | 调用现成 Greeks、IV、pricing、smile/surface convenience APIs；读取 hidden tests。 |
| Trusted verifier | 用 pytest 调固定版本的金融/数值包复算；检查 imports、数值与金融不变量。 | 把 reference output、包调用结果或 hidden test 内容暴露给未完成的 policy。         |

#### 最重要的权限分离

**被测 LLM 不能调包算 Greeks/IV/smile；verifier 可以而且应当调包。**禁止对象是 solver 的捷径，而不是 trusted oracle。Verifier 的 package/backend/version/function/convention 与 method id 均写入 verifier contract；若 variant 指定 analytic、finite difference、tree、PDE、Monte Carlo、Newton、bisection 或特定 smile fit，pytest oracle 也必须使用对应方法，不能用另一种算法只比较“接近的结果”。

### Solver 的允许与禁止列表

允许范围按题目声明，典型最小集合为：

- Python 标准库中的 `math`、`decimal`、`json`、`csv`；
- 若任务允许，使用 NumPy 进行基础 array 与 linear algebra；
- agent 自己写出的 normal CDF/PDF、Black–Scholes 公式、finite difference、Newton/bisection 与 least-squares 逻辑。

典型禁止范围包括：

- QuantLib、py_vollib/vollib、mibian、rateslib 及其他衍生品定价库；
- 任何直接返回 option price、Greek、IV、smile 或 surface 的 convenience API；
- 当 task 要求自行实现求根时，封装好的 implied-volatility solver；
- 当 task 要求自行拟合 smile 时，自动选择模型或超参数的黑箱校准器。

禁止规则不能只写在 prompt 中。执行层应同时使用 import allowlist、依赖隔离、AST/import manifest 检查与运行时审计。

## 合成市场快照

### 可重放生成

第 $v$ 个 market snapshot 定义为 $$D_v^{\mathrm{mkt}}
 =G_{\mathrm{mkt}}\!\left(\theta_v;\,
 \texttt{generator\_version},\texttt{seed}_v,\texttt{RNG}_v\right),$$ 其中 $\theta_v$ 可以包含： $$S_0,\; r,\; q,\; T,\; K,\; \sigma,\;
 \text{curve parameters},\;\text{skew},\;\text{curvature},\;
 \text{quote-noise model}.$$ 生成后保存 canonical serialization、snapshot id 与 hash： $$\operatorname{hash}(D_{v,t}^{\mathrm{mkt}})
 =\operatorname{hash}(D_v^{\mathrm{mkt}})
 \quad\text{for all solver steps }t.$$

### QuantLib 统一生成器与三表合同

生产 authoring job 将 $G_{\mathrm{mkt}}$ 实现为 pinned QuantLib pipeline，而不是只使用单一 GBM 公式。模型 registry 可以包含 GBM/BSM baseline、Heston、jump diffusion/Bates、local volatility，以及 SVI/SABR smile/surface；每个 variant 必须保存实际使用的 QuantLib version、Python binding、process class、pricing engine、参数、离散化方法与 engine id。GBM 只作为解析基线，不作为唯一 generator。

统一 snapshot 至少由以下三类对象组成：

| 对象 | 必需字段 |
|:---|:---|
| `underlying_daily` | `date, underlying_id, spot_open, spot_high, spot_low, spot_close, adjusted_close, volume, dividend, corporate_action`；若某字段不由当前模型生成，必须固定为明确的 `null`/常数规则。 |
| `option_daily` | `date, underlying_id, option_id, call_put, strike, expiry, exercise_style, settlement_type, contract_multiplier, bid, ask, mid, settlement_price, volume, open_interest`。 |
| `pricing_metadata` | `valuation_timestamp, currency, discount_curve/risk_free_rate, dividend_curve/dividend_yield, borrow_or_carry_rate, calendar, day_count, physical_dynamics, pricing_dynamics, pricing_model, pricing_engine, generator_version, seed, RNG, input_precision, canonicalization`。 |

`underlying_daily` 中用于历史收益、VaR/ES 的路径属于物理测度 $P$；`option_daily` 的定价属于风险中性测度 $Q$。两者可共享当日 spot、variance state 与市场日期，但 drift、风险溢价及模型参数必须分别保存为 `physical_dynamics` 与 `pricing_dynamics`。如果某题只需 flat rate/dividend，可以把完整 curves 简化为固定 $r,q$，但简化规则本身仍是合同字段。

Authoring pipeline 固定为：QuantLib 先生成 underlying path/state，再对每个 valuation date 和 $K\times T$ grid 用指定 QuantLib pricing engine 生成 option chain，最后按题面精度量化并冻结。题目的 IV 真值必须从 solver 实际可见的、已量化 option price 按指定求根法重新反解，不能直接拿 QuantLib 内部未公开的 latent volatility 作答案。这样一套数据即可派生 Greeks、IV、smile/surface、underlying VaR/ES 与 option-portfolio VaR/ES variants；不需要维护彼此不一致的独立“Greeks 数据表”或“VaR 数据表”。

### 最低有效性门控

即使数据可以合成，也必须先保证任务有定义：

- $S_0>0,K>0,T>0,\sigma>0$；
- option quote 位于模型和合约约定允许的界内；
- IV 任务存在且只有一个声明区间内的根；
- rate/dividend、discounting、calendar 与 day-count 明确；
- surface task 的 grid、缺失 pattern 与 extrapolation policy 明确；
- 若目标是无套利 smile/surface，则额外执行 monotonicity、convexity 与 calendar checks。

如果题目故意要求识别 arbitrage violation，则违规本身可被合成，但必须作为 task truth 明确记录，而不能意外混入普通 IV/Greek 题。

## Task Variant 与唯一方法合同

### 母对象与 variant

金融 variant 可写为 $$v=(D_v^{\mathrm{mkt}},q_v,M_v,C_v,O_v,V_v,S_v),$$ 其中 $M_v$ 是 model/method contract，$C_v$ 是 convention contract；

$O_v$ 是 output contract，$V_v$ 是 verifier contract，$S_v$ 是 target skills。

### 必须冻结的金融约定

<a id="tab:contract"></a>

*表 2：金融衍生品 task contract 的必需字段*

| 字段                 | 内容                                                                                                                       |
|:---------------------|:---------------------------------------------------------------------------------------------------------------------------|
| Instrument           | call/put、European/American、exercise、payoff、notional、currency。                                                        |
| Generator backend    | QuantLib version/Python binding、process class、pricing engine、model parameters、generator config id。                    |
| Physical dynamics    | 生成 underlying history 的 $P$-measure process、drift/risk premium、discretization、time grid 与 path state。              |
| Pricing dynamics     | 生成 option chain 的 $Q$-measure model/parameters、engine 与 calibration policy；不得与 $P$-measure 隐式混用。             |
| Snapshot schema      | `underlying_daily`、`option_daily`、`pricing_metadata` 的字段、类型、主键、null policy、单位与可见性。                      |
| Market state         | spot/forward、strike、maturity、rate、dividend、curve 与 quote definition。                                                |
| Clock convention     | valuation date、calendar、business-day adjustment、day-count、time-to-expiry。                                             |
| Pricing model/method | Black–Scholes–Merton、Black-76、tree、PDE、Monte Carlo 等唯一模型与唯一 engine/method。                                    |
| Greek definition     | spot/forward Greek、holding-what-fixed、analytic/finite-difference/MC estimator、bump/stencil 与 scaling。                 |
| IV method            | objective price、vol bracket、algorithm id、initial state、固定迭代次数或确定性停止规则、fallback 与 failure output。      |
| Smile/surface method | moneyness coordinate、basis、weights、fit objective、linear-algebra routine、regularization、interpolation/extrapolation。 |
| Randomness           | generator seed、RNG、noise draw order；MC 还需冻结 shocks/path order、variance reduction 与 reduction order。              |
| Numerics             | dtype、运算与归约顺序、工作精度、quantization checkpoints、舍入模式与 non-convergence output。                             |
| Submission           | JSON/table fields、row order、units、固定 decimal places、canonical string 与 serialization order。                        |

金融衍生品 task contract 的必需字段

### Greek 单位必须显式

许多“答案不唯一”其实来自 convention 未写清。每题至少要声明：

- Vega 是对 $\Delta\sigma=1$ 还是一个 volatility point（$0.01$）；
- Rho 是对 $\Delta r=1$ 还是一个 percentage point；
- Theta 是 annual、calendar-day 还是 trading-day decay；
- Delta/Gamma 对 spot 还是 forward，是否包含 discount/dividend factor；
- rate 与 dividend 是连续复利、简单利率还是离散复利。

## 适合生成的任务族

*表 3：确定性衍生品任务族*

| 任务族                    | Solver 需手工实现                                                             | Verifier 可调用的 package oracle                                                            |
|:--------------------------|:------------------------------------------------------------------------------|:--------------------------------------------------------------------------------------------|
| European analytic pricing | BSM/Black-76 公式、normal CDF/PDF                                             | QuantLib 或 py_vollib 的 analytic engine；输出按共同 canonicalizer 精确化。                 |
| Analytic Greeks           | Delta/Gamma/Vega/Theta/Rho 及约定换算                                         | QuantLib/py_vollib analytic Greeks；不得换成 finite difference。                            |
| Finite-difference Greeks  | 指定 stencil、bump、边界处理与运算顺序                                        | 同一 package price engine 后由 pytest 执行完全相同的 stencil；不得直接读取 analytic Greek。 |
| Implied volatility        | 指定 Newton/bisection/Brent、bracket、初值与迭代规则                          | package pricing function 加同一 root algorithm，或调用算法完全匹配的 pinned solver。        |
| Smile fit                 | 指定坐标、design matrix、OLS/WLS/regularization 与线性代数例程                | NumPy/SciPy/statsmodels 中同一 routine、参数、row 与 coefficient order。                    |
| Monte Carlo price/Greeks  | 指定 RNG/shocks、path construction、estimator、variance reduction 与归约顺序  | pytest 使用同一冻结 paths 与 estimator；不得改用 analytic/PDE oracle。                      |
| VaR/ES                    | 指定 parametric、historical 或 MC 定义、quantile convention 与 scenario order | pytest 使用同一风险方法与完全相同的 scenario/quantile rule。                                |
| Surface interpolation     | 固定 grid、插值、边界与 extrapolation                                         | QuantLib/SciPy 的同一 pinned interpolator 与参数。                                          |

每个 variant 只选择一个明确任务合同；analytic Greeks、finite-difference Greeks 与 Monte Carlo Greeks 应作为不同 variants。可以在一个 episode 中串联 price $\rightarrow$ IV $\rightarrow$ Greeks $\rightarrow$ smile，但每个阶段的方法、精度、舍入 checkpoint 与 canonical output 都必须冻结。

## Agent Trajectory Schema

### 统一九字段

顶层 schema 固定为 $$\boxed{
\begin{gathered}
\text{Problem, Context, Assumptions, Skills, Evidence,}\\
\text{Intermediate Reasoning, Verification, Confidence, Outcome}.
\end{gathered}}$$

*表 4：九字段在衍生品任务中的含义*

| 字段                   | 金融衍生品含义                                                                                           |
|:-----------------------|:---------------------------------------------------------------------------------------------------------|
| Problem                | instrument、冻结 market snapshot 与 price/Greek/IV/smile 目标。                                          |
| Context                | model、day-count、rate/dividend、Greek units、method、seed、允许/禁止 package。                          |
| Assumptions            | 模型假设、quote validity、root existence、smoothness 与 no-arbitrage scope。                             |
| Skills                 | derive formula、implement normal CDF、root solve、differentiate、fit smile、check invariants。           |
| Evidence               | 输入 snapshot、真实 tool output、iteration trace、residuals 与 fitted artifacts。                        |
| Intermediate Reasoning | state–action–observation–next-state trajectory，不包含 hidden oracle。                                   |
| Verification           | import/method audit、pytest package recomputation、canonical exact equality、invariants 与 test report。 |
| Confidence             | convergence flag、residual、stability 与 verified/unverified 状态。                                      |
| Outcome                | canonical numeric/table answer、tests pass/fail 与 terminal reward。                                     |

### 推荐 trajectory

典型 episode 的 actions 为： $$\begin{aligned}
&\text{parse contract}
\rightarrow \text{validate inputs}
\rightarrow \text{derive conventions}\\
&\rightarrow \text{implement primitives}
\rightarrow \text{solve price/IV/Greeks}
\rightarrow \text{fit smile}\\
&\rightarrow \text{check residuals/invariants}
\rightarrow \text{serialize output}.
\end{aligned}$$ 每一步保存真实 observation；hidden package output 只在 episode 终止后由 verifier 使用。可以保留 iteration history 作为 agent data，但不通过 step reward 暗示正确答案。

## Pytest Package-Backed Hard Verifier

### Verifier contract

每个 variant 的 verifier manifest 至少包含： $$\begin{gathered}
(\texttt{backend package},\texttt{package version},\texttt{function/engine},\\
\texttt{method id},\texttt{market conventions},\texttt{dtype/operation order},\\
\texttt{decimal places},\texttt{rounding mode},\texttt{canonical schema},\\
\texttt{banned imports},\texttt{test ids}).
\end{gathered}$$

Trusted verifier 直接在 pytest fixture 中构造 package objects、加载冻结 market snapshot，并按与 solver 相同的 method contract 复算 outputs。Oracle 与 submission 都被转换为固定小数位的 canonical decimal strings，再逐字段或逐字节比较： $$\texttt{assert canonical(submission) == canonical(package\_oracle)}.$$ 因此 verifier 不调用 `pytest.approx`、`math.isclose`、`numpy.isclose` 或 `allclose`，也不保存 `atol`/`rtol`。数值算法内部若需要停止规则，该规则属于 method contract；它不是 hard-verifier tolerance。

### 测试层

<a id="tab:tests"></a>

*表 5：金融 hard verifier 的 pytest tests*

| 测试                 | 断言                                                                                        |
|:---------------------|:--------------------------------------------------------------------------------------------|
| Submission schema    | 必需字段、row/strike/maturity 顺序、单位、无 NaN/Inf。                                      |
| Snapshot identity    | market snapshot id/hash、seed、generator version 一致。                                     |
| Import compliance    | 无禁止库、无隐藏文件访问、无动态安装/网络加载。                                             |
| Method identity      | solver 声明的 method id、关键 method artifacts 与 verifier contract 精确一致。              |
| Price                | package oracle 与提交值经过共同 canonicalization 后字符串完全相等。                         |
| Greeks               | method、definition、scaling 与 canonical value 逐字段完全相等。                             |
| Implied volatility   | 同一 root method 产生的 canonical IV、status 与 iteration contract 完全相等。               |
| Smile/surface        | coordinate、basis、weights、routine、coefficient order 与 canonical coefficients 完全相等。 |
| Financial invariants | put–call parity、bounds、sign、monotonicity/convexity 或任务指定性质。                      |
| Edge cases           | deep ITM/OTM、short maturity、low vega、root failure 的指定行为。                           |

金融 hard verifier 的 pytest tests

### 二值 Outcome Reward

$$R_{\mathrm{ORM}}=
\mathbf 1\!\left\{
\bigwedge_{j=1}^{J}
\texttt{pytest\_test}_j=\texttt{PASS}
\right\}.$$ 单项测试的偏差和错误类型可以记录进 diagnostics，但任何一项失败都不能被其他正确项或长推理过程抵消。若任务希望分阶段 curriculum，应拆成多个独立 variants，而不是把 hard verifier 改成主观加权分。

## 防止绕过与数据污染

### 仅扫描源码不够

静态 AST 可以发现直接 `import QuantLib`，但不能单独防住反射、动态 import、subprocess、预装服务或复制出的 package output。因此 solver image 应采用：

- 只安装 allowlist 依赖；
- 禁止网络、动态安装与任意系统调用；
- 记录 import/runtime audit；
- 将 hidden verifier 放在另一个不可读容器；
- verifier 只解析 solver 的 JSON/表格，不执行其代码。

### 避免 convention mismatch

Package oracle 本身不自动解决语义和方法错配。Authoring 时必须先用一条公开 sanity case 验证 backend 的：

- option type、discounting 与 dividend treatment；
- vega/rho scaling；
- theta sign 与 annual/day convention；
- IV price convention、root algorithm 与 bracket；
- smile coordinate、weights、linear-algebra routine 与 coefficient order；
- MC 的 RNG、path、estimator 与 reduction order；
- tree/PDE 的 grid、time step 与 boundary method；
- canonical decimal places、rounding mode 与 serialization。

只有 wrapper 的数学对象、算法和 convention 全部与 task contract 对齐后，package output 才可作为 canonical answer。若包只提供另一种方法，即使数值接近也不能作为该 variant 的 oracle。

## 质量控制与验收

### Authoring-side checks

1.  用固定 seed materialize market snapshot 并保存 hash；
2.  检查输入定义域、price bounds、IV root existence 与 method feasibility；
3.  用同方法的 pinned package backend 生成 canonical answer；
4.  重复运行 pytest，确认 expected files 在目标环境中 byte-identical；
5.  拒绝落在舍入边界附近或跨目标平台不能稳定 canonicalize 的样本；
6.  将任一 canonical 字段最后一位改变 $1$，确认 exact-equality test 失败；
7.  检查 solver 容器确实无法 import 被禁 packages；
8.  将 package lock、wrapper version 与 test manifest 纳入 verifier id。

### 发布门槛

一个金融衍生品 variant 只有同时满足下列条件才可发布：

- generator/seed/RNG/config 完整且 snapshot 已冻结；
- instrument、model、calendar、day-count 与 Greek/IV/smile conventions 无歧义；
- solver allowlist/denylist 可由环境强制执行；
- package-backed pytest 能以同一方法复算全部 canonical outputs；
- 每个字段的精度、舍入、canonical representation 与 edge-case failure behavior 已指定；
- verifier manifest 不含数值 tolerance，全部数值字段使用 exact equality；
- hidden tests 与 package oracle 不对 solver 可见；
- tests 全通过才记录 $R_{\mathrm{ORM}}=1$。

#### 一句话总结

金融衍生品市场：**出题端用 pinned QuantLib 统一生成 underlying daily panel、option daily chain 与 pricing metadata，生成后冻结；Greeks/IV/smile/surface/VaR/ES 全部从同一快照派生；LLM 必须手搓规定方法，pytest 必须以相同方法调固定版本金融包复算；双方按同一精度、舍入和 schema 生成 canonical output，并以 exact equality 接受唯一答案，不设置 verifier tolerance。**

## 参考资料

1.  <a id="ref-1"></a> pytest documentation. *pytest: helps you write better programs*.  
    <https://docs.pytest.org/>

2.  <a id="ref-2"></a> QuantLib. *A free/open-source library for quantitative finance*.  
    <https://www.quantlib.org/>

3.  <a id="ref-3"></a> py_vollib documentation and source repository.  
    <https://github.com/vollib/py_vollib>
