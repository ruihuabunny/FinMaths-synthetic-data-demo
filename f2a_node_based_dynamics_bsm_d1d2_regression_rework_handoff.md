# F2A Node-Based Dynamics + BSM \(d_1,d_2\) Regression：Codex 返修说明

> **Revision marker — 2026-08-08**  
> 本版本已将 Step 2 从“逐 row implied-volatility inversion”正式改为“逐 option 时间序列的 BSM \(d_1,d_2\) price-term constrained regression”。如果你看到的 Step 2 仍以逐 row IV inversion 为核心，那就是旧版本。

> 目标分支：`f2a-2nd-revised`  
> 适用范围：authoring pipeline、F2A task specification、public/private schema、hard verifier 与相关 Markdown 文档。

## 1. 最终任务定义

F2A 不是一个简单的静态套利扫描器，而是一条完整的 quant agent trajectory：

\[
\boxed{
\text{node-based underlying dynamics fitting}
\rightarrow
\text{per-option BSM }d_1/d_2\text{ price-term regression}
\rightarrow
\text{mutation localisation}
\rightarrow
\text{X/U/T executable arbitrage verification}
}
\]

必须保留前三个建模环节，不能把任务简化成只枚举 payoff identities；但最终的“可执行套利”结论必须由计入交易成本的现金流 certificate 验证，而不能只由 model residual 决定。

## 2. 生成器的既定事实

返修必须以当前 authoring pipeline 的真实生成机制为准：

### 2.1 Underlying

- 每条 underlying 的物理测度 drift 与 diffusion 都由有限个 nodes 定义；
- node 日期（或相对 `time_origin` 的 `day_offset`）对应已知 event dates；
- node value 是未知、需要拟合的参数；
- nodes 之间采用线性插值；
- node support 之外采用 flat extrapolation；
- close-to-close 区间可能跨周末或跨 node，必须对 drift 与 squared diffusion 做精确区间积分；
- endpoint transition 是与上述积分一致的 time-inhomogeneous GBM transition，并由 QuantLib/authoring pipeline 生成；
- 多资产共享 interest-rate path，并通过合法 correlation matrix 对 Brownian residuals 施加联合相关结构。

因此，Agent **不需要**完成：

- changepoint detection；
- 未知 knot 搜索；
- 任意函数估计；
- GBM/CEV/Heston 等开放式 physical-model family selection。

Agent 只需要在公开的 node grid 上拟合 drift node values 与 diffusion node values。

### 2.2 Options

- vanilla option 报价的 pricing family 固定为 BSM；
- base quotes 由 BSM prices 加受控 quote noise 得到；
- mutation 在 frozen、无套利的 base DB 上制造指定的 X/U/T signature；
- 每条 option 的多 valuation-date 报价序列可以独立拟合其 BSM pricing parameters；不要求整张 surface 共享单一常数 volatility；
- BSM identification 应直接围绕定价公式中的 \(d_1,d_2\) 两个 price terms 构造固定 regression-style loss；
- 不应把“逐 row 释放一个独立 volatility 并反演 IV”作为模型识别步骤，因为严格价格界内的任意单个报价都能映射为某个 IV，这无法检验整条报价序列是否遵循生成器的 BSM 参数结构；
- implied-volatility inversion 仅在题目额外要求逐 row IV 时作为可选派生输出，不再是 Step 2 的核心。

## 3. Step 1：已知 event-node dates，拟合 node values

### 3.1 公开参数化

对 underlying \(u\)，公开 node dates：

\[
\tau_{u,0}<\tau_{u,1}<\cdots<\tau_{u,J}.
\]

由这些 dates 构造 piecewise-linear hat basis \(\phi_{u,j}(t)\)：

\[
\mu_u(t)=\sum_{j=0}^{J}\alpha_{u,j}\phi_{u,j}(t),
\qquad
\sigma_u(t)=\sum_{j=0}^{J}\beta_{u,j}\phi_{u,j}(t),
\qquad \beta_{u,j}>0.
\]

需要拟合的未知量仅为：

