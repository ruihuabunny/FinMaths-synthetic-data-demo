# 金融数学与衍生品市场的确定性 ORM、Task Mutation 与 Curriculum 框架

**七维 Task Grammar、受约束 Mutation、同币种联合市场模拟、Arbitrage Finding、Adaptive Curriculum、冻结市场快照与 Pytest Hard Verification**

*A Deterministic ORM Hard-Verifier Framework for Financial Mathematics and Derivatives Markets*

- 作者：Ruihua Luo
- 日期：August 2026
- 版本：`Mutation + curriculum + same-currency joint-simulator extension`

---

## 摘要

本文给出金融数学与衍生品市场任务的独立数据方案。与普通统计/数据科学领域不同，出题端以固定版本的 QuantLib 作为统一市场数据生成器：在固定 generator configuration、seed 与 RNG 下生成 underlying daily panel、完整 option daily chain 及 pricing metadata，materialize 后立即冻结为只读 market snapshot。Greeks、implied volatility、smile/surface、VaR 与 ES 不再分别造数据，而是从同一快照派生 task variants。对于同币种多资产市场，所有资产定义在共同风险中性测度 $\mathbb Q$、共同 numeraire 与共享利率路径下；每个资产的完整 option surface 由一个合法且内部一致的边际 pricing model 生成，再以对称、单位对角且半正定的相关矩阵对 underlying/model drivers 进行联合耦合。Derivative contracts 本身不占相关矩阵的行列。随后固定定价模型、underlying dependence、day-count、Greek convention、IV 求根算法、smile/surface 拟合方法、VaR/ES 定义、dtype、操作/归约顺序与 canonical output schema，使 solver 与 verifier 在同一 method contract 下生成逐字段唯一的规范答案。

Solver 的核心训练目标是自己实现 Greeks、implied volatility、volatility smile/surface 与 arbitrage search 计算。其执行环境不得调用 QuantLib、py_vollib、mibian、rateslib 等现成衍生品定价接口，也不得用封装好的 IV/Greek/smile API 绕过推导；允许的基础数值原语由 task contract 明确列出。Trusted verifier 不受这一限制：它直接以 `pytest` 调用固定版本且与题目方法一致的权威数值/金融包复算标准答案，再强制转换为题目指定 dtype 并按 canonical schema 序列化。Hard verifier 不设置绝对或相对 tolerance，而是逐字段执行 exact equality；全部测试通过时 outcome reward 为 $1$，否则为 $0$。

在此基础上，本文进一步把任务表示为七维坐标 $\tau=(L,P,M,A,D,R,F)$：推理复杂度、产品族、模型假设、数值方法、数据/工具环境、风险输出与 arbitrage-finding 难度。$F$ 轴按 frozen snapshot 中 pricing-model 来源数量、候选数据规模、mutated 数据数量与类型，以及需要扫描的 no-arbitrage invariant families 分级；从 F2 起再用 A/B 后缀区分“判断套利并分类”和“判断套利并求 maximal spread”。Task-space registry 规定合法组合，mutation engine 沿一个或少数坐标以及联合依赖配置生成带 lineage 的受控变体，curriculum scheduler 再根据 rollout 的分项通过率与错误类型调整采样分布。七维坐标同时充当难度描述、task mutation grammar 与能力归因工具；`arbitrage_finding` 的 LLM 输出保留完整 trajectory，F0/F1 的 ORM 验收 `(arbitrage_opportunity: bool)`，F2A--F6A 验收 `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)`，F2B--F6B 验收 `(arbitrage_opportunity: bool, maximal_spread: float)`，且都不对 trajectory 内容打分。最终 reward 仍由 all-pass hard verifier 给出，不因 curriculum 引入主观软分。本阶段明确排除 FX、quanto、cross-currency derivatives、多币种利率与 numeraire conversion。

**关键词：** financial derivatives；multi-asset simulator；correlation matrix；risk-neutral measure；task grammar；task mutation；arbitrage finding；curriculum learning；Greeks；implied volatility；volatility smile；synthetic market data；pytest；outcome reward model；hard verifier

## 最终设计结论

#### 最终方案

金融衍生品市场单独建域：authoring 端用 pinned QuantLib 统一生成 underlying daily prices、option daily prices 与定价元数据；每个 variant 固定 generator、seed 与全部市场约定，生成后冻结 snapshot；Greeks/IV/smile/surface/VaR/ES 都从该快照派生。同币种多资产 snapshot 使用共同 $\mathbb Q$、共同 numeraire、共享利率路径与合法边际模型，并以合法相关矩阵耦合 underlying/model drivers，而不是 derivative contracts。每个任务再注册为七维坐标 $\tau=(L,P,M,A,D,R,F)$，其中 $F$ 单独刻画在 mutated frozen snapshot 中发现套利的难度。Task-space registry 定义各级含义与 compatibility constraints；mutation engine 产生可追踪变体；curriculum scheduler 根据模型 mastery 选择训练分布。Task contract 同时约束 solver 与 verifier 的公式/算法、dtype、操作顺序和输出 schema；solver 禁止调用现成 Greeks/IV/smile 包，trusted verifier 用 `pytest` 调固定版本、同方法的金融包复算并以 `==` 精确验收。`arbitrage_finding` 要求 LLM 生成完整 trajectory；F0/F1 的 outcome ORM 比较 bool，F2A--F6A 比较 bool 与 cross-sectional/cross-asset/calendar 类型集合，F2B--F6B 比较 bool 与 maximal spread。全部适用 tests pass 才有 $R_{\mathrm{ORM}}=1$。

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

第 $v$ 个 market snapshot 定义为

$$
D_v^{\mathrm{mkt}}
=G_{\mathrm{mkt}}\!\left(
\theta_v;\,
\texttt{generator\_version},
\texttt{seed}_v,
\texttt{RNG}_v
\right).
$$

其中 $\theta_v$ 可以包含

$$
S_0,\; r,\; q,\; T,\; K,\; \sigma,\;
\text{curve parameters},\; \text{skew},\; \text{curvature},\;
\text{quote-noise model},\; \text{numeraire},\; \mathbb Q,\; R_t.
$$

生成后保存 snapshot id 与 revision。若
$D_{v,t}^{\mathrm{mkt}}$ 表示 solver 在第 $t$ 步看到的 snapshot，则不可变性要求

$$
\operatorname{id}\!\left(D_{v,t}^{\mathrm{mkt}}\right)
=\operatorname{id}\!\left(D_v^{\mathrm{mkt}}\right),\qquad
\operatorname{revision}\!\left(D_{v,t}^{\mathrm{mkt}}\right)
=\operatorname{revision}\!\left(D_v^{\mathrm{mkt}}\right)
\quad \text{for every solver step } t.
$$

### QuantLib 统一生成器与联合快照合同

生产 authoring job 将 $G_{\mathrm{mkt}}$ 实现为 pinned QuantLib pipeline，而不是只使用单一 GBM 公式。模型 registry 可以包含 GBM/BSM baseline、Heston、jump diffusion/Bates、local volatility，以及 SVI/SABR smile/surface；每个 variant 必须保存实际使用的 QuantLib version、Python binding、process class、pricing engine、参数、离散化方法与 engine id。GBM 只作为解析基线，不作为唯一 generator。

统一 snapshot 至少由以下对象组成：

| 对象 | 必需字段 |
|:---|:---|
| `underlying_daily` | `date, underlying_id, spot_open, spot_high, spot_low, spot_close, adjusted_close, volume, dividend, corporate_action`；若某字段不由当前模型生成，必须固定为明确的 `null`/常数规则。 |
| `option_daily` | `date, underlying_id, option_id, call_put, strike, expiry, exercise_style, settlement_type, contract_multiplier, bid, ask, mid, settlement_price, volume, open_interest`。 |
| `pricing_metadata` | `valuation_timestamp, market_id, currency, numeraire, risk_neutral_measure_id, rate_path_id, discount_curve/risk_free_rate, dividend_curve/dividend_yield, borrow_or_carry_rate, calendar, day_count, physical_dynamics, pricing_dynamics, pricing_model, pricing_engine, generator_version, seed, RNG, input_precision, canonicalization`。 |
| `underlying_dependence` | `dependence_spec_id, measure, driver_order, formulation, factor_loading_matrix, idiosyncratic_diagonal, correlation_matrix, matrix_dtype, factorization_method, factorization_order, time_grid, regime_id`；driver order 只排列 underlying/model drivers，不排列 derivative contracts。单资产任务给出退化的一维单位矩阵或明确的 `not_applicable` 规则。 |
| `option_contracts` | 稳定 `option_id`、underlying、call/put、frozen absolute strike、expiry、exercise/settlement/multiplier，以及 listing date/spot/moneyness provenance。 |
| `option_chain_specs` | `chain_id`、candidate expiry 与 strike/moneyness grid、call/put、listing/roll rule、strike increment/rounding、liquidity filter、quote model 和合约约定；属于 private authoring provenance。 |

