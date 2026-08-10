# 合成期权链的 IV、Greeks 与 Smile 确定性 ORM Agent Trajectory 样例

**固定 Seed、Black--Scholes--Merton 手工实现、Solver 禁止调包与 Pytest Package Oracle**

*A Deterministic ORM Agent-Trajectory Example for Implied Volatility, Greeks, and a Volatility Smile*

- 作者：Ruihua Luo
- 日期：August 2026
- Variant：`DERIV-BSM-IV-GREEKS-SMILE-EXACT-v2`

---

> 实现状态（2026-08-10）：本文件是说明性 design example，不是 checked-in accepted
> package，也不是当前市场 surface 的无套利认证。实际
> `bsm_market_implied_greeks_v1` 使用 bid/ask midpoint、8 underlyings、2 expiries、每 expiry
> 5 strikes、paired call/put、共 160 rows 和 8-decimal outputs；它不拟合 smile，也不向
> Solver 开放 NumPy。实际 artifact/contract 以 [`task_packages/README.md`](../../task_packages/README.md)
> 与 package 生成的 prompt 为准。

## 摘要

本文给出一个可转成 agent/RL 数据的金融衍生品确定性设计样例。Authoring environment
冻结 valuation-date spot 与合约/curve 状态，再用 pinned QuantLib 的 Q-measure
Black–Scholes–Merton analytic engine 生成 `option_daily` quotes；本例不模拟 P-measure
underlying transition。NumPy PCG64 和 seed $20260804$ 只负责按固定顺序采样 illustrative
latent smile perturbations。题面所见的 spot、rate、dividend、maturity、strike 和舍入后 call
mid 随即被冻结。Solver 必须从基础数值原语自行实现 normal CDF/PDF、implied-volatility
求根、Delta/Gamma/Vega/Theta/Rho 以及 log-forward-moneyness 上的二次 smile OLS，不得调用
现成 Greeks、IV、pricing 或 smile package。

Trusted verifier 与 solver 的权限相反。Verifier 的 pytest fixture 可以直接调用固定版本的 QuantLib 复算 analytic BSM price/Greeks，并调用 NumPy 的固定 linear-algebra routine 复算 smile coefficients；但 verifier 必须与 solver 使用同一个 80-step bisection、analytic Greek 与 normal-equation OLS method contract。双方再按固定小数位与 round-half-even 规则生成 canonical strings，pytest 逐字段执行 exact equality，不设置任何数值 tolerance。生产训练时 reference values 位于 hidden verifier 中；本文仅为说明数据结构而展示。

**关键词：** synthetic option chain；Black–Scholes–Merton；implied volatility；Greeks；volatility smile；fixed seed；pytest；QuantLib；py_vollib；hard verifier

## Market Snapshot 的生成与冻结

### 固定 market conventions

*表 1：合约与市场输入*

| 字段                 | 固定值                                              |
|:---------------------|:----------------------------------------------------|
| `instrument`         | European call，无提前行权，无离散现金流             |
| `spot` $S_0$         | $100.0$                                             |
| `risk-free rate` $r$ | $0.03$，连续复利，flat                              |
| `dividend yield` $q$ | $0.01$，连续复利，flat                              |
| `time to expiry` $T$ | $0.25$ years，ACT/365F 已折算后的精确 year fraction |
| `notional`           | 每一单位 underlying；价格与 Greeks 均为 per-unit    |
| `option type`        | call                                                |
| `generator backend`  | pinned QuantLib/Python binding；版本写入 environment lock |
| `P history`          | 本例只条件于 valuation-date spot，不使用或模拟 P transition；若扩展 daily history，必须另行声明 P-measure process、risk premia 与 state law |
| `Q pricing engine`   | QuantLib analytic European BSM engine               |
| `generator RNG`      | NumPy PCG64                                         |
| `generator seed`     | $20260804$                                          |
| `snapshot id`        | `DERIV-SYNTH-CALLCHAIN-20260804-v1`                 |

### 统一 daily data package

QuantLib authoring job 先生成统一市场数据包，再从中选择当前 task 所需的一个 valuation-date slice：