\[
\alpha_u=(\alpha_{u,0},\ldots,\alpha_{u,J}),
\qquad
\beta_u=(\beta_{u,0},\ldots,\beta_{u,J}).
\]

Node dates 表示可能改变 drift/diffusion 线性斜率模式的已知事件时点；事件的位置已知，但方向与幅度未知。这正是本任务公开 node locations、隐藏 node heights 的经济含义。

### 3.2 与 generator 一致的区间量

令：

\[
y_{u,k}=\log\frac{S_{u,t_{k+1}}}{S_{u,t_k}}.
\]

对每个观测区间 \([t_k,t_{k+1}]\)，node dates 唯一确定：

\[
A_{u,k,j}
=
\int_{t_k}^{t_{k+1}}\phi_{u,j}(t)\,dt,
\]

以及：

\[
Q_{u,k,j\ell}
=
\int_{t_k}^{t_{k+1}}
\phi_{u,j}(t)\phi_{u,\ell}(t)\,dt.
\]

于是 generator-consistent conditional mean 与 variance 为：

\[
M_{u,k}(\alpha_u,\beta_u)
=
A_{u,k}^{\top}\alpha_u
-\frac12\beta_u^{\top}Q_{u,k}\beta_u,
\]

\[
V_{u,k}(\beta_u)
=
\beta_u^{\top}Q_{u,k}\beta_u.
\]

这里不能把周末、跨 node 区间或 irregular date gaps 一律近似为单点 Euler step；fitter 与 verifier 必须复用或严格匹配 authoring pipeline 的积分约定和 day-count convention。

### 3.3 指定 regression-style loss

使用固定的 heteroskedastic Gaussian regression loss：

\[
\mathcal L_u(\alpha_u,\beta_u)
=
\sum_k
\left[
\log V_{u,k}(\beta_u)
+
\frac{
\left(y_{u,k}-M_{u,k}(\alpha_u,\beta_u)\right)^2
}{V_{u,k}(\beta_u)}
\right].
\]

并施加预先公开、固定的 bounds：

\[
\alpha_{u,j}\in[\mu_{\min},\mu_{\max}],
\qquad
\beta_{u,j}\in[\sigma_{\min},\sigma_{\max}].
\]

Task contract 还必须冻结：

- node ordering；
- time origin 与 day-count convention；
- interpolation/extrapolation convention；
- loss version；
- parameter bounds；
- optimizer；
- initialization；
- stopping rule；
- tie-breaking rule；
- output dtype、rounding 和 serialization precision。

这使 Step 1 成为固定 node grid 上的低维 regression problem，而不是开放式 stochastic-process identification。

### 3.4 Step 1 的 hard-verifier 目标

Verifier 应从 frozen observed path 和公开 task contract 重新计算指定 estimator：

\[
(\widehat\alpha_u,\widehat\beta_u)
=
\arg\min_{\alpha_u,\beta_u}\mathcal L_u.
\]

评分对象应是指定 estimator 的规范化输出，不应要求有限随机路径下的估计结果逐位等于 generator private node values。Private node values 用于 authoring QA、simulation provenance 和 identifiability screening，不直接替代 estimator ground truth。

## 4. Step 2：逐 option 时间序列拟合 BSM 的 \(d_1,d_2\) price terms

### 4.1 以 option contract 为回归单位

对 option contract \(c\)，使用它在多个 valuation dates \(t\in\mathcal T_c\) 上的报价序列。已知：

\[
\left(S_t,K_c,T_c,r,q,\operatorname{type}_c,
P_{c,t}^{\mathrm{obs}}\right).
\]

需要拟合的是该 option 的共享 BSM pricing parameters \(\theta_c\)，例如 constant pricing volatility，或已知 pricing-volatility node dates 上的 node values；不是给每个 row 单独释放一个 \(\sigma_{c,t}\)。

定义：

\[
R_{t,T_c}=\int_t^{T_c}r_s\,ds,
\qquad
Q_{t,T_c}=\int_t^{T_c}q_s\,ds,
\]

\[
V_{c,t}(\theta_c)
=
\int_t^{T_c}\sigma_{c,\mathbb Q}^2(s;\theta_c)\,ds.
\]