`underlying_daily` 中用于历史收益、VaR/ES 的路径属于物理测度 $\mathbb P$；`option_daily` 的定价属于风险中性测度 $\mathbb Q$。两者可共享当日 spot、variance state 与市场日期，但 drift、风险溢价及模型参数必须分别保存为 `physical_dynamics` 与 `pricing_dynamics`。$\mathbb P$ 与 $\mathbb Q$ 下的 underlying dependence 使用 measure-qualified spec id；不得把历史相关参数无声明地复用到风险中性定价。当前 authoring simulator materialize `measure=P` 的 cross-asset `underlying_dependence`；config `1.5.0` 已为 single-asset vanilla margins 声明共同 $\mathbb Q$/numeraire/rate-path identity 和 drift-only Girsanov diffusion mapping，而 multi-asset $\mathbb Q$ dependence 仍属于后续阶段。如果某题只需 flat rate/dividend，可以把完整 curves 简化为固定 $r,q$，但简化规则本身仍是合同字段。

Authoring pipeline 固定为：QuantLib 先在共同时间网格上生成全部 underlying path/state，再对每个 valuation date、underlying 与 strike--maturity grid $\mathcal{K}\times\mathcal{T}$ 用指定 QuantLib pricing engine 生成 option chain，最后按题面精度量化并冻结。题目的 IV 真值必须从 solver 实际可见的、已量化 option price 按指定求根法重新反解，不能直接拿 QuantLib 内部未公开的 latent volatility 作答案。这样一套数据即可派生 Greeks、IV、smile/surface、underlying VaR/ES、option-portfolio VaR/ES 以及同一联合过程下的 basket/index/spread variants；不需要维护彼此不一致的独立“Greeks 数据表”或“VaR 数据表”。

当前实现已经完成 static `OptionChainBuilder` baseline：generator config `1.3.0` 把
expiry schedule、互斥的 listing-moneyness/absolute-strike grid、成对 call/put、strike
increment 与 listing/roll rules 冻结为 private chain spec；moneyness 模式在 listing date
转成 absolute strike，strike 模式则直接按 increment 规范化，随后合约身份与 strike 都
不再随每日 spot 改写。第一版只允许
`listing_rule=snapshot_start`、`roll_rule=static`，动态 weekly/monthly/quarterly listing
留给 exchange-profile 阶段。Config `1.4.0` 进一步把该网格定义为 candidate grid，并按
listing-moneyness inclusive band 与 maximum expiry 只 materialize 流动性较好的合约；
side-specific deterministic quote noise 只乘在 BSM bid/ask half-spread 上，不改变
`mid = settlement_price`。Config `1.5.0` 进一步移除 legacy `base_implied_volatility`
与 smile：physical drift/volatility 节点从声明的概率分布抽样一次后冻结；在明确的
drift-only Girsanov baseline 中，Q drift 改为 $r-q$ 且 deterministic diffusion 满足
$\sigma_Q(t)=\sigma_P(t)$。每个 valuation-to-expiry interval 对 $\sigma_Q^2$ 精确积分并
取 constant-equivalent RMS，再用 QuantLib analytic BSM 定价；最终 IV 则从实际已量化
canonical mid 调用 QuantLib 反解并写入 private audit。当前 public profile 使用 22 个 underlying，每个实际保留
4 expiries × 7 strikes × call/put，共 1,232 个固定合约，65 个 business dates 内生成
60,368 条 expiry 前 quotes。各期限来自同一个 deterministic-time-varying-diffusion BSM
marginal model，而不是 `physical volatility + 0.02` 或逐 quote latent smile。Option
contracts 不加入 correlation matrix；multi-asset Q-dependence 仍是下一阶段。

### 同币种 Conditional-Independent Baseline 与相关矩阵扰动

本阶段只考虑同一币种、同一 numeraire 与同一市场计价体系。所有资产共享同一条利率路径 $r_{0:T}$，并定义在共同风险中性测度 $\mathbb Q$ 与联合 filtration 下；暂不考虑 FX、quanto、cross-currency derivatives、多币种利率或 numeraire conversion。

第 $i$ 个资产在 $\mathbb Q$ 下使用一个合法且内部一致的边际 pricing model。以下写法仅展示其 spot driver；Heston、stochastic-rate 或 hybrid model 的 volatility/rate drivers 也必须按固定 `driver_order` 纳入完整 block dependence specification：

$$
\frac{dS_{i,t}}{S_{i,t}}
=
(r_t-q_{i,t})\,dt
+
\sigma_i(t,S_{i,t},V_{i,t},\ldots)\,dW^{\mathbb Q}_{i,t}.
$$

不同资产可以使用不同的合法模型，例如 BSM、CEV、Heston、local volatility 或 mixture dynamics；但同一资产的全部 strike、maturity 与合约变体必须由同一个 coherent model snapshot 一致生成。这里的“合法边际 pricing model”是能够产生完整一致价格面的动态/定价模型，而不是对每个 quote 独立拟合的点态公式。

生成器先构造 baseline：给定共享利率路径 $r_{0:T}$ 后，各资产的 idiosyncratic drivers 相互独立。随后用相关矩阵 $R_t$ 对基础冲击进行 perturbation/coupling：

$$
R_t=R_t^\top,
\qquad
\operatorname{diag}(R_t)=\mathbf 1,
\qquad
R_t\succeq 0,
\qquad
-1<R_{ij,t}<1\quad(i\ne j).
$$

若允许完全共线或完全反向的退化结构，可将最后一个条件放宽为 $-1\le R_{ij,t}\le1$。PSD 已足以定义联合高斯冲击；本文默认直接使用下述 factor-loading construction。若某个变体改用普通 Cholesky decomposition，则要求 $R_t\succ0$，或必须在合同中指定支持半正定矩阵的确定性 factorization。

为避免与 money-market numeraire 常用的 $B_t$ 混淆，factor-loading matrix 统一记为 $\Lambda_t$。设其第 $i$ 行为 $\lambda_{i,t}^{\top}$，并定义

$$
D_t=\operatorname{diag}\!\left(
1-\lVert\lambda_{1,t}\rVert^2,\ldots,
1-\lVert\lambda_{n,t}\rVert^2
\right)\succeq0,
\qquad
R_t=\Lambda_t\Lambda_t^\top+D_t.
$$

合法样本冻结 $\Lambda_t$ 与 $D_t$，再由 verifier 按相同 dtype、driver order 与 canonicalization 精确重放 $R_t$。因此 symmetry、单位对角与 PSD 都由生成构造保证，不依赖带数值容差的事后 eigenvalue 判定。故意生成的非 PSD 样本则保存其唯一 violation type 与构造 lineage。

给定相互独立的共同 factor shock $\eta_t\sim N(0,I_k)$ 与 idiosyncratic shock $\varepsilon_t\sim N(0,I_n)$，联合冲击生成规则为

$$
Z_t=\Lambda_t\eta_t+D_t^{1/2}\varepsilon_t,
\qquad
d\langle W_i^{\mathbb Q},W_j^{\mathbb Q}\rangle_t
=R_{ij,t}\,dt.
$$

因此，`conditional independence given the shared interest-rate path` 描述的是相关扰动前的 baseline。只要 $R_t$ 存在非零非对角元，最终资产在仅给定利率路径后便是 conditionally correlated，而不是 conditionally independent。若要在最终模型中保留条件独立表述，则必须进一步给定产生相关性的共同 factor paths；本文默认采用更直接的“conditional-independent baseline + PSD correlation perturbation”表述。

相关矩阵只负责耦合 underlying/model 随机驱动，不改变任何资产的边际 pricing model、边际参数或完整 option surface；option id、Greek 或 derivative quote 不得作为矩阵 driver。对 stochastic-vol/hybrid models，cross-asset perturbation 还必须保持各资产既有的 spot--vol/rate marginal driver block 不变。因此，single-asset no-arbitrage consistency 直接继承自合法边际模型，而不是在 generator 中另外逐项施加 put--call parity、strike monotonicity、convexity 与 calendar constraints。这些性质只在 authoring/verifier 中作为防止实现错误的 sanity checks。

联合市场的充分构造条件概括为

$$
\boxed{
\text{valid coherent marginal models}
+\text{common }\mathbb Q
+\text{shared rate path and numeraire}
+\text{PSD joint correlation structure}
+\text{consistent joint pricing}
}.
$$

所有 basket、index、spread 或其他多资产 payoff 必须使用同一个 joint underlying process、同一 underlying-dependence snapshot 与同一组联合状态定价，不能先分别生成 derivative prices 再事后拼接相关性。相关矩阵本身不会修复非法边际模型，也不能替代共同 $\mathbb Q$ 与一致联合定价。

### 最低有效性门控