| 文件/对象 | 本例保留的核心字段 |
|:---|:---|
| `underlying_daily.csv` | `date, underlying_id, spot_open, spot_high, spot_low, spot_close, adjusted_close, volume, dividend, corporate_action`；本例使用 valuation date 上的 `spot_close=100.0`。 |
| `option_daily.csv` | `date, underlying_id, option_id, call_put, strike, expiry, exercise_style, settlement_type, contract_multiplier, bid, ask, mid, settlement_price, volume, open_interest`；当前任务从中读取 call `mid`。 |
| `pricing_metadata.json` | `valuation_timestamp, currency, risk_free_rate/discount_curve, dividend_yield/dividend_curve, borrow_or_carry_rate, calendar, day_count, physical_dynamics, pricing_dynamics, pricing_model, pricing_engine, generator_version, seed, RNG, input_precision, canonicalization`。 |

本例只展示 IV/Greeks/smile 所需的一日、单到期 slice；同一 generator family 保留完整 `underlying_daily` 和多日 `option_daily` 后即可派生 historical/parametric/Monte Carlo VaR、ES 或 full-revaluation option-portfolio risk variants。Greeks、IV、smile 和 VaR/ES 均是下游计算结果，不另建相互独立的 synthetic truth tables。

Forward 用 $$F=S_0e^{(r-q)T}.$$ 在 strike 升序下定义 $k_i=\log(K_i/F)$，authoring 端的 latent volatility generator 为 $$\sigma_i
=0.205-0.10k_i+0.55k_i^2+\varepsilon_i,
\qquad
\varepsilon_i\overset{\mathrm{iid}}{\sim}
\mathcal N(0,0.0015^2),$$ 并按固定顺序消耗 PCG64 random stream。随后将每个 $\sigma_i$ 注入 pinned QuantLib BSM pricing process/analytic engine 生成 call mid，并舍入到小数点后八位。Latent $\sigma_i$ 和 QuantLib 内部未量化 price 只用于 authoring audit；solver 输入只包含冻结后的 quote。

逐 strike latent-volatility construction 本身不自动证明整张 slice 无静态套利；若把本例升级为
可发布 market-surface task，必须另外验证 discounted bounds、strike monotonicity/convexity、
put-call parity（若 puts 可交易）及声明坐标下适用的跨期限条件。当前 accepted D4 task 不把
此 illustrative smile 当作生成模型。

### Solver-visible option chain

*表 2：冻结的合成 European call mid quotes*

| Strike $K$ | Call mid $C_{\mathrm{mkt}}$ |
|:-----------|:----------------------------|
| 80         | 20.51627694                 |
| 90         | 11.33390185                 |
| 95         | 7.40365418                  |
| 100        | 4.36887313                  |
| 105        | 2.26620258                  |
| 110        | 1.01004117                  |
| 120        | 0.19095135                  |

这张表一旦生成即成为当前 variant 的 ground truth。Solver 不得重新运行 latent generator，也不得用未舍入的 authoring price 替换表中 quote。

#### 数据阶段与解题阶段分开

金融子域允许 authoring 端造数据，不代表 solver 可以继续造数据。Solver 只解冻结后的 option chain；snapshot 的 strike、price、rate、dividend 与 maturity 任一变化都会使 identity test 失败。

## Problem 与固定方法

### 任务

对每个 strike：

1.  从冻结的 call mid 反解 BSM implied volatility；
2.  在该 implied volatility 下计算 call Delta、Gamma、Vega、Theta 与 Rho；
3.  在全部 strikes 上，用 $k=\log(K/F)$ 拟合 $$\widehat\sigma(k)=\beta_0+\beta_1k+\beta_2k^2$$ 的 unweighted ordinary least squares smile；
4.  按固定 JSON/CSV schema 输出逐 strike 结果与 $(\beta_0,\beta_1,\beta_2)$。

### BSM price 与 normal functions

Solver 需自行实现 $$\begin{aligned}
d_1&=\frac{\log(S_0/K)+(r-q+\tfrac12\sigma^2)T}{\sigma\sqrt T},
&d_2&=d_1-\sigma\sqrt T,\\
C(\sigma)&=S_0e^{-qT}N(d_1)-Ke^{-rT}N(d_2),
&\phi(x)&=\frac{e^{-x^2/2}}{\sqrt{2\pi}}.
\end{aligned}$$ 允许通过 `math.erf` 构造 $N(x)$。不得调用直接返回 BSM price、Greeks 或 implied volatility 的包/API。

### IV 求根合同

对每个 strike 求解 $$C(\sigma)=C_{\mathrm{mkt}},\qquad \sigma\in[10^{-6},5].$$ 固定规则为：