若使用 constant volatility，则：

\[
V_{c,t}(\theta_c)=\sigma_c^2(T_c-t).
\]

若 pricing volatility 也由公开 node dates 上的 piecewise-linear node function 给出，则 \(V_{c,t}(\theta_c)\) 必须使用与 generator 相同的精确 squared-volatility interval integration；Agent 只拟合 private node heights 对应的参数估计。

### 4.2 BSM 的两个核心 price terms

定义：

\[
d_{1,c,t}(\theta_c)
=
\frac{
\log(S_t/K_c)+R_{t,T_c}-Q_{t,T_c}
+\tfrac12V_{c,t}(\theta_c)
}{
\sqrt{V_{c,t}(\theta_c)}
},
\]

\[
d_{2,c,t}(\theta_c)
=d_{1,c,t}(\theta_c)-\sqrt{V_{c,t}(\theta_c)}.
\]

对于 call，两个 discounted price terms 为：

\[
X^{S}_{c,t}(\theta_c)
=S_te^{-Q_{t,T_c}}\Phi(d_{1,c,t}),
\qquad
X^{K}_{c,t}(\theta_c)
=K_ce^{-R_{t,T_c}}\Phi(d_{2,c,t}),
\]

且 BSM restriction 是：

\[
C_{c,t}^{\mathrm{BSM}}(\theta_c)
=X^{S}_{c,t}(\theta_c)-X^{K}_{c,t}(\theta_c).
\]

对于 put：

\[
P_{c,t}^{\mathrm{BSM}}(\theta_c)
=K_ce^{-R_{t,T_c}}\Phi(-d_{2,c,t})
-S_te^{-Q_{t,T_c}}\Phi(-d_{1,c,t}).
\]

因此应回归/拟合的是 \(d_1,d_2\) 进入的两个 BSM price terms，而不是把裸 \(d_1,d_2\) 当作线性 predictors。由于这些 terms 本身依赖 \(\theta_c\)，这在参数上通常是一个低维、受约束的非线性 regression problem。

### 4.3 固定 regression-style loss

对每条 option 时间序列，冻结下列目标：

\[
\widehat\theta_c
=
\arg\min_{\theta_c\in\Theta_c}
\sum_{t\in\mathcal T_c}
w_{c,t}\,
\rho\!\left(
\frac{
P_{c,t}^{\mathrm{obs}}
-P_{c,t}^{\mathrm{BSM}}(\theta_c)
}{s_{c,t}}
\right),
\]

其中：

- \(w_{c,t}\) 是公开且固定的 row weight；
- \(s_{c,t}\) 是固定的 quote-noise scale 或 normalization；
- \(\rho\) 是冻结的 squared loss、Huber loss 或其他确定性 regression loss；
- \(\Theta_c\) 给出 pricing-volatility bounds 与必要的 positivity constraints。

拟合后计算：

\[
\widehat P_{c,t}^{\mathrm{BSM}}
=P_{c,t}^{\mathrm{BSM}}(\widehat\theta_c),
\qquad
e_{c,t}
=P_{c,t}^{\mathrm{obs}}-\widehat P_{c,t}^{\mathrm{BSM}}.
\]

通过 task contract 中固定的 standardized-residual threshold、sequence-level fit statistic 与 tie-breaking rule 判断该序列/row 是否符合 BSM quote-noise 机制，或是否是 mutation candidate。

可以把下面的辅助回归作为诊断：

\[
P_{c,t}^{\mathrm{obs}}
=a_c+b_{1,c}X^{S}_{c,t}(\widehat\theta_c)
+b_{2,c}X^{K}_{c,t}(\widehat\theta_c)+\varepsilon_{c,t},
\]

call 的 BSM coefficient restriction 为：

\[
(a_c,b_{1,c},b_{2,c})=(0,1,-1).
\]

但 hard-verifier 的主目标应直接复算受约束 BSM price-term loss；不必把辅助 coefficients 变成额外的自由答案。

### 4.4 Step 2 的确定性合同

必须固定：