即使数据可以合成，也必须先保证任务有定义。对于普通合法样本，single-asset no-arbitrage 由 coherent marginal pricing model 内生保证；authoring 端只需验证模型配置与实现没有破坏这种继承关系。其余 task-specific 门控包括：

- $S_0>0,K>0,T>0,\sigma>0$；
- option quote 位于模型和合约约定允许的界内；
- IV 任务存在且只有一个声明区间内的根；
- rate/dividend、discounting、calendar 与 day-count 明确；
- surface task 的 grid、缺失 pattern 与 extrapolation policy 明确；
- listing moneyness 只在挂牌时转换一次，absolute strike 与 stable contract id 跨 valuation
  dates 不变；同一 expiry/strike 的 call/put 配对和完整 grid 可重放；
- 多资产任务的共同 $\mathbb Q$、numeraire、rate path id、driver order 与 dependence spec 完整；
- $R_t$ 对称、单位对角且 PSD，固定 factorization method 与输出顺序；
- 联合衍生品全部来自同一 joint process，而不是独立边际价格的事后拼接。

Put--call parity、strike monotonicity/convexity 与 calendar consistency 可以作为 QC sanity checks 运行，但不重复充当 generator 的独立构造假设。如果题目故意要求识别 arbitrage violation，则违规本身可被合成，但必须作为 task truth 明确记录，而不能意外混入普通 IV/Greek 题。

## Task Variant 与唯一方法合同

### 母对象与 variant

金融 variant 可写为

$$
\mathcal{V}_v
=\left(
D_v^{\mathrm{mkt}},q_v,\tau_v,C_v,O_v,V_v,S_v,\Pi_v
\right),
$$

其中 $q_v$ 是 problem/query，$\tau_v=(L_v,P_v,M_v,A_v,D_v,R_v,F_v)$ 是七维任务坐标，$C_v$ 是 convention contract。这里坐标分量 $D_v$ 表示 data/tool axis，不是 market snapshot $D_v^{\mathrm{mkt}}$；$F_v$ 表示 arbitrage-finding axis，不是 filtration。

$O_v$ 是 output contract，$V_v$ 是 verifier contract，$S_v$ 是 target skills，$\Pi_v$ 是 parent/child id、engine id、operator 与 seed 构成的 mutation provenance。旧式 model/method contract 被拆入 $M_v$ 与 $A_v$，从而可以分别归因“模型假设错误”和“数值方法错误”。

### 必须冻结的金融约定

<a id="tab:contract"></a>

*表 2：金融衍生品 task contract 的必需字段*

| 字段                 | 内容                                                                                                                       |
|:---------------------|:---------------------------------------------------------------------------------------------------------------------------|
| Instrument           | call/put、European/American、exercise、payoff、notional、共同 currency；本阶段禁止 cross-currency/quanto/FX。            |
| Generator backend    | QuantLib version/Python binding、process class、pricing engine、model parameters、generator config id。                    |
| Physical dynamics    | 生成 underlying history 的 $\mathbb P$-measure process、drift/risk premium、discretization、time grid 与 path state。     |
| Pricing dynamics     | 共同 $\mathbb Q$、numeraire、共享 rate path，以及各资产 coherent marginal model/parameters、engine 与 calibration policy；不得与 $\mathbb P$-measure 隐式混用。 |
| Underlying dependence | measure-qualified dependence spec id、underlying/model driver order、$\Lambda_t$、$D_t$、$R_t$、PSD/PD policy、factorization、matrix dtype 与 time/regime policy；禁止 derivative ids。 |
| Snapshot schema      | `underlying_daily`、`option_daily`、`pricing_metadata`、`underlying_dependence`、`option_contracts`、`option_chain_specs` 的字段、类型、主键、null policy、单位与可见性。  |
| Market state         | asset/underlying id、spot/forward、strike、maturity、共享 rate path、dividend、curve 与 quote definition。                 |
| Clock convention     | valuation date、calendar、business-day adjustment、day-count、time-to-expiry。                                             |
| Pricing model/method | Black–Scholes–Merton、Black-76、tree、PDE、Monte Carlo 等；单模型 task 固定唯一模型与 engine/method，多模型 task（包括 F4A/F4B 及更高 base level）则为每个 coherent partition 固定唯一 model id、engine/method 与 row-routing rule。 |
| Greek definition     | spot/forward Greek、holding-what-fixed、analytic/finite-difference/MC estimator、bump/stencil 与 scaling。                 |
| IV method            | objective price、vol bracket、algorithm id、initial state、固定迭代次数或确定性停止规则、fallback 与 failure output。      |
| Smile/surface method | moneyness coordinate、basis、weights、fit objective、linear-algebra routine、regularization、interpolation/extrapolation。 |
| Arbitrage finding    | $F$ level、$\chi_F$、source-model partitions、parent/child snapshot ids、逐 cell mutation manifest、单位名义套利模板，以及最终 ORM projection；B 类另需 spread currency/definition 与 argmax reduction order。 |
| Randomness           | generator seed、RNG、independent draw order、correlation factorization/order；MC 还需冻结 shocks/path order、variance reduction 与 reduction order。 |
| Numerics             | dtype、运算与归约顺序、工作精度、必要的显式 cast checkpoints 与 non-convergence output。                                    |
| Submission           | JSON/table fields、row order、units、目标 dtype、canonical representation 与 serialization order；`arbitrage_finding` 另需完整 trajectory，最终 `Outcome.orm_answer` 按 level 为 `(bool)`、`(bool, ordered enum array)` 或 `(bool, float)`。 |

金融衍生品 task contract 的必需字段

### Greek 单位必须显式

许多“答案不唯一”其实来自 convention 未写清。每题至少要声明：

- Vega 是对 $\Delta\sigma=1$ 还是一个 volatility point（$0.01$）；
- Rho 是对 $\Delta r=1$ 还是一个 percentage point；
- Theta 是 annual、calendar-day 还是 trading-day decay；
- Delta/Gamma 对 spot 还是 forward，是否包含 discount/dividend factor；
- rate 与 dividend 是连续复利、简单利率还是离散复利。

## 七维 Task Grammar 与难度空间

任务不再只标为“简单/中等/困难”，而是使用可解释坐标：

$$
\boxed{\tau=(L_{\mathrm{reasoning}},P_{\mathrm{product}},M_{\mathrm{model}},A_{\mathrm{method}},D_{\mathrm{data/tool}},R_{\mathrm{risk}},F_{\mathrm{arbitrage\ finding}})}.
$$

七个轴相互独立但不做无条件笛卡尔积。每个 variant 必须通过 compatibility registry，或被明确标记为“识别不兼容”的 adversarial task。

### 推理复杂度轴 $L$

| Level | 任务形式 | 核心能力 |
|---|---|---|
| L0 | 单一输入、单一数值输出 | 公式识别、基础计算 |
| L1 | 单产品、多字段输出 | price、IV 与完整 Greeks |
| L2 | 多腿组合与结构识别 | position sign、leg aggregation、payoff、breakeven |
| L3 | 逆问题与校准 | IV 求根、curve/smile/surface、模型参数校准 |
| L4 | 情景分析与风险归因 | shock、PnL decomposition、portfolio/model comparison |
| L5 | 完整 agentic workflow | 查询数据库、清洗、实现、运行、验证、保存 artifact |
| L6 | 开放式模型风险与修复 | 识别错误假设、诊断并修复失败 pipeline |

### 产品复杂度轴 $P$

| Level | 产品族 | 代表结构/能力 |
|---|---|---|
| P0 | Vanilla call/put | pricing、Greeks、IV、put--call parity |
| P1 | Digital / discontinuous payoff | cash-or-nothing、asset-or-nothing、gap option、静态复制 |
| P2 | 两腿同期限组合 | straddle、strangle、vertical spread、risk reversal |
| P3 | 跨期限组合 | calendar、diagonal、term-structure exposure |
| P4 | 三至四腿组合 | butterfly、condor、iron condor、box spread |
| P5 | 非对称复杂组合 | ratio spread、backspread、seagull、collar |
| P6 | Barrier / touch | in/out、one-touch、no-touch、double barrier |
| P7 | Path-dependent | Asian、lookback、cliquet、forward-start |
| P8 | Multi-asset / structured book | basket、index、spread、best/worst-of、多产品多期限 position book |

P1 的 ANO/CNO 可加入双重验证。例如 call payoff 满足

$$
S_T\mathbf{1}_{\{S_T>K\}}
=(S_T-K)^+ + K\mathbf{1}_{\{S_T>K\}},
$$

因此 `ANO call = vanilla call + K × unit CNO call`。组合价格与 Greeks 还必须等于各 legs 按 position/notional 加权后的和。

### 模型假设轴 $M$