- method id 固定为 `bsm-bisection-float64-80-v1`；
- 初始端点为 $\ell_0=10^{-6}$ 与 $u_0=5$，先精确检查 $C(\ell_0)\le C_{\mathrm{mkt}}\le C(u_0)$；
- 对 $j=0,\ldots,79$，按 float64 运算顺序计算 $m_j=(\ell_j+u_j)/2$；若 $C(m_j)<C_{\mathrm{mkt}}$，令 $\ell_{j+1}=m_j$，否则令 $u_{j+1}=m_j$；
- 不提前停止，不切换 Newton/Brent，也不使用 residual threshold；完成恰好 $80$ 次更新后取 $\hat\sigma=(\ell_{80}+u_{80})/2$；
- bracket 无效时输出 `invalid_bracket`，不能静默返回端点或最后一次结果。

Verifier 以 QuantLib analytic BSM price 作为 price primitive，运行同一 80-step bisection wrapper，并检查 method id、iteration count、status 与 canonical IV。使用 package 自带的另一种 IV solver，即使得到相近的根，也不属于本 variant 的 verifier method。

## Greek Contract

### 解析公式

在恢复的 $\hat\sigma$ 下： $$\begin{aligned}
\Delta&=e^{-qT}N(d_1),\\
\Gamma&=\frac{e^{-qT}\phi(d_1)}{S_0\hat\sigma\sqrt T},\\
\mathcal V&=S_0e^{-qT}\phi(d_1)\sqrt T,\\
\Theta_{\mathrm{annual}}
&=-\frac{S_0e^{-qT}\phi(d_1)\hat\sigma}{2\sqrt T}
-rKe^{-rT}N(d_2)+qS_0e^{-qT}N(d_1),\\
\rho&=KTe^{-rT}N(d_2).
\end{aligned}$$

### 输出单位

*表 3：必须统一的 Greek scaling*

| 字段          | 输出定义                               | 换算                           |
|:--------------|:---------------------------------------|:-------------------------------|
| `delta`       | spot 上升 $1$ 单位的 price sensitivity | 直接输出 $\Delta$              |
| `gamma`       | spot 的二阶 sensitivity                | 直接输出 $\Gamma$              |
| `vega_1volpt` | volatility 上升 $1$ percentage point   | $\mathcal V/100$               |
| `theta_day`   | 一个 calendar day 的 price change      | $\Theta_{\mathrm{annual}}/365$ |
| `rho_1pct`    | rate 上升 $1$ percentage point         | $\rho/100$                     |

上述尺度被同时写入 solver output schema 与 verifier wrapper；不接受“数值相差 $100$ 倍但概念相同”。

## Smile Contract

### Coordinate 与 OLS

固定 $$k_i=\log(K_i/F),\qquad
  X_i=(1,k_i,k_i^2).$$ 使用全部七个 strikes、相等权重、不加正则化，按 strike 升序构造 $X$，并求 $$\hat\beta
  =\arg\min_{\beta\in\mathbb R^3}
  \sum_{i=1}^7(\hat\sigma_i-X_i\beta)^2.$$ 本 variant 的 method id 固定为 `ols-normal-equation-numpy-solve-v1`。Solver 与 verifier 都按 float64、strike 升序构造 $X$，使用未量化的内部 IV，且只允许执行 $$\hat\beta=\texttt{numpy.linalg.solve}(X^\top X,X^\top\hat\sigma).$$ 不得改用 QR、SVD、`lstsq` 或预封装的 volatility-smile calibration API。输出 coefficient order 固定为 $$(\texttt{beta0},\texttt{beta1},\texttt{beta2}).$$

### 为什么用 log-forward-moneyness

本例明确使用 $K/F$，而不是 $K/S_0$。将 spot moneyness 偷换为 forward moneyness 会改变全部 $k_i$ 与 coefficients，即使 IV 本身正确也不能通过 smile test。

## Authoring-Side Canonical Output

#### 展示范围

下表为了说明“唯一答案如何落库”而公开 authoring-side reference。真实训练 episode 中，该表及 package oracle 只存在于 hidden verifier，不能进入 policy-visible state。

*表 4：逐 strike canonical IV 与 Greeks*