- option-series grouping key；
- BSM formula/version 与 call/put convention；
- rate、dividend 和 day-count integration；
- pricing-volatility 参数化、公开 node dates、interpolation/extrapolation；
- quote field（mid、settlement 或其他 authoring field）；
- weights、noise normalization、loss 和 residual threshold；
- parameter bounds、optimizer、initialization、stopping 与 tie-breaking；
- dtype、rounding 与 serialization precision。

如果任务额外要求 IV，可在 Step 2 之后对指定 rows 做 BSM inversion，并显式输出 “finite_iv” 或 “no_finite_iv”。这只是派生诊断，不得反过来要求所有 post-mutation quotes 都位于 IV 可反演区间；否则会错误排除一部分真实 mutation/arbitrage cases。

## 5. Step 3：异常定位与 X/U/T 套利验证

Step 1 和 Step 2 用于重建潜在 dynamics、识别异常结构并定位候选 mutation；最终判定分为两层。

### 5.1 Model-based diagnosis

输出：

- fitted physical drift/diffusion node values；
- per-option BSM pricing-parameter estimates；
- \(d_1,d_2\) price-term fitted values、sequence fit statistic 与 standardized residuals；
- 可选的 per-row implied-volatility diagnostic；
- suspicious row/group/spot；
- inferred mutation direction 与 magnitude；
- counterfactual clean-price estimate；
- X/U/T candidate family。

Model residual 可以定位异常，但不能单独充当 executable arbitrage certificate。

### 5.2 Transaction-cost-aware certificate

对每个候选 X/U/T 组合，使用真实可执行 side prices 构造 legs，并计入：

- bid/ask；
- option fee（当前合同为每张、每边 \$0.50，若配置不同则以 frozen contract 为准）；
- underlying execution cost（当前合同为 5 bps，若配置不同则以 frozen contract 为准）；
- contract multiplier；
- funding；
- dividend/carry；
- expiry 或中间 liquidation 规则。

Verifier 必须重新计算：

\[
\operatorname{InitialCashflow}(\pi)
\]

以及所需状态下的：

\[
\operatorname{TerminalPayoff}(\pi;\omega).
\]

只有满足任务合同中的严格套利条件，例如：

\[
\operatorname{InitialCashflow}(\pi)>0,
\qquad
\operatorname{TerminalPayoff}(\pi;\omega)\ge 0
\quad\forall\omega,
\]

或其等价的负初始净成本表述，才计为 executable arbitrage。

X/U/T signature 仍为：

\[
000,001,010,011,100,101,110,111.
\]

其中：

- `X`：同一 chain 内的 cross-strike/static-shape arbitrage；
- `U`：option 与 underlying/cash/dividend replication 之间的套利，例如 transaction-cost-aware put-call parity；
- `T`：跨 maturity 关系；必须使用 authoring contract 明确的 calendar、intermediate liquidation 与 carry 规则。

## 6. Public/private visibility 必须返修

### 6.1 Agent 可见内容

F2A public task view 至少应提供：

```yaml
underlying_id:
time_origin:
time_axis: calendar_day_offset
day_count: Actual365Fixed
dynamics_form: time_inhomogeneous_gbm
function_type: piecewise_linear_nodes
drift_node_dates: []
volatility_node_dates: []
interpolation: linear
extrapolation: flat
drift_bounds: []
volatility_bounds: []
fit_contract:
  loss_id:
  optimizer:
  initialization:
  stopping_rule:
  rounding:
pricing_contract:
  family: BSM
  option_series_grouping_key:
  quote_field:
  volatility_parameterization:
  pricing_volatility_node_dates: []
  interpolation:
  extrapolation:
  loss_id:
  row_weights:
  noise_normalization:
  residual_threshold:
  optimizer:
  initialization:
  stopping_rule:
  rounding:
  optional_iv_diagnostic: false
```

如果 drift 与 diffusion 共用同一 node grid，可以只公开统一的 `physical_node_dates`，但 schema 与文档必须明确声明共享关系。

### 6.2 Agent 不可见内容

以下内容必须保留在 private oracle/authoring metadata，不能通过 `solver_visible.pricing_metadata` 或任何 public DB view 泄漏：