| Level | Model assumption | 核心测试内容 |
|---|---|---|
| M0 | Black--Scholes / Black-76 | 常波动率、解析公式、基准 conventions |
| M1 | Shifted BS / displaced diffusion | shift、负价格/利率域、链式法则 |
| M2 | CEV | 状态依赖波动率、elasticity、边界行为 |
| M3 | Mixture lognormal / mixture dynamics | regime weights、endogenous smile、mixture Greeks |
| M4 | Local volatility | Dupire surface、插值、局部波动率一致性 |
| M5 | Heston | stochastic volatility、correlation、vol-of-vol、Fourier/PDE |
| M6 | Jump diffusion | jump intensity、尾部风险、不连续 dynamics |
| M7 | CIR stochastic rates | short-rate dynamics、bond pricing、rate-sensitive risk |
| M8 | Hybrid models | BS--CIR、Heston--CIR、local-stochastic volatility |
| M9 | Model-risk comparison | 多模型校准、price/Greek instability、model-risk adjustment |

CIR 通常描述短利率而非 equity spot。任务必须分别声明 spot、volatility、rate dynamics 与相关矩阵，例如 `Heston + CIR rate`，不得含糊写成“CIR equity option”。

### 数值方法轴 $A$

| Level | 指定方法 | 必须固定的条件 |
|---|---|---|
| A0 | Closed-form analytic | 公式版本、参数 convention |
| A1 | Deterministic root finding | bisection/Newton/Brent、区间、初值、迭代规则 |
| A2 | Numerical differentiation | bump、forward/central stencil、计算顺序 |
| A3 | Quadrature / Fourier inversion | nodes、integration bound、transform convention |
| A4 | Lattice | binomial/trinomial、步数、tree construction |
| A5 | Finite difference / PDE | grid、boundary、time stepping、scheme |
| A6 | Monte Carlo | RNG、seed、paths、time steps、variance reduction |
| A7 | Calibration optimisation | objective、weights、initialisation、optimizer、stop rule |
| A8 | Mixed numerical pipeline | calibration + pricing + Greeks + scenario rerun |

### 数据与工具复杂度轴 $D$

| Level | 数据环境 | Agent 行为 |
|---|---|---|
| D0 | 参数直接给出 | 纯计算，无工具调用 |
| D1 | 单张结构化表 | 读取、筛选、类型转换 |
| D2 | 多张关联表 | join market、contract、curve、position |
| D3 | 原始 option chain | 清洗、构造 forward、反求 IV |
| D4 | DuckDB market snapshot | SQL 查询、期限匹配、curve interpolation |
| D5 | 缺失值和异常值 | arbitrage filter、stale quote、确定性修复 |
| D6 | 隐藏产品标签 | 根据 legs/payoff 识别交易结构 |
| D7 | 完整 agent environment | 查询、写代码、执行、诊断、修复、保存 artifact |
| D8 | Stateful/sequential market task | 多时点更新、重新校准、风险变化追踪 |

### 风险输出复杂度轴 $R$

| Level | 输出目标 | 内容 |
|---|---|---|
| R0 | 单一 price 或 Greek | scalar output |
| R1 | 完整 instrument metrics | price + first/second-order Greeks |
| R2 | 组合聚合 | leg-level 与 portfolio-level risk |
| R3 | Payoff analysis | breakeven、max profit/loss、piecewise payoff |
| R4 | Scenario analysis | spot、vol、rate、time shocks |
| R5 | PnL attribution | Delta/Gamma/Vega/Theta/cross-term attribution |
| R6 | VaR / ES | 指定 historical/parametric/MC 方法 |
| R7 | Smile/surface risk | skew、term structure、sticky-strike/delta |
| R8 | Model risk | model-to-model price/Greek difference |
| R9 | 完整风险报告 | structured JSON/table + explanation + artifacts |

### 套利发现轴 $F_{\mathrm{arbitrage\ finding}}$

$F$ 是正式的第七个难度维度，刻画 solver 在 mutated frozen snapshot 中搜索可执行静态套利
的复杂度。它不替代模型轴 $M$：$M$ 描述单个 pricing model 的数学复杂度，$F$ 描述同一
搜索 universe 中 pricing-model 来源的异质性、候选数据量以及 snapshot mutation 的数量和
类型。每个 $F$ variant 还必须冻结下面的原始计数与输出合同，而不能只保存 level：

$$
\chi_F=(n_{\mathrm{models}},n_{\mathrm{candidate\ rows}},n_{\mathrm{mutated\ cells}},
n_{\mathrm{mutation\ target\ types}},n_{\mathrm{invariant\ families}},o_F),
\qquad o_F\in\{\texttt{bool},\texttt{bool+type},\texttt{bool+spread}\}.
$$

| Level | Pricing-model 来源 | Snapshot mutation 与搜索范围 | ORM 验收答案 |
|---|---|---|---|
| F0 | 该轴默认不激活；arbitrage task 采用单一模型 | 小型 clean snapshot，`mutated_cells=0`，作为无套利负对照 | `(arbitrage_opportunity: bool)` |
| F1 | 单一模型，如全量 BSM | 小型候选集；1 个 cell、1 种 target type、1 个 invariant family | `(arbitrage_opportunity: bool)` |
| F2A | 单一模型 | 完整 option chain；至多 1 个隐藏的 option-price 或 underlying-spot point mutation | `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)` |
| F2B | 与 F2A 相同 | 与 F2A 相同；增加 exact argmax/reduction 要求 | `(arbitrage_opportunity: bool, maximal_spread: float)` |
| F3A | 单一模型 | 多个 mutated cells、多个 target types 与 invariant families | `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)` |
| F3B | 与 F3A 相同 | 与 F3A 相同；增加 exact argmax/reduction 要求 | `(arbitrage_opportunity: bool, maximal_spread: float)` |
| F4A | 多个 coherent marginal pricing models | 各模型分区显式；少量 mutation 混入跨模型候选集 | `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)` |
| F4B | 与 F4A 相同 | 与 F4A 相同；增加 exact argmax/reduction 要求 | `(arbitrage_opportunity: bool, maximal_spread: float)` |
| F5A | 多个 pricing models | 大型 snapshot；多个 mutated cells、target types、产品族与期限 | `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)` |
| F5B | 与 F5A 相同 | 与 F5A 相同；增加 exact argmax/reduction 要求 | `(arbitrage_opportunity: bool, maximal_spread: float)` |
| F6A | 多模型、多资产 joint snapshot | 跨 quote/contract/tradeable funding tables 的多类型 mutation 与大规模候选集 | `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)` |
| F6B | 与 F6A 相同 | 与 F6A 相同；增加 exact argmax/reduction 要求 | `(arbitrage_opportunity: bool, maximal_spread: float)` |

因此 A/B 不是新的第八轴，而是 $F$ 轴内部的两种 outcome projection。F2A/F2B、...、F6A/F6B
可以共享完全相同的 frozen child snapshot、候选模板和 mutation lineage，只改变 $o_F$；A 类
测试“是否能发现套利，并把所有已发现类型归为 cross-sectional、cross-asset 和/或 calendar”，B 类测试
“是否能按冻结归约合同求出最大 spread”。这些 levels 形成部分序而不是简单总序：例如 F3A 的
搜索范围可以大于 F2B，但两者验收的第二字段不同，curriculum 应分别记录 detection/type
mastery 与 spread mastery。

$F$ level 只表示搜索与输出难度，不能泄露分类标签。除明确的 F0 clean negative control 外，
F1 及所有 A/B levels 都必须同时包含 matched positive 与 negative children：positive mutation
故意跨越套利边界；negative child 可以是同分布的 clean subset，后续版本也可以加入仍保持全部
声明 no-arbitrage invariants 的 mutation。因此 `mutated_cells>0` 或某个 $F$ level 本身不推出
`arbitrage_opportunity=true`；dataset manifest 还需按 level 记录但不向 solver 泄露 label
balance。

多 pricing-model 来源只能来自 parent snapshot 中各自 coherent 且标识清楚的 underlying/product
分区；同一 payoff 不能仅因两个模型给出不同理论值就被判为套利。只有 solver 实际可交易、
currency/underlying/payoff/expiry/settlement 可按冻结模板比较或复制的报价才能进入 candidate
set。对 `pricing_model_id`、seed 或 correlation provenance 等不可交易元数据的 mutation 只构成
consistency task，不构成 `arbitrage_finding`，除非另外给出由它导致的可交易价格与复制关系。

Authoring 先选择一个通过全部 no-arbitrage checks 的 frozen parent snapshot，再以
copy-on-write 生成 child snapshot。母快照及其 id/revision 必须保持只读且 byte-identical；
child 获得新的 snapshot id/revision/lineage，按 $F$ contract 变异指定数量和类型的
solver-visible 数据后再次冻结。允许的 target types 只包括能够进入声明交易策略的报价或
payoff 定义，例如 option bid/ask、可交易 spot/forward、可交易 funding instrument quote，
以及 strike/expiry/settlement 等 contract fields。Solver 默认只看到 frozen child、公开交易
约定和难度 metadata；parent snapshot 与逐 cell before/after mutation manifest 属于 private
authoring provenance，不能通过直接 diff 泄露答案。