| $K$ | IV             | Delta          | Gamma          | Vega/1pt       | Theta/day       | Rho/1pct       |
|:----|:---------------|:---------------|:---------------|:---------------|:----------------|:---------------|
| 80  | 0.255583272001 | 0.965367178324 | 0.005633773644 | 0.035997457541 | -0.008644700397 | 0.190051102231 |
| 90  | 0.224548169916 | 0.848495436988 | 0.020657972344 | 0.115967747102 | -0.017986418890 | 0.183789104622 |
| 95  | 0.212498213855 | 0.718238088761 | 0.031601389982 | 0.167880973161 | -0.022874651889 | 0.161050386740 |
| 100 | 0.207439153174 | 0.538506361981 | 0.038175894737 | 0.197979381899 | -0.025095018070 | 0.123704407670 |
| 105 | 0.202625926302 | 0.350512723953 | 0.036521077709 | 0.185002930008 | -0.022274842145 | 0.081962674538 |
| 110 | 0.197726448666 | 0.193295141035 | 0.027712108559 | 0.136985420259 | -0.015817581316 | 0.045798682334 |
| 120 | 0.205262529487 | 0.046709280587 | 0.009511834661 | 0.048810581063 | -0.005730100514 | 0.011199941772 |

由这些 IV 得到 canonical quadratic-smile coefficients： $$\boxed{
\begin{aligned}
\beta_0&=0.205686482173,\\
\beta_1&=-0.102185319658,\\
\beta_2&=\phantom{-}0.521828793802.
\end{aligned}}$$

表中的小数不是“显示近似”，而是本 variant 要求提交的 canonical decimal strings：逐 strike 数值与 smile coefficients 都保留恰好 $12$ 位小数并采用 round-half-even。

## Canonical Output 与 Exact Equality

### 提交 schema

提交包括：

- `results.csv`：列固定为  
  `strike, iv, delta, gamma, vega_1volpt,`  
  `theta_day, rho_1pct`；

- `smile.json`：`beta0, beta1, beta2, coordinate, basis, fit_status`；
- `run.json`：variant/snapshot id、seed 与三个 method ids；  
  `iteration_count=80`、bracket/status flags 与每个 strike 的八位小数 `repriced_mid`。

### 数值规范化与验收

*表 5：逐字段 canonicalization；hard verifier 无 tolerance*

| 字段                      | Canonical form       | Exact assertion                                         |
|:--------------------------|:---------------------|:--------------------------------------------------------|
| Market quote/repriced mid | 恰好 $8$ 位小数      | string `==`；两者逐行完全相等                           |
| IV 与五个 Greeks          | 恰好 $12$ 位小数     | string `==`；字段名、sign 与 scaling 同时精确匹配       |
| Smile coefficients        | 恰好 $12$ 位小数     | string `==`；coordinate/basis/method/order 同时精确匹配 |
| IDs/method/status/order   | canonical UTF-8 text | string/list `==`；strikes 严格升序且无缺行              |

所有数值先通过 `Decimal(str(x))`，再以 `ROUND_HALF_EVEN` 量化到规定小数位；禁止科学计数法，正零统一写成带固定小数位的 `0.000...`。JSON key order、CSV column/row order 与换行符也固定。Pytest 直接执行 $$\texttt{assert candidate\_canonical\_bytes == oracle\_canonical\_bytes}.$$ 不使用 `pytest.approx`、`isclose`/`allclose`、`atol` 或 `rtol`。Authoring QC 会剔除落在舍入边界附近、不能在目标 pinned environment 中稳定生成同一 canonical string 的样本。

## Agent Trajectory

### 正向 transitions

<a id="tab:trajectory"></a>

*表 6：正向 episode scaffold*

| Step | Skill / action                  | Policy-visible observation                       | State update            |
|:-----|:--------------------------------|:-------------------------------------------------|:------------------------|
| 1    | Parse contract                  | snapshot、BSM conventions、units、denylist       | task/convention ledgers |
| 2    | Validate quotes                 | bounds、finite values、strike order              | input gate status       |
| 3    | Implement normal/BSM primitives | sanity-case price and finite status              | pricing artifact        |
| 4    | Solve IV by strike              | 80-step brackets、method id、status              | IV artifact             |
| 5    | Compute analytic Greeks         | per-strike raw and scaled Greeks                 | Greek artifact          |
| 6    | Build $k=\log(K/F)$             | forward、coordinates、design matrix shape        | smile-design artifact   |
| 7    | Fit fixed quadratic OLS         | coefficients、residual vector、rank              | smile artifact          |
| 8    | Canonicalize/check              | repriced mids、sign/range、fixed-decimal strings | self-check artifact     |
| 9    | Serialize outputs               | schema/order validation                          | candidate submission    |
| 10   | Hidden pytest verification      | 仅终止后返回 pass/fail diagnostics               | outcome and reward      |