- drift node values；
- diffusion node values；
- 完整含 values 的 `physical_dynamics`；
- 完整含 values 的 `pricing_dynamics.volatility_function`；
- clean option prices；
- quote-noise realization；
- mutation targets、directions、magnitudes 与 signature oracle；
- sampling seed 与 private sampling hyperparameters；
- private correlation/dependence specification；
- 可直接反推出答案的 generator configuration。

当前若 `schema.py` 将含 `nodes: [{day_offset, value}]` 的 `physical_dynamics` 或 `pricing_dynamics` 放入 `solver_visible.pricing_metadata`，必须拆分为 public node locations 与 private node values。否则 Agent 可以直接读取答案，Step 1/Step 2 都失去意义。

## 7. Node horizon 问题必须修复

当前实现中，F2A parent 约含 65 个 business dates，观测 offset 约为 \(0\) 到 \(88\)；但某些 7-node grids 的 terminal offsets 位于约 \(188\) 到 \(346\)。观测区间之外、从未影响任一 transition 的 node values 不可由发布路径拟合。

优先采用以下方案：

> 为 F2A parent 单独生成 4–7 个 event nodes，并确保所有需要评分的 node basis 在 observed horizon 内获得充分支持。

实现要求：

- node dates 仍由 node-based generator 产生；
- 事件间隔可以 irregular，但必须满足最小支持/最小观测数约束；
- drift/diffusion node values 仍保持 private；
- 若保留 horizon 外 nodes，则只能作为 private future-generation nodes，不能纳入 Agent 输出或 Step 1 评分；
- bootstrap sample 进入任务前执行 design/support screening。

可接受但次优的替代方案是延长 underlying history，覆盖最后一个 scored node。不要要求 Agent 拟合从未进入 observed transitions 的 node value。

## 8. Authoring gates

每个 frozen parent/bootstrap sample 在发布前必须通过：

1. **Base-chain gate**：quote noise 后、mutation 前的 base chain 的 executable signature 必须严格为 `000`；
2. **Node-support gate**：每个 scored node 都对 observed transitions 有充分且非退化的 basis support；
3. **Fit gate**：指定 optimizer 成功收敛，输出有限，无非法 volatility，且没有未处理的 tie/multiple-solution status；
4. **Option-series support gate**：每条需要拟合的 option 都有足够多的 valuation-date observations，且公开的 pricing-volatility parameterization 对 observed sequence 具有非退化支持；
5. **BSM regression gate**：指定 \(d_1,d_2\) price-term regression 在 pre-mutation base quotes 上确定性收敛，pricing parameters 有限、满足 bounds，且 residual statistic 符合 frozen quote-noise criterion；
6. **Mutation gate**：mutation 后 signature 精确等于目标 X/U/T signature，并能由固定 residual rule 定位到目标 rows/groups；
7. **Execution gate**：在 bid/ask、fees、funding、dividend 和 multiplier 下仍存在目标 certificate；
8. **Exclusivity gate**：全量扫描确认不存在目标 signature 之外的额外套利；
9. **Leakage gate**：public tables、metadata、logs 和 task bundle 均不含 physical/pricing node values、clean prices 或 mutation oracle。

不要设置“所有 post-mutation quotes 都必须存在有限 IV”的发布 gate。IV 可逆性不是 BSM regression 的必要条件，而且某些用于制造套利的 mutation 合理地可能落到有限 IV 区间之外。

## 9. Hard verifier 分层

| Verifier | 复算内容 | Ground truth 来源 |
|---|---|---|
| `V1_underlying_fit` | 每条 underlying 的 \(\widehat\alpha_u,\widehat\beta_u\) | frozen path + public node dates + fixed loss/solver |
| `V2_option_fit` | 每条 option 的 BSM pricing parameters、\(d_1,d_2\) price terms、fitted prices、residuals 与 fit status | published option time series + fixed BSM regression contract |
| `V3_mutation` | affected rows/groups、方向、clean-price字段 | private mutation oracle +规范化输出 |
| `V4_arbitrage` | X/U/T signature、legs、现金流与 payoff certificate | frozen executable DB + private authoring oracle |
| `V5_scan` | 不存在其他套利的 scan summary | trusted full enumeration |