每个候选套利模板 $j$ 都固定为单位名义本金，并在声明的 bid/ask、funding、transaction-cost、
exercise 与 settlement 约定下计算 present-value spread $s_j$；模板本身必须保证其到期净
payoff 对所有声明的可达状态非负。所有 levels 都固定候选模板、枚举顺序和 bool 判定规则；
只有 B 类额外固定 dtype、spread 运算顺序、argmax/reduction order 与 float serialization。
内部 task truth 定义为

$$
s_{\max}=\max\left(0,\max_j s_j\right),\qquad
\texttt{arbitrage\_opportunity}=(s_{\max}>0).
$$

每个候选模板还必须在 authoring 时被唯一路由到下列类型之一：

- `cross-sectional`：在同一 valuation time 下，对同一 maturity/settlement bucket 内的
  option claims 进行 strike shape 或其他纯 option 横截面比较；
- `cross-asset`：在同一 valuation time 下，用 executable underlying、funding/carry 与 option
  claims 构造 bounds、put-call parity 或其他 underlying-option 策略，并计入每条腿的交易成本；
- `calendar`：候选策略或复制关系跨越两个或以上 maturity/cashflow dates，并使用
  声明的 funding、carry、exercise 与 settlement 合同将不同日期的价值放到同一基准下比较。

唯一路由按 `calendar`（跨 cashflow dates）、`cross-asset`（同日期 bucket 的 underlying-option）、
`cross-sectional`（纯 option 横截面）的优先级决定；一个 output array 出现多个类型，表示不同的
positive candidate 分别命中了这些类型。

对 F2A--F6A，`arbitrage_type` 是所有 $s_j>0$ 候选模板类型的并集，序列化为固定顺序
`["cross-sectional", "cross-asset", "calendar"]` 的子序列。因此 canonical 结果为 `[]` 或该
三元素固定序列的任一非空子序列，共八种。这个数组表示 AND/OR，不得用顺序不定的 set 或
自由文本替代。并且必须满足

$$
\texttt{arbitrage\_opportunity}
=\left(\lvert\texttt{arbitrage\_type}\rvert>0\right).
$$

这个类型集合由 verifier 从 solver-visible child 和冻结候选模板重算，不得从 mutation
intention 或 private expected violation id 直接复制。F2A--F6A 不得包含无法归入这三类
的 positive 候选模板；继续增加类型时必须再次版本化扩展 output contract。

令

$$
\begin{aligned}
\mathcal F_{\mathrm{bool}}
&=\{F0,F1\},\\
\mathcal F_{\mathrm{type}}
&=\{F2A,F3A,F4A,F5A,F6A\},\\
\mathcal F_{\mathrm{spread}}
&=\{F2B,F3B,F4B,F5B,F6B\}.
\end{aligned}
$$

则 ORM answer contract 为

$$
Y_F=
\begin{cases}
(\texttt{arbitrage\_opportunity}:\texttt{bool}),
&F\in\mathcal F_{\mathrm{bool}},\\
(\texttt{arbitrage\_opportunity}:\texttt{bool},\
 \texttt{arbitrage\_type}:\texttt{ordered enum array}),
&F\in\mathcal F_{\mathrm{type}},\\
(\texttt{arbitrage\_opportunity}:\texttt{bool},\
 \texttt{maximal\_spread}:\texttt{float}),
&F\in\mathcal F_{\mathrm{spread}}.
\end{cases}
$$

生成器必须让全部样本远离零判定边界，B 类还必须远离 float canonicalization 边界。无套利时
F0/F1 canonical answer 为 `(false)`，A 类为 `(false, [])`，B 类为 `(false, 0.0)`。B 类有套利时
`maximal_spread` 是共同货币下每单位名义本金的最大 canonical spread，禁止通过放大仓位制造
无穷结果。这里 `(bool)` 表示 schema 中的一字段 typed tuple，`arbitrage_type` 表示
上述固定顺序的 typed enum array，二者都不是未定义的自由文本；其括号、boolean token
与 serialization 同样由 manifest 冻结。

LLM 仍须输出完整九字段 trajectory，并在 `Outcome` 中按 $Y_F$ 给出最终验收答案。
`arbitrage_finding` 的 ORM reward projection 只抽取并 exact-compare 对应的一字段或二字段
tuple；trajectory 由 episode schema 收集，用于训练、能力归因和审计，但不与 hidden oracle
trajectory 比较，也不增加 ORM 验收字段。LLM 不需要在最终答案中输出 argmax instrument、
portfolio legs 或套利证明；A 类必须输出 `arbitrage_type` 但不需要输出
`maximal_spread`，后者只由 hidden verifier 按冻结模板复算。

## 产品—模型—方法 Compatibility Registry

| 产品/模型 | Analytic | Root finding | Fourier | Tree/PDE | Monte Carlo |
|---|---:|---:|---:|---:|---:|
| BS vanilla | 强 | IV | 可选 | 可用 | 可用 |
| BS CNO/ANO | 强 | IV 可选 | 可选 | 可用 | 不优先 |
| Shifted BS vanilla | 强 | IV | 可选 | 可用 | 可用 |
| CEV vanilla | 部分 | 校准 | 不优先 | 强 | 可用 |
| Mixture vanilla | mixture analytic | 校准 | 可选 | 可选 | 可用 |
| Heston vanilla | 半解析 | 校准 | 强 | 强 | 可用 |
| Barrier | 部分模型可用 | — | 较少使用 | 强 | 强 |
| Asian/lookback | 少量特例 | — | 部分可用 | 可用 | 强 |
| Same-currency basket/index/spread | 少量特例 | 校准可选 | 依模型 | 可用 | 强 |
| Hybrid stochastic-rate | 很少 | 校准 | 部分可用 | 强 | 强 |

Generator 只从 registry 采样合法组合。故意违反 compatibility 时，target 必须改成 method/model mismatch classification 或 pipeline repair，不能把伪任务当成数值定价题。

对 F4A/F4B 及更高 base level 的多模型 snapshot，registry 先逐一验证每个 source-model partition 的
product--model--method compatibility，再验证跨 partition 的候选套利模板只连接经济上可比较
或可静态复制的 claims。模型异质性只增加搜索与路由难度，不豁免共同 currency、numeraire、
valuation timestamp、rate-path context 和 settlement convention。

## 受约束 Task Mutation Engine

母题 $\tau_0$ 通过 mutation operator $\mu$ 变为

$$
\tau_1=\mu(\tau_0;\texttt{seed},\texttt{constraints}),
$$

并在生成 snapshot 与 answer 前执行 compatibility check。Mutation 类型包括：

- **单轴上调/下调：** 只改变 $(L,P,M,A,D,R,F)$ 中一个坐标；
- **同级替换：** 难度近似不变但切换产品、模型或方法；
- **单轴反事实：** 其余输入保持不变，用于能力归因；
- **多轴组合：** 构造后期 curriculum 的完整 workflow；
- **错误定向 mutation：** 根据 rollout 的 sign、convention、calibration、tool-use 等错误生成 adversarial variants；
- **`arbitrage_finding`：** 从合法 frozen parent snapshot 复制出具有新身份的 child，按目标 $F$ level 变异规定数量与类型的公开可交易数据并再次冻结；F0/F1 ORM 验收 canonical bool，F2A--F6A 验收 bool 与 canonical `arbitrage_type`，F2B--F6B 验收 bool 与 maximal spread，禁止原地修改母快照；
- **不兼容 mutation：** 故意制造 product--model--method mismatch，目标是识别并拒绝错误设定。

对于联合市场，mutation engine 还可以在不改变七维坐标编号的情况下，对 `underlying_dependence` 子合同执行受约束 mutation：

- 资产数量、underlying/model driver order 与边际模型组合；derivative contracts 不进入矩阵；
- identity、full、block 或 factor-implied correlation structure；
- normal、stress、crisis correlation regime；
- 在每个时间点均保持 PSD 的 time-varying $R_t$；
- asset--asset、spot--volatility 与 asset--rate driver correlation；
- index/basket weights 与 constituent dependence；
- 单轴反事实：固定全部 marginal snapshots，只改变一个合法 correlation entry 或 $\Lambda_t$ factor loading；
- adversarial mutation：边际 option surfaces 全部合法，但完整 underlying correlation matrix 非 PSD，或联合衍生品价格来自不一致的 underlying-dependence snapshot。

合法 mutation 必须保持共同 $\mathbb Q$、numeraire、共享 rate path 和所有 marginal model snapshots 不变，除非 operator 明确声明这些字段也是 mutation target。普通 adversarial task 应只破坏一个可归因约束；`arbitrage_finding` 可按 $F$ level 破坏多个约束，但必须逐 cell 保存完整 mutation set 与预期受影响的 invariant families，不能混入未登记的第三类异常。