正向 episode scaffold

### 统一九字段

*表 7：本例的 AI-for-STEM schema*

| 字段                   | 本例内容                                                                                       |
|:-----------------------|:-----------------------------------------------------------------------------------------------|
| Problem                | 从冻结 call quotes 恢复 IV、Greeks 与 quadratic smile。                                        |
| Context                | BSM、$S,r,q,T$、fixed seed、root/Greek/smile contracts、package denylist。                     |
| Assumptions            | European exercise、continuous rates、BSM inversion、valid quotes、unique root。                |
| Skills                 | implement formula、differentiate、root solve、scale Greeks、fit OLS、check invariants。        |
| Evidence               | option chain、iteration/residual traces、Greek table、design matrix 与 fit diagnostics。       |
| Intermediate Reasoning | 表 [对应表格](#tab:trajectory) 的 state–action–observation transitions。                       |
| Verification           | import/method audit、package-backed pytest、canonical exact equality 与 financial invariants。 |
| Confidence             | fixed iteration、valid bracket、rank、finite/stability 与 verified status。                    |
| Outcome                | canonical files、tests pass/fail 与 $R_{\mathrm{ORM}}\in\{0,1\}$。                             |

## Pytest Package Oracle

### Backend mapping

*表 8：Trusted verifier 的直接调包方案*

| 验收对象       | Pytest fixture 调用                                         | Wrapper 工作                                                                       |
|:---------------|:------------------------------------------------------------|:-----------------------------------------------------------------------------------|
| BSM price / IV | pinned QuantLib analytic engine + 80-step bisection wrapper | 对齐 continuous $r,q,T$、call convention、bracket、update order 与 iteration count |
| Greeks         | pinned QuantLib analytic Greeks                             | 固定 analytic method，并转成 per-1-vol-point、per-day、per-1%-rate                 |
| Smile OLS      | pinned `numpy.linalg.solve`                                 | 固定 normal equations、$k=\log(K/F)$、float64、row 与 coefficient order            |
| Imports        | AST/import manifest + isolated environment                  | 拒绝 solver 调用 finance/IV/smile package                                          |
| Canonicalizer  | pinned Decimal/serialization wrapper                        | 固定小数位、round-half-even、key/row order 与 UTF-8 bytes                          |
| Invariants     | pytest exact/boolean assertions                             | price bounds、repriced-mid equality、finite、sign/range                            |

Verifier 不复用 solver 源码，但必须实现同一公开 method contract：QuantLib 提供 analytic price/Greeks，pytest wrapper 执行相同的 80-step bisection，NumPy 执行相同的 normal equations。随后 oracle 与 solver 输出走同一个 canonicalizer 并做 exact equality。

### Hidden tests

<a id="tab:tests"></a>

*表 9：本例的 hard tests*

| Test id                     | Hard assertion                                                                        |
|:----------------------------|:--------------------------------------------------------------------------------------|
| `test_snapshot`             | $S,r,q,T,K,C_{\mathrm{mkt}}$、seed 与 snapshot id 精确匹配。                          |
| `test_banned_imports`       | solver 未调用 QuantLib、py_vollib、mibian、rateslib 或其他被禁 API。                  |
| `test_schema_and_order`     | 三个输出对象完整，七个 strikes 严格升序，无 NaN/Inf。                                 |
| `test_method_contract`      | 三个 method ids、80 次 bisection、analytic Greeks 与 normal-equation solve 精确匹配。 |
| `test_iv_oracle`            | 每个 $12$ 位 canonical IV string 与同方法 package oracle 完全相等。                   |
| `test_repricing`            | 每个 $8$ 位 canonical `repriced_mid` 与冻结 quote string 完全相等。                   |
| `test_greek_oracle`         | 五个 Greeks 与 wrapper-normalized canonical package strings 完全相等。                |
| `test_greek_scaling`        | vega、theta、rho 单位未相差 $100$ 或 $365$。                                          |
| `test_smile_coordinate`     | 使用 $\log(K/F)$，而非 $\log(K/S_0)$。                                                |
| `test_smile_coefficients`   | NumPy oracle 的三个 $12$ 位 coefficient strings 完全相等。                            |
| `test_financial_invariants` | call bounds、Delta range、Gamma/Vega sign 与 finite checks 通过。                     |

本例的 hard tests

$$R_{\mathrm{ORM}}
=\mathbf 1\{\text{表 \href{#tab:tests}{对应表格} 的所有 pytest tests 通过}\}.$$

## First-Error 负轨迹

### 负轨迹 A：Solver 直接调用 py_vollib

数值完全正确，但源码/import audit 发现 solver 调用现成 IV/Greek API。第一处错误是违反 tool protocol；`test_banned_imports` 失败，reward 为 $0$。本任务奖励的是模型自行实现，而不是能否找到一个库。

### 负轨迹 B：Vega/Rho 未缩放

Agent 输出对 $\Delta\sigma=1$ 的 raw vega 和对 $\Delta r=1$ 的 raw rho，却把字段写成 per-1-point。数值分别相差 $100$ 倍；`test_greek_oracle` 与 `test_greek_scaling` 失败。

### 负轨迹 C：用 Spot Moneyness

Agent 用 $k=\log(K/S_0)$ 拟合 smile。逐 strike IV 与 Greeks 可以全部正确，但 smile coordinate 与 coefficients 错误；相应 tests 失败，terminal reward 仍为 $0$。

### 负轨迹 D：重新生成未舍入价格

Agent 使用 seed 重跑 latent generator，并用未舍入 BSM prices 解 IV。即使它更接近 latent volatility，也没有回答冻结 snapshot 的任务；`test_snapshot`、IV 与 repricing tests 失败。

### 负轨迹 E：擅自改用 Newton 或提前停止

Agent 改用 Newton、Brent，或在 residual 看起来足够小时提前停止。若 method/source 可观察，
policy test 会拒绝；若它导致不同 canonical output，IV exact-equality test 会失败。若不同实现
在 12 位输出下恰好完全相同，单凭 submission 不能反推出内部 iteration schedule，semantic
verifier 不应声称能区分这种不可观察差异。正确实现仍是执行完整 80-step bisection，而不是
给 verifier 增加 tolerance。

## 落库对象

*表 10：本例的可落库对象*

| 对象              | 内容                                                                                         |
|:------------------|:---------------------------------------------------------------------------------------------|
| `market_snapshot` | QuantLib 生成的 `underlying_daily`、`option_daily`、`pricing_metadata`，以及 snapshot/config version、seed 与 RNG。 |
| `task_variant`    | BSM/IV/Greek/smile contracts、solver allowlist/denylist、output schema。                     |
| `reference_run`   | 同方法 package wrapper、canonical strings/bytes、rounding contract、uniqueness certificate。 |
| `agent_episode`   | 表 [对应表格](#tab:trajectory) 的 transitions、tool observations 与 artifacts。              |
| `pytest_suite`    | 表 [对应表格](#tab:tests) 的 hidden tests、package lock 与 fixture version。                 |
| `ORM_outcome`     | pass/fail、first failure、diagnostics 与 terminal reward。                                   |

#### 样例结论

这条样例完整体现了最终约束：pinned QuantLib 在固定 seed/config 下统一生成 underlying daily state、option daily prices 与 pricing metadata，生成后冻结；Greeks、IV 与 smile 只作为下游 task outputs；LLM 与 pytest 的 IV、Greek 和 smile 方法严格对齐；LLM 只能用基础原语手工实现，trusted verifier 可以调固定版本的金融/数值包；双方按同一精度与舍入规则生成 canonical bytes，并以 exact equality 验收，不设置 hard-verifier tolerance。Solver 调包或换方法即使结果接近也失败；只有所有 tests pass 才获得 $R_{\mathrm{ORM}}=1$。

## 参考资料

1.  <a id="ref-1"></a> F. Black and M. Scholes. *The Pricing of Options and Corporate Liabilities*. Journal of Political Economy, 81(3):637–654, 1973.
2.  <a id="ref-2"></a> R. C. Merton. *Theory of Rational Option Pricing*. Bell Journal of Economics and Management Science, 4(1):141–183, 1973.
3.  <a id="ref-3"></a> pytest documentation. *pytest: helps you write better programs*.  
    <https://docs.pytest.org/>

4.  <a id="ref-4"></a> QuantLib. *A free/open-source library for quantitative finance*.  
    <https://www.quantlib.org/>