所有浮点结果先按照 task contract 指定的 dtype 与 precision 规范化，再进行 exact hard comparison；不要在 ORM 层引入未定义的 tolerance。

## 10. 建议的 Agent 输出 schema

```yaml
underlying_fits:
  - underlying_id:
    node_dates: []
    drift_node_values: []
    diffusion_node_values: []
    objective_value:
    solver_status:

option_fits:
  - option_contract_id:
    valuation_row_ids: []
    pricing_family: BSM
    pricing_parameterization:
    pricing_parameters: []
    fitted_prices: []
    d1_values: []
    d2_values: []
    standardized_residuals: []
    objective_value:
    fit_status:
    optional_iv_diagnostics: []

mutation_diagnosis:
  - mutation_group_id:
    affected_row_ids: []
    observed_prices: []
    recovered_clean_prices: []
    mutation_type:
    mutation_direction:
    mutation_size:

arbitrage:
  signature: "XUT"
  opportunities:
    - family:
      row_ids: []
      legs:
        - instrument_id:
          side:
          quantity:
          execution_price:
      gross_initial_cashflow:
      option_fees:
      underlying_costs:
      funding_and_dividend:
      net_initial_cashflow:
      terminal_payoff_certificate:
  full_scan_summary:
```

## 11. Codex 的具体返修清单

- [ ] 不再把 Step 1 写成未知-knot/changepoint/model-family selection；
- [ ] 将 Step 1 改成“公开 event-node dates，拟合 node values”；
- [ ] 使用与 generator 的 exact interval integration 一致的 \(M_k,V_k\)；
- [ ] 固定 regression loss、solver、bounds、dtype、rounding 与 tie-breaking；
- [ ] 将 Step 2 改为逐 option 时间序列的 BSM \(d_1,d_2\) price-term regression，而非逐 row IV inversion，也不强迫整张 surface 共享常数 volatility；
- [ ] 冻结 option-series grouping、pricing-volatility parameterization、loss、noise normalization、residual rule、solver 与 precision；
- [ ] 保留 quote noise 与 mutation 的区分；
- [ ] 拆分 public node locations 与 private node values，修复 `solver_visible` 泄漏；
- [ ] 让所有 scored nodes 落入 observed horizon 并有充分支持，或只评分 active supported nodes；
- [ ] 保留 Step 3 的 model-based diagnosis；
- [ ] 最终套利 ground truth 仍由 transaction-cost-aware X/U/T certificate 决定；
- [ ] 加入 base `000`、underlying-node support、option-series support、BSM regression、target signature、exclusivity 与 leakage gates；
- [ ] 删除“所有 post-mutation quotes 必须有有限 IV”的错误发布限制；IV 只作为可选派生诊断；
- [ ] 更新相关 tests，证明 public bundle 无法访问 node values/clean prices/mutation oracle；
- [ ] 更新相关 Markdown，确保数学公式、schema、实现与 verifier contract 一致。

## 12. 验收标准

返修完成后，应能从任一 F2A task bundle 明确回答：

1. Agent 知道哪些 node dates，但不知道哪些 node values？
2. 每个 scored node 是否真正影响了 observed returns？
3. Fitter 是否严格复用了 generator 的 time axis、day count、interpolation 与区间积分？
4. 每条 option 时间序列是否按照固定的 BSM \(d_1,d_2\) price-term regression contract 得到唯一规范化 estimator output 与 residual diagnosis？
5. Base DB 在 mutation 前是否严格为 `000`？
6. Mutation 后是否只产生指定 X/U/T signature？
7. Model diagnosis 与 executable arbitrage certificate 是否被明确区分？
8. Trusted verifier 是否能只依赖 frozen DB、public contract 与 private oracle 完整复算？
9. Public schema 是否不存在任何答案泄漏？

只要以上九项均能通过，F2A 就形成了完整且闭合的 agent task：既保留真实的 underlying/option fitting 难度，也保持 deterministic hard verification 和精确的 X/U/T arbitrage ground truth。