每个 child task 必须保存：

```json
{
  "parent_task_id": "task_0001",
  "child_task_id": "task_0001_m03",
  "operator": "change_model_assumption",
  "before": {"L": 1, "P": 0, "M": 0, "A": 0, "D": 0, "R": 1, "F": "F0"},
  "after":  {"L": 1, "P": 0, "M": 2, "A": 3, "D": 0, "R": 1, "F": "F0"},
  "seed": 20260804,
  "engine_id": "deterministic-task-mutation-v1"
}
```

`arbitrage_finding` child 还必须在 private authoring provenance 中保存
`parent_snapshot_id`、`child_snapshot_id`、$\chi_F$、
全部 source pricing-model ids、逐 cell 的 table/row/field/before/after/target type、候选套利模板
版本；B 类另存 maximal-spread reduction order。同一 snapshot 的 A/B pair 只改变 $o_F$，
不得重新抽样 mutation。这使 mutation pair 成为因果式能力对照：原题通过而
只改 $M$ 后失败，主要指向 model-assumption gap；只改 $D$ 后失败，主要指向 data/tool-use
gap；提高 base level 后失败主要指向套利搜索或模型路由能力，而同级 A 通过、B 失败则主要
指向 maximal-spread 归约能力。该结论
是受控归因而非绝对因果证明，但比无结构题库的总体准确率更可解释。

## Adaptive Curriculum

### Stage 定义

| Stage | 主要采样空间 | 训练目标 |
|---|---|---|
| 0 数值与约定冷启动 | L0 × P0 × M0 × A0 × D0 × R0 × F0 | dtype、discounting、sign、units、schema |
| 1 完整 vanilla | L1 × P0 × M0 × A0--A2 × D0--D1 × R1 × F0 | price、IV、完整 Greeks |
| 2 多腿组合 | L2 × P1--P5 × M0 × A0--A2 × D1--D2 × R2--R3 × F0 | 结构识别、聚合、payoff、复制恒等式 |
| 3 IV/smile/期限结构 | L3 × P0--P4 × M0--M3 × A1/A7 × D2--D4 × R1/R7 × F0 | IV inversion、smile/surface、calendar |
| 4 非 BS dynamics | L3--L4 × P0--P5 × M1--M6 × A3--A7 × D2--D4 × R4/R7/R8 × F0 | model--product--method matching、校准、model risk |
| 5 Exotic/path-dependent | L3--L5 × P6--P7 × M0--M6 × A4--A6 × D3--D5 × R4--R8 × F0 | barrier、monitoring、path construction |
| 6 随机利率/同币种多资产/hybrid | L4--L5 × P0--P8 × M0--M8 × A3--A8 × D3--D7 × R4--R9 × F0 | 共享利率路径、PSD 相关结构、联合定价、joint calibration |
| 7 Arbitrage finding | L2--L6 × P0--P8 × M0--M9 × A0--A8 × D1--D8 × R2--R9 × {F1, F2A/F2B, ..., F6A/F6B} | 先学 bool detection，A variant 再学 cross-sectional/cross-asset/calendar 分类，B variant 学 maximal spread |
| 8 Agentic risk workflow | L5--L6 × P0--P8 × M0--M9 × A0--A8 × D4--D8 × R5--R9 × {F0, F1, F2A/F2B, ..., F6A/F6B} | DuckDB→识别→校准/套利扫描→定价→风险→artifact |

Stage 4 的推荐内部顺序为：

$$
\text{Shifted BS}\rightarrow\text{CEV}\rightarrow\text{Mixture}\rightarrow\text{Heston}\rightarrow\text{Local vol/jumps}.
$$

### 采样、晋级与 replay

默认 mixture 为 20% 已掌握任务、60% 当前阶段、20% 下一阶段探索任务。根据冻结模型在滚动窗口中的 `pass@1` 调整：

| pass@1 | 调度动作 |
|---:|---|
| >95% | 降低该 cell 权重，保留 replay |
| 80%--95% | 标记基本掌握，逐步晋级 |
| 20%--80% | 作为 RL 核心分布 |
| 5%--20% | 少量保留，结合 verifier-guided repair 或任务分解 |
| <5% | 暂缓直接 RL，先 SFT、降轴或拆解 |

晋级不能只看最终 reward，还要记录 numeric correctness、method compliance、schema compliance、tool execution、invariant checks、fixed-seed rerun consistency 与 verifier-guided retry improvement。这些是 diagnostics 与 scheduler state，不改变最终 $R_{\mathrm{ORM}}\in\{0,1\}$。

### Demo 最小 curriculum

| Batch | 配置 | 目的 |
|---|---|---|
| B0 | BS vanilla，单价格/Greek | 验证 generator 与 hard verifier |
| B1 | BS + IV + complete Greeks | 测试逆问题与多字段 all-pass |
| B2 | straddle/spread/butterfly/CNO/ANO | 测试结构识别、聚合与静态复制 |
| B3 | multi-asset option chains + mixture/Heston + PSD correlation + DuckDB | 展示联合市场与完整 agentic workflow |
| B4 | mutated frozen snapshots；先 F1/F2A，再做同 snapshot 的 F2B，随后逐级推进 F3A/F3B--F5A/F5B | 将 bool detection、cross-sectional/cross-asset/calendar 分类与 maximal-spread 归约拆开评估 |

冻结基础模型即可先评估 `pass@1`、`pass@N`、verifier-guided retry、分层成功率、失败类型、固定 seed 重跑一致性与 verifier mutation-test 拦截率。若中间难度存在 rollout variance 且 retry 明显提升，就说明该环境具有可探索、可验证的 reward landscape。

## 工程模块与仓库边界

三个一等模块必须职责分离：

| 模块 | 回答的问题 | 不负责什么 |
|---|---|---|
| `task_space` | 哪些坐标和组合合法 | 不生成任务、不决定采样 |
| `mutation` | 如何从母题生成有 lineage 的变体 | 不根据模型表现安排训练 |
| `curriculum` | 当前采样哪些任务、权重是多少 | 不修改任务本身 |

建议核心目录：

```text
configs/{task_space,mutations,curricula}/
datasets/{generated/{base,mutated,splits},manifests/{tasks,lineage,curricula}}/
src/synthetic_derivatives/{task_space,mutation,curriculum,authoring,solver,training,verifier}/
schemas/{task,difficulty,mutation,curriculum,manifest,snapshot,trajectory,submission}.schema.json
tests/{unit,integration,verifier_robustness,public}/
runs/{rollouts,evaluations,curriculum_state}/
```

原 `tests/mutation/` 应改名为 `tests/verifier_robustness/`，避免与正式 task mutation engine 混淆。端到端闭环为：

$$
\text{Base Task}\rightarrow\text{Mutation Engine}\rightarrow\text{Candidate Pool}
\rightarrow\text{Curriculum Scheduler}\rightarrow\text{Solver Rollout}
\rightarrow\text{Hard Verifier}\rightarrow\text{Mastery Update}.
$$

## 适合生成的任务族

*表 3：确定性衍生品任务族*

| 任务族                    | Solver 需手工实现                                                             | Verifier 可调用的 package oracle                                                            |
|:--------------------------|:------------------------------------------------------------------------------|:--------------------------------------------------------------------------------------------|
| European analytic pricing | BSM/Black-76 公式、normal CDF/PDF                                             | QuantLib 或 py_vollib 的 analytic engine；输出按共同 canonicalizer 精确化。                 |
| Analytic Greeks           | Delta/Gamma/Vega/Theta/Rho 及约定换算                                         | QuantLib/py_vollib analytic Greeks；不得换成 finite difference。                            |
| Finite-difference Greeks  | 指定 stencil、bump、边界处理与运算顺序                                        | 同一 package price engine 后由 pytest 执行完全相同的 stencil；不得直接读取 analytic Greek。 |
| Implied volatility        | 指定 Newton/bisection/Brent、bracket、初值与迭代规则                          | package pricing function 加同一 root algorithm，或调用算法完全匹配的 pinned solver。        |
| Smile fit                 | 指定坐标、design matrix、OLS/WLS/regularization 与线性代数例程                | NumPy/SciPy/statsmodels 中同一 routine、参数、row 与 coefficient order。                    |
| Multi-leg portfolios      | straddle/spread/butterfly/condor/CNO/ANO 的 legs、方向、payoff 与 Greeks 聚合 | 各腿同方法 oracle 加权求和，并检查静态复制、breakeven 与 payoff identity。                  |
| Arbitrage finding         | 输出完整 trajectory；F0/F1 给 `(arbitrage_opportunity: bool)`，F2A--F6A 再给 canonical `arbitrage_type`，F2B--F6B 改为给 `maximal_spread` | 重放 frozen parent-to-child mutation 与单位名义模板；ORM 按 $F$ exact-compare 一字段或二字段 tuple，不比较 trajectory。 |
| Joint multi-asset pricing | 读取共同 $\mathbb Q$/rate path、按固定 $R_t$ 构造联合 shocks、计算 basket/index/spread payoff | 同一 joint process、driver order、factorization 与冻结 paths 下的 package/reference oracle。 |
| Joint-consistency detection | 检查矩阵 symmetry/diagonal/PSD、dependence id 与联合定价来源；定位唯一违规约束 | pytest 精确检查 eigen/factorization contract、lineage 与 joint-pricing provenance。          |
| Exotic/path-dependent     | barrier/touch/Asian 等指定 monitoring、path 与 boundary 逻辑                  | QuantLib 同模型同 engine，或同一冻结 path/grid 的 reference implementation。                |
| Model calibration/risk    | CEV/mixture/Heston/local-vol 参数校准、跨模型 repricing                       | 同 objective、initialisation、optimizer 与 method 的 pinned calibration pipeline。          |
| Monte Carlo price/Greeks  | 指定 RNG/shocks、path construction、estimator、variance reduction 与归约顺序  | pytest 使用同一冻结 paths 与 estimator；不得改用 analytic/PDE oracle。                      |
| VaR/ES                    | 指定 parametric、historical 或 MC 定义、quantile convention 与 scenario order | pytest 使用同一风险方法与完全相同的 scenario/quantile rule。                                |
| Surface interpolation     | 固定 grid、插值、边界与 extrapolation                                         | QuantLib/SciPy 的同一 pinned interpolator 与参数。                                          |

每个 variant 只选择一个明确任务合同；analytic Greeks、finite-difference Greeks 与 Monte Carlo Greeks 应作为不同 variants。可以在一个 episode 中串联 price $\rightarrow$ IV $\rightarrow$ Greeks $\rightarrow$ smile，但每个阶段的方法、精度、舍入 checkpoint 与 canonical output 都必须冻结。

## Agent Trajectory Schema

### 统一九字段

顶层 schema 固定为

$$
\boxed{
\begin{gathered}
\text{Problem, Context, Assumptions, Skills, Evidence,}\\
\text{Intermediate Reasoning, Verification, Confidence, Outcome}.
\end{gathered}
}
$$

*表 4：九字段在衍生品任务中的含义*

| 字段                   | 金融衍生品含义                                                                                           |
|:-----------------------|:---------------------------------------------------------------------------------------------------------|
| Problem                | instrument、冻结 market snapshot、joint-dependence snapshot 与 price/Greek/IV/smile/arbitrage-finding 目标。              |
| Context                | 七维坐标、共同 $\mathbb Q$/numeraire/rate path、marginal models、$R_t$、$\chi_F$、day-count、method、seed 与 package policy。 |
| Assumptions            | coherent marginal models、合法或故意破坏的 joint structure、quote validity、root existence 与 no-arbitrage scope。 |
| Skills                 | derive formula、identify structure/model、root solve、differentiate、calibrate、query tools、check invariants。 |
| Evidence               | 输入 snapshot、真实 tool output、iteration trace、residuals 与 fitted artifacts。                        |
| Intermediate Reasoning | state–action–observation–next-state trajectory，不包含 hidden oracle。                                   |
| Verification           | import/method audit、pytest package recomputation、canonical exact equality、invariants 与 test report。 |
| Confidence             | convergence flag、residual、stability 与 verified/unverified 状态。                                      |
| Outcome                | canonical numeric/table answer、tests pass/fail 与 terminal reward；`arbitrage_finding` 按 $F$ 必含最终 `(bool)`、`(bool, ordered enum array)` 或 `(bool, float)` tuple。 |

`arbitrage_finding` 的九字段仍全部由 LLM 生成并保存；其中 Evidence、Intermediate Reasoning、
Verification 等字段可包含查询、扫描和自检轨迹。Trajectory collector 负责 episode schema，
而 outcome ORM 不为这些自由度较高的字段构造 hidden reference，也不按其内容加减 reward。

### 推荐 trajectory

典型 episode 的 actions 为：

$$
\begin{aligned}
&\text{parse coordinates/contract}
\rightarrow \text{query and validate inputs}
\rightarrow \text{identify structure/model}\\
&\rightarrow \text{derive conventions}
\rightarrow \text{implement specified method}
\rightarrow \text{price/calibrate/risk}\\
&\rightarrow \text{check residuals/invariants}
\rightarrow \text{serialize output/artifacts}.
\end{aligned}
$$

每一步保存真实 observation；hidden package output 只在 episode 终止后由 verifier 使用。可以保留 iteration history 作为 agent data，但不通过 step reward 暗示正确答案。

## Pytest Package-Backed Hard Verifier

### Verifier contract

每个 variant 的 verifier manifest 至少包含：

$$
\begin{gathered}
(\texttt{task coordinates},\texttt{lineage ids},\texttt{backend package},\\
\texttt{package version},\texttt{function/engine},\texttt{method id},\\
\texttt{market conventions},\texttt{joint dependence spec},\\
\texttt{dtype/operation order},\\
\texttt{decimal places},\texttt{rounding mode},\texttt{canonical schema},\\
\texttt{banned imports},\texttt{test ids}).
\end{gathered}
$$

Trusted verifier 直接在 pytest fixture 中构造 package objects、加载冻结 market snapshot，并按与 solver 相同的 method contract 复算 outputs。Oracle 与 submission 都被强制转换为指定 dtype，再按同一 canonical schema 序列化并逐字段或逐字节比较：

$$
\texttt{canonical(submission)}
=
\texttt{canonical(package\_oracle)}.
$$

`arbitrage_finding` 使用更窄且由 level 决定 schema 的 outcome projection。设 $\pi_F$ 只读取
LLM 完整 trajectory 中 `Outcome.orm_answer`：当 $F\in\mathcal F_{\mathrm{bool}}$ 时读取一字段
bool tuple，当 $F\in\mathcal F_{\mathrm{type}}$ 时读取 bool/ordered-enum-array 二字段 tuple，当
$F\in\mathcal F_{\mathrm{spread}}$ 时读取 bool/float 二字段 tuple。答案比较统一为

$$
\texttt{canonical}\!\left(\pi_F(\texttt{submission})\right)
=
\texttt{canonical}\!\left(Y_F\right).
$$

bool 始终按 canonical boolean 比较；A 类的第二个字段按固定 enum 与顺序逐项 exact-compare；
B 类的第二个字段按 manifest 指定 dtype、运算顺序与 float serialization 生成后
exact-compare。若 A 类缺少 `arbitrage_type`、类型顺序错误、类型与 bool 不一致或多输出
`maximal_spread`，或 B 类缺少 `maximal_spread`，schema 不匹配并直接失败。完整 trajectory
仍是 LLM episode 输出，但 $\pi_F$ 不读取其余八个字段，也不读取 `Outcome` 中
ORM answer 以外的解释或 artifact。

因此 verifier 不调用 `pytest.approx`、`math.isclose`、`numpy.isclose` 或 `allclose`，也不保存 `atol`/`rtol`。若 schema 本身要求定点 decimal，该定点表示属于输出类型而不是 verifier tolerance；数值算法内部的停止规则同样属于 method contract。

### 测试层

<a id="tab:tests"></a>

*表 5：金融 hard verifier 的 pytest tests*

| 测试                 | 断言                                                                                        |
|:---------------------|:--------------------------------------------------------------------------------------------|
| Submission schema    | 必需字段、row/strike/maturity 顺序、单位、无 NaN/Inf。                                      |
| Snapshot identity    | market snapshot id/revision、seed、generator version 一致。                                 |
| Joint-market identity | 共同 $\mathbb Q$/numeraire/rate path id、driver order、dependence spec 与 marginal snapshot ids 一致。 |
| Correlation validity | $D_t\succeq0$ 且为对角矩阵，$R_t=\Lambda_t\Lambda_t^\top+D_t$，并精确满足 symmetry、单位对角、元素界与 PSD/PD policy。 |
| Import compliance    | 无禁止库、无隐藏文件访问、无动态安装/网络加载。                                             |
| Method identity      | solver 声明的 method id、关键 method artifacts 与 verifier contract 精确一致。              |
| Difficulty identity  | $(L,P,M,A,D,R,F)$ 坐标、compatibility decision 与 task-family id 精确一致。                 |
| Mutation lineage     | parent/child/engine/operator/seed 完整，child snapshot 可重放。                            |
| Arbitrage ORM answer | 按 $F$ 仅抽取 `Outcome.orm_answer` 的 `(bool)`、`(bool, ordered enum array)` 或 `(bool, float)`；与 verifier 重放得到的 $Y_F$ exact equality。 |
| Price                | package oracle 与提交值经过共同 canonicalization 后字符串完全相等。                         |
| Greeks               | method、definition、scaling 与 canonical value 逐字段完全相等。                             |
| Implied volatility   | 同一 root method 产生的 canonical IV、status 与 iteration contract 完全相等。               |
| Smile/surface        | coordinate、basis、weights、routine、coefficient order 与 canonical coefficients 完全相等。 |
| Financial invariants | 合法边际模型继承的 put–call parity、bounds、monotonicity/convexity 等实现层 sanity checks，或任务指定性质。 |
| Joint pricing        | basket/index/spread 等使用同一 joint process、correlation snapshot 与联合状态，provenance 完整。 |
| Portfolio identities | leg aggregation、straddle/spread/butterfly、ANO/CNO 等静态复制关系。                        |
| Artifact contract    | SQL/code/JSON/table/report 等要求的文件存在且 schema/version 正确。                        |
| Edge cases           | deep ITM/OTM、short maturity、low vega、root failure 的指定行为。                           |

金融 hard verifier 的 pytest tests

对 `arbitrage_finding`，表中除 `Arbitrage ORM answer` 外的适用检查都只作为发布前 gate、
运行环境诊断或 trajectory audit，不进入 $R_{\mathrm{ORM}}^{\mathrm{arb}}$；不能因为 trajectory
schema、文字解释、method declaration 或 artifact 的差异改变该任务的 outcome reward。

### 二值 Outcome Reward

$$
R_{\mathrm{ORM}}
=\mathbf{1}\!\left\{
\bigwedge_{j=1}^{J}
\left(\texttt{pytest\_test}_j=\texttt{PASS}\right)
\right\}.
$$

单项测试的偏差和错误类型可以记录进 diagnostics，但任何一项失败都不能被其他正确项或长推理过程抵消。若任务希望分阶段 curriculum，应拆成多个独立 variants，而不是把 hard verifier 改成主观加权分。

上式适用于普通 task family。对 `arbitrage_finding`，发布前的 snapshot identity、lineage、
模板合法性与重放检查仍须全部通过，但它们属于 authoring/infrastructure gate；发布后的 outcome
reward 只由 tuple 决定：

$$
R_{\mathrm{ORM}}^{\mathrm{arb}}
=\mathbf{1}\!\left\{
\texttt{canonical}\!\left(\pi_F(\texttt{submission})\right)
=\texttt{canonical}\!\left(Y_F\right)
\right\}.
$$

因此 trajectory 的长短、措辞、所选中间路径或是否显式写出 argmax portfolio 都不改变
`arbitrage_finding` 的 ORM reward；但 LLM 仍按 trajectory schema 输出完整 episode。A 类的
ORM 读取 `arbitrage_type` 而不读取 maximal spread，B 类则读取 maximal spread 而不读取
`arbitrage_type`。

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

1.  用固定 seed materialize market snapshot 并保存 snapshot id/revision；
2.  检查输入定义域、price bounds、IV root existence 与 method feasibility；
3.  用同方法的 pinned package backend 生成 canonical answer；
4.  重复运行 pytest，确认 expected files 在目标环境中 byte-identical；
5.  拒绝落在舍入边界附近或跨目标平台不能稳定 canonicalize 的样本；
6.  将任一 canonical 字段最后一位改变 $1$，确认 exact-equality test 失败；
7.  检查 solver 容器确实无法 import 被禁 packages；
8.  将 package lock、wrapper version 与 test manifest 纳入 verifier id；
9.  对 mutation child 检查 compatibility、parent/child ids、单轴不变量与可重放 lineage；
10. 用故意错误的数值、方法、schema、坐标和 lineage 做 verifier robustness mutation tests；
11. 冻结模型重复评估 `pass@1`、`pass@N`、repair gain、难度 cell 成功率与 fixed-seed consistency；
12. 检查 curriculum scheduler 只改变采样权重，不修改 frozen task 或 hard reward 定义；
13. 对同币种多资产 snapshot 检查共同 $\mathbb Q$、numeraire、rate path id、underlying/model driver order 与 marginal snapshot ids，并拒绝 derivative ids 进入相关矩阵；
14. 检查 $D_t\succeq0$ 且为对角矩阵、$R_t=\Lambda_t\Lambda_t^\top+D_t$、单位对角与 PSD/PD policy，并固定 matrix dtype、driver order 与输出顺序；
15. 固定全部 marginal inputs，只切换 identity matrix 与合法非对角 $R_t$，确认每个单资产边际 price/surface 保持不变；
16. 将一个非对角 underlying 相关项或联合定价 dependence id 故意改错，确认 joint-consistency test 拒绝样本；
17. 把 put--call parity、strike monotonicity/convexity 与 calendar consistency 作为边际模型实现的 sanity checks，而不是重复的 generator 构造约束。
18. 对 `arbitrage_finding` 确认 parent snapshot 保持 byte-identical、child 使用新 snapshot identity，并精确核对 $\chi_F$、source-model partitions 及逐 cell mutation manifest；
19. 逐个验证单位名义套利模板的 payoff 非负性与交易/结算可执行性，并拒绝位于零判定边界附近的样本；B 类还须按冻结 dtype、枚举和 reduction order 重放 $s_{\max}$，并拒绝 float canonicalization 边界样本；
20. 保持最终 ORM answer 不变而任意改变 trajectory 文本，确认 $R_{\mathrm{ORM}}^{\mathrm{arb}}$ 不变；对 A 类翻转 boolean、遗漏/错序/错分 `arbitrage_type` 或额外提交 spread 均应失败，对 B 类翻转 boolean、遗漏 spread 或改变 spread 最后一位均应失败。

### 发布门槛

一个金融衍生品 variant 只有同时满足下列条件才可发布：

- generator/seed/RNG/config 完整且 snapshot 已冻结；
- 七维坐标、task-family id、compatibility decision 与 mutation lineage 完整；`arbitrage_finding` 还须包含 $\chi_F$、source-model routing、mutation manifest、套利模板及与 A/B level 一致的一字段或二字段 ORM projection；
- instrument、model、calendar、day-count 与 Greek/IV/smile conventions 无歧义；
- 同币种 scope、共同 $\mathbb Q$/numeraire/rate path、coherent marginal models 与 underlying-dependence contract 无歧义；
- underlying 相关矩阵合法、不包含 derivative ids，且所有多资产 payoff 由同一 joint underlying process 与 dependence snapshot 定价；
- solver allowlist/denylist 可由环境强制执行；
- package-backed pytest 能以同一方法复算全部 canonical outputs；
- 每个字段的精度、舍入、canonical representation 与 edge-case failure behavior 已指定；
- verifier manifest 不含数值 tolerance，全部数值字段使用 exact equality；
- hidden tests 与 package oracle 不对 solver 可见；
- tests 全通过才记录 $R_{\mathrm{ORM}}=1$；
- curriculum state 与 verifier diagnostics 可复现，但不泄露 hidden oracle，也不改变二值 reward。

#### 一句话总结

金融衍生品市场：**出题端用 pinned QuantLib 统一生成并冻结 underlying/option snapshot；同币种多资产市场由 coherent marginal pricing models、共同 $\mathbb Q$/numeraire、共享利率路径与 underlying-driver PSD correlation perturbation 组成，derivative contracts 不进入相关矩阵，single-asset consistency 由边际模型继承，多资产 payoff 必须由同一 joint underlying process 定价；以 $\tau=(L,P,M,A,D,R,F)$ 定义七维 task grammar，其中 $F$ 按 pricing-model 来源、候选数据量及 snapshot mutation 的数量与类型控制 arbitrage-finding 难度，并从 F2 起用 A/B pair 分离 type classification 与 exact spread reduction；通过 compatibility-constrained mutation 扩题，通过 adaptive curriculum 按 mastery 采样；LLM 必须手搓规定方法并输出完整 trajectory。普通任务由 pytest 按相同模型、方法、dtype、操作顺序和 schema exact equality 验收；`arbitrage_finding` 的 F0/F1 ORM 验收 `(arbitrage_opportunity: bool)`，F2A--F6A 验收 `(arbitrage_opportunity: bool, arbitrage_type: cross-sectional AND/OR cross-asset AND/OR calendar)`，F2B--F6B 验收 `(arbitrage_opportunity: bool, maximal_spread: float)`，trajectory 不参与该 outcome equality。最终 reward 始终是二值 ORM；七维 diagnostics 只用于能力归因、task mutation 与 curriculum 调度，不引入 verifier tolerance。本阶段不考虑 FX、quanto 或 cross-currency derivatives。**

## 参考资料

1.  <a id="ref-1"></a> pytest documentation. *pytest: helps you write better programs*.  
    <https://docs.pytest.org/>

2.  <a id="ref-2"></a> QuantLib. *A free/open-source library for quantitative finance*.  
    <https://www.quantlib.org/>

3.  <a id="ref-3"></a> py_vollib documentation and source repository.  
    <https://github.com/vollib/py_vollib>
