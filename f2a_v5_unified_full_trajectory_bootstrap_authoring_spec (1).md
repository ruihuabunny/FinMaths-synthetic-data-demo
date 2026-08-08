# F2A v5 Model-Based X/U/T Signal + Full-Trajectory Bootstrap Authoring 统一实施规范

**统一修订日期：** 2026-08-08  
**目标分支：** f2a-2nd-revised  
**当前目标：** 从 frozen DuckDB 生成 8-underlying F2A agent tasks，并把 node-based physical fitting、BSM \(d_1,d_2\) price-term validation、fitted counterfactual、mutation localisation 与 model-based X/U/T signal classification 闭合为同一条可复现、可 hard-verify 的 trajectory。

**本次最终修订：** v5 的 scored X/U/T label 改为 public child DB 上的 canonical fitted-counterfactual signal；v4 的 transaction-cost-aware executable-arbitrage oracle 保留为独立 baseline/audit。65-business-day pilot 默认使用 3 个 shared drift/diffusion nodes，并新增 diffusion-estimation scaling、delta-method uncertainty、Monte Carlo/parametric-bootstrap FP/FN 报告与 cohort-level publication gates。

当前工作只覆盖 dataset authoring、task packaging、reference solver 与 hard verifier；暂不处理模型训练，不设计 train/validation/test split，也不借此重新研究或重写已经通过审计的 X/U/T 数学。

本文件整合并取代以下三份工作文档：

- f2a_v5_full_trajectory_verifier_rework_handoff(1).md；
- f2a_node_based_dynamics_bsm_d1d2_regression_rework_handoff(1).md；
- f2a_agent_task_bootstrap_authoring_next_plan.md。

若旧文档与本文件冲突，以本文件为准。

---

## 0. 统一后的关键结论

### 0.1 版本边界

F2A v4 与 v5 必须并存，不得把 v5 需求静默写入 v4。

| 版本 | 任务范围 | 处理原则 |
|---|---|---|
| v4 | 单 valuation-date market slice 的 X/U/T executable-arbitrage scan | 保持 status = EXECUTABLE、runtime_enabled = true；冻结 schema、fixtures、candidate IDs 与现金流数学 |
| v5 | 8-underlying、multi-date、full-trajectory model-based X/U/T signal task | 使用独立 variant ID、config、schema、fixtures、solver 与 verifier orchestration；允许相对 private truth 存在受控 FP/FN |

建议 v5 variant 名称：

~~~text
bsm_model_reconstruction_xut_signal_f2a_v5
~~~

### 0.2 v5 最终 trajectory

\[
\boxed{
\text{node-based underlying fitting}
\rightarrow
\text{BSM }d_1,d_2\text{ counterfactual construction}
\rightarrow
\text{mutation localisation}
\rightarrow
\text{model-based X/U/T signal classification}
}
\]

其中：

1. Agent 已知 event-node dates，只拟合 node values，不做 changepoint detection；
2. 65-business-day pilot 默认每条 underlying 使用 3 个公开 event-node dates；只有通过 information/support gate 才能增加 nodes；
3. 在当前 linked-diffusion variant 中，Girsanov 只改变 drift，Stage-1 fitted diffusion 直接进入 risk-neutral integrated variance；不再另拟合一套会切断误差传播的 per-contract volatility；
4. BSM validation 以固定 option contract 的多 valuation-date 时间序列为单位；
5. \(d_1,d_2\) 对应的 BSM price-term coefficients 由公式固定，不能自由回归；
6. per-row implied-volatility inversion 只能是可选诊断，不是 Stage 2 的核心 estimator；
7. fitted BSM counterfactual 与 residual 可以定义 model-based X/U/T signal，但不能被表述为 model-free executable-arbitrage proof；
8. bid/ask、fees、underlying costs、funding、dividends/carry 与 multiplier 必须进入 signal edge；v4 cash-flow certificate 继续单独报告 execution audit；
9. hard verifier 验证 public child 上完全指定的 canonical estimate 与 signal，不要求有限随机路径精确恢复 private generator/mutation truth；
10. private truth 只用于 authoring calibration，必须报告 FP/FN、signature confusion 与 uncertainty，而不是直接覆盖 Agent ground truth。

### 0.3 对旧 bootstrap 计划的必要修正

旧计划假设现有 22-underlying × 65-trading-day frozen DuckDB 可以直接用于只扫描 X/U/T 的任务。该假设对 v4 pilot 仍成立，但对 v5 不再无条件成立。

现有 frozen parent 只有同时通过以下条件时才能直接复用为 v5 parent：

- 所有 scored physical nodes 在 public observation horizon 内有充分 support；
- 每条被评分的 option contract 有稳定、足够长的多日期序列；
- public data 包含 Stage 1/2 所需的 rate、dividend/carry、time-axis 与 fitting contract；
- public view 不泄漏 physical/pricing node values、clean quotes、seeds 或 mutation lineage；
- quote field 在 mutation 后保持自洽；
- canonical Stage-1/2 solvers 可确定性收敛并通过 identifiability gates。

若任一条件不满足，必须生成独立且版本化的 F2A v5 parent。不得为了坚持“本阶段不重新生成 QuantLib parent”而发布不可识别或有答案泄漏的 v5 task。

因此：

- v4 Stage-3-only bootstrap pilot：可以继续复用当前 frozen parent，不重定价；
- v5 full-trajectory pilot：优先复用，但必须 gate；gate 失败时允许并要求生成 v5-specific parent；
- 无论走哪条路径，v4 的现有行为和测试都不得被改变。

### 0.4 旧文档冲突的最终裁决

| 旧表述 | 最终统一规则 |
|---|---|
| 当前阶段绝不重新生成 QuantLib parent | 只对 v4 Stage-3-only pilot 成立；v5 parent qualification 失败时必须生成独立 parent |
| Public task 只提交 slice/signature/candidate IDs | 这是 v4 contract；v5 还必须提交 Stage-1/2 与 mutation diagnosis 的规范化输出 |
| Canonical answer 只来自 mutation 后 executable full rescan | 对 v4 成立；v5 canonical answer 来自 public child 上的 Stage-1 fit、BSM counterfactual 与 model-signal full rescan |
| Verifier 分为 V1–V5，并用 private mutation oracle 直接评分 localisation | 统一为 semantic \(V_1/V_2/V_3\)；private lineage 只作 publication QA，localisation 的 scored result 来自 public deterministic estimator |
| 每条 quote 独立 IV inversion | 正式弃用为 core estimator；只保留 optional diagnostic |
| 自由回归 BSM 两个 price-term coefficients | 禁止；BSM formula 固定 intercept 与 coefficients，当前 variant 的 volatility input 来自 Stage-1 linked diffusion |
| Model residual 绝不能定义套利 | 改为：residual/fitted counterfactual 可以定义 **model-based X/U/T signal**；只有 v4 cash-flow certificate 才能声称 model-free/executable arbitrage |
| Public fitted signature 必须等于 private mutation signature | 不要求逐 task 相等；二者差异定义 FP/FN，并由 cohort-level calibration gate 控制 |

---

## 1. 必须保护的现有数学与实现

以下内容是 v4 已完成的 executable-arbitrage 数学核心，只允许修复可复现 bug、补 validation 或 tests，不得借 v5 重写。v5 将其作为独立 execution audit 与 realism diagnostic，不再把它当 scored signal label：

- X：single-expiry shape arbitrage；
- U：transaction-cost-aware put-call consistency arbitrage；
- T：two-expiry call-stock-flip calendar arbitrage；
- bid/ask execution side；
- option fee；
- stock bid/ask transaction cost；
- funding/dividend segment；
- contract multiplier；
- \(s_j\)、\(g_j\) 与 terminal non-negativity certificate；
- canonical candidate enumeration 与 candidate IDs；
- 001、mixed signatures 与 111 的 reachability；
- authoring scan 与 trusted oracle scan 的一致性门；
- 独立 reference solver。

特别要求：

- Calendar family 保持启用，不得重新 block、删除或降级为 documentation-only；
- 不得重新引入 arbitrary position grid 或巨量组合系数搜索；
- regression fitting 不得混入 f2a_oracle.py；
- v5 应增加 model-signal scanner；不得替换或篡改现有现金流数学。

保护路径：

| 路径 | 处理方式 |
|---|---|
| src/synthetic_derivatives/verifier/f2a_contract.py | 保留 execution primitives；仅在 v5 明确需要时扩展 typed contract |
| src/synthetic_derivatives/verifier/f2a_oracle.py | 保留完整 executable X/U/T catalogue scan，作为 v4 oracle 与 v5 execution audit |
| 现有 X/U/T fixtures 与 tests | 继续作为 v4 regression tests；v5 另建 model-signal fixtures，不改变原期望值 |

---

## 2. Generator 与任务的既定事实

### 2.1 Physical underlying dynamics

每条 underlying 的 physical-measure drift 与 diffusion：

- 由有限个 event nodes 定义；
- node date 或相对 time_origin 的 day_offset 已知；
- node value 未知；
- nodes 之间 linear interpolation；
- node support 外 flat extrapolation；
- close-to-close interval 可能跨 weekend 或 event node；
- transition 使用与 drift 和 squared diffusion 的精确区间积分一致的 time-inhomogeneous GBM；
- 多资产共享 interest-rate path，并可通过合法的 symmetric positive-semidefinite correlation structure 关联 Brownian residuals。

当前 65-business-day pilot 的默认 contract：

- drift 与 diffusion 使用同一组 **3 个** public node dates；
- 3 nodes 是默认上限而不是统计定律；实际日期可由 event calendar 决定；
- 所有 3 个 scored basis functions 必须在 public horizon 内有非退化 support；
- 若增加第 4 个及以上 node，必须用 Fisher-information/conditioning 与 empirical FP/FN 证明收益大于方差代价；
- 若 event-node 密度随 horizon 同比例增加，则不能声称 node-wise error 会随总 horizon 自动下降。

因此 Stage 1 不是：

- changepoint detection；
- unknown-knot search；
- arbitrary-function estimation；
- GBM/CEV/Heston 等开放式 physical-model selection。

### 2.2 Option quotes

- vanilla option pricing family 固定为 BSM；
- base quotes 来自 BSM prices 加受控 quote noise；
- arbitrage mutation 在 frozen、通过 base gates 的 quotes 上施加；
- option fitting 的单位是固定 contract 的多 valuation-date 序列；
- 不要求整张 surface 共享一个 constant volatility；
- 当前 v5 variant 使用 linked deterministic diffusion：\(\sigma^{\mathbb Q}_u(t)=\sigma^{\mathbb P}_u(t)\)，因为 Girsanov 改变 drift 而不改变 diffusion；
- Stage 2 使用 \(\widehat\beta_u\) 构造 BSM fitted counterfactual，不再为每个 contract 另拟合独立 volatility；
- 若未来要允许 volatility risk premium 或独立 \(\mathbb Q\)-volatility nodes，必须另建 variant，并把下面所有 \(\Sigma_{\widehat\beta}\) 换成该 pricing estimator 的完整 covariance；
- mutation 先制造 private-truth target signature；public canonical fitted signature 可以因估计误差出现少量 FP/FN，不能用 requested signature 覆盖 public rescan。

---

## 3. Stage 1：已知 event-node dates 的 underlying regression

### 3.1 Public parameterisation

对 underlying \(u\)，公开有序 node dates：

\[
\tau_{u,0}<\tau_{u,1}<\cdots<\tau_{u,J}.
\]

令 \(\phi_{u,j}(t)\) 为由 node grid 唯一确定的 piecewise-linear hat basis：

\[
\mu_u(t)=\sum_{j=0}^{J}\alpha_{u,j}\phi_{u,j}(t),
\qquad
\sigma_u(t)=\sum_{j=0}^{J}\beta_{u,j}\phi_{u,j}(t),
\qquad
\beta_{u,j}>0.
\]

Agent 只估计：

\[
\alpha_u=(\alpha_{u,0},\ldots,\alpha_{u,J}),
\qquad
\beta_u=(\beta_{u,0},\ldots,\beta_{u,J}).
\]

Node dates 表示已知 event locations；事件造成的方向、幅度与相邻区间斜率由隐藏 node heights 决定。

### 3.2 Generator-consistent interval quantities

对观测 interval \([t_k,t_{k+1}]\)，定义：

\[
y_{u,k}=\log\frac{S_{u,t_{k+1}}}{S_{u,t_k}},
\]

\[
A_{u,k,j}
=
\int_{t_k}^{t_{k+1}}\phi_{u,j}(t)\,dt,
\]

\[
Q_{u,k,j\ell}
=
\int_{t_k}^{t_{k+1}}
\phi_{u,j}(t)\phi_{u,\ell}(t)\,dt.
\]

则 exact conditional mean 与 variance 为：

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

不得把 weekend、irregular gap 或跨 node interval 一律近似成单点 Euler step。Fitter、reference solver 与 verifier 必须严格复用 authoring generator 的：

- time origin；
- time axis；
- day-count convention；
- interpolation；
- extrapolation；
- interval integration convention。

### 3.3 Canonical loss

默认使用固定的 heteroskedastic Gaussian regression loss：

\[
\mathcal L_u(\alpha_u,\beta_u)
=
\sum_k
\left[
\log V_{u,k}(\beta_u)
+
\frac{
\left(y_{u,k}-M_{u,k}(\alpha_u,\beta_u)\right)^2
}{
V_{u,k}(\beta_u)
}
\right].
\]

并施加公开 bounds：

\[
\alpha_{u,j}\in[\mu_{\min},\mu_{\max}],
\qquad
\beta_{u,j}\in[\sigma_{\min},\sigma_{\max}].
\]

若实际实现使用不同但 generator-consistent 的 regression loss，必须给出新的 loss ID/version；不能在同一 task identity 下静默更换。

### 3.4 必须冻结的 estimator contract

- node ordering；
- time origin、time axis 与 day count；
- interpolation 与 flat-extrapolation rule；
- loss ID/version 与 row weights；
- drift/diffusion bounds；
- input dtype；
- optimizer 及版本化 contract；
- initialization；
- stopping/convergence rule；
- multiple minima、bound solution 与 tie-breaking rule；
- output dtype；
- decimal quantization；
- serialization order。

### 3.5 Stage-1 ground truth

Verifier 从 public path 和 public fitting contract 复算：

\[
(\widehat\alpha_u,\widehat\beta_u)
=
\arg\min_{\alpha_u,\beta_u}\mathcal L_u.
\]

Scored ground truth 是该 canonical estimator 的规范化输出，不是 private generator node values。Private values 仅用于：

- simulation provenance；
- authoring QA；
- recovery diagnostics；
- node-support 与 identifiability screening。

因此必须同时保存两个不混淆的对象：

\[
\widehat\beta_u^{\mathrm{public}}
=
\operatorname{Fit}(S_{u,0:n};\text{public contract}),
\qquad
\beta_u^\star
=
\text{private generator value}.
\]

前者进入 Agent 的 fitted counterfactual 与 hard verifier；后者只进入 uncertainty/FP/FN audit。

### 3.6 Diffusion covariance 与 horizon/node scaling

令 \(p=J+1\) 为 diffusion-node 数，\(n\) 为可用 return intervals。对 Gaussian interval likelihood，忽略 \(O(\Delta t)\) 的 mean contribution 时，diffusion block 的 Fisher information 近似为：

\[
I_{\beta\beta}
\approx
2\sum_{k=1}^{n}
\frac{
(Q_k\beta)(Q_k\beta)^\top
}{
(\beta^\top Q_k\beta)^2
},
\qquad
\Sigma_{\widehat\beta}
\approx
I_{\beta\beta}^{-1}.
\]

实现中应使用 full observed Hessian 或把 drift block profile 掉后的 Schur complement，而不是把上式的近似常数硬编码进 verifier。上式用于 authoring scaling 与 sanity check。

在 node 数 \(p\) 固定、grid shape 固定、每个 basis support 随 horizon 同比例增长时：

\[
\boxed{
\operatorname{SE}(\widehat\beta_j)
=O(n^{-1/2})
=O(T^{-1/2})
}
\]

对 quasi-uniform grid，更有粗略关系：

\[
\boxed{
\frac{\operatorname{SE}(\widehat\beta_j)}{\beta_j}
=
O\!\left(\sqrt{\frac{p}{n}}\right)
}
\]

但常数取决于 node location、boundary flat extrapolation、irregular intervals 与 basis collinearity。若 horizon 增长时以固定 spacing 持续增加 nodes，即 \(p\propto n\)，node-wise relative SE 一般不会下降；若只在末端追加 observations，则主要只改善有末端 support 的 nodes。

可以报告 constant-vol-equivalent information sample size：

\[
n_{\mathrm{eff},j}^{I}
=
\frac{1}{2\,\operatorname{RSE}(\widehat\beta_j)^2}.
\]

它把 actual-grid covariance 映射到 constant-volatility 的 \(1/\sqrt{2n}\) benchmark，适合跨 node grids 比较，但不能替代完整 covariance。

### 3.7 65-day、3-node 的推荐量级

在以下 benchmark 下：

- daily returns；
- 3 个等距 nodes 覆盖整个 observation horizon；
- exact Gaussian interval likelihood；
- constant reference volatility \(\sigma=20\%\)；
- 不跨 underlying pooling；
- nodes 固定，horizon 增长时按比例铺开；

3-node grid 的中心 node 与 boundary node 近似满足：

\[
\boxed{
\operatorname{RSE}_{\mathrm{center}}
\approx
\frac{1.414}{\sqrt n}
\approx
\frac{0.0891}{\sqrt{T_{\mathrm{years}}}}
}
\]

\[
\boxed{
\operatorname{RSE}_{\mathrm{boundary}}
\approx
\frac{1.871}{\sqrt n}
\approx
\frac{0.1179}{\sqrt{T_{\mathrm{years}}}}
}
\]

其中 \(n\approx252T_{\mathrm{years}}\)。数值量级如下：

| Usable returns \(n\) | Horizon | 3-node center RSE | 3-node boundary RSE |
|---:|---:|---:|---:|
| 65 | 约 3 个月 | 17.5% | 23.2% |
| 126 | 约半年 | 12.6% | 16.7% |
| 252 | 1 年 | 8.9% | 11.8% |
| 504 | 2 年 | 6.3% | 8.3% |
| 756 | 3 年 | 5.1% | 6.8% |
| 1260 | 5 年 | 4.0% | 5.3% |

若数据库有 65 个 price dates，则通常只有 64 个 close-to-close returns；与表中 65-return benchmark 的差异不足 1%，但代码和报告必须使用 actual usable intervals。

与旧 7-node 方案相比，65-return benchmark 的 typical/boundary RSE 约从 \(29.3\%/40.1\%\) 降到 3-node 的 \(17.5\%/23.2\%\)。因此当前 pilot 固定 3 nodes 是更合理的 bias-variance trade-off。实际 event dates 不等距时，必须根据 actual \(A_k,Q_k\) 重算，而不能直接复制本表。

### 3.8 Drift 的边界

Drift nodes 仍由 Stage 1 拟合并 hard-verify，但在当前 BSM linked-diffusion contract 中：

\[
\frac{\partial P^{\mathrm{BSM}}}{\partial\widehat\alpha}=0.
\]

因此 drift estimation error 不进入 option counterfactual 或 X/U/T signal。不得为了使用 drift estimate 而把 physical drift 塞进 risk-neutral pricing；只有另行公开并冻结 market-price-of-risk mapping 的新 variant 才能这么做。

---

## 4. Stage 2：linked diffusion 下的 BSM \(d_1,d_2\) validation 与 fitted counterfactual

### 4.1 Fitting unit

以固定 option contract \(c\) 在多个 valuation dates \(t\in\mathcal T_c\) 上的序列为单位。Stable grouping key 至少包含：

~~~text
(underlying_id, option_type, strike, expiry, settlement_style, multiplier)
~~~

若 option_id 在 frozen DB 中不是全局唯一，任何 row、contract 或 candidate identity 都必须包含足够的 composite key。

每个 observation 至少公开：

\[
\left(
S_t,K_c,T_c,r/q\text{ curve inputs},
\operatorname{type}_c,
P_{c,t}^{\mathrm{obs}}
\right).
\]

Task 必须使用 mutation-independent、公开且 deterministic 的 eligible-series selection rule。不能只发布或只评分 private target contracts。Stage 2 的目的不是从每条 mutation 后的 option series 再反演一套 volatility，而是用 Stage-1 \(\widehat\beta_u\) 构造共同、可解释的 counterfactual。

### 4.2 Integrated inputs

定义：

\[
R_{t,T_c}=\int_t^{T_c}r_s\,ds,
\qquad
Q_{t,T_c}=\int_t^{T_c}q_s\,ds,
\]

\[
W_{u;t,T_c}(\widehat\beta_u)
=
\int_t^{T_c}\sigma_{u,\mathbb Q}^2(s;\widehat\beta_u)\,ds
=
\widehat\beta_u^\top Q_{u;t,T_c}\widehat\beta_u.
\]

这里使用当前 variant 的 linked-diffusion assumption：

\[
\sigma_{u,\mathbb Q}(s)=\sigma_{u,\mathbb P}(s).
\]

Constant volatility 是 \(p=1\) 的特例：

\[
W_{u;t,T_c}=\widehat\sigma_u^2(T_c-t).
\]

Piecewise-linear diffusion 的 squared-volatility integration 必须与 generator 完全一致，包括 option maturity 落在 observed path horizon 之外时的 flat extrapolation。若 maturity 大量落在最后一个 node 之外，counterfactual uncertainty 会集中在 terminal node，必须由 propagation report 明示。

### 4.3 \(d_1,d_2\) 与 call price terms

\[
d_{1,c,t}(\widehat\beta_u)
=
\frac{
\log(S_t/K_c)+R_{t,T_c}-Q_{t,T_c}
+\tfrac12W_{u;t,T_c}(\widehat\beta_u)
}{
\sqrt{W_{u;t,T_c}(\widehat\beta_u)}
},
\]

\[
d_{2,c,t}(\widehat\beta_u)
=
d_{1,c,t}(\widehat\beta_u)-\sqrt{W_{u;t,T_c}(\widehat\beta_u)}.
\]

对 call：

\[
X^S_{c,t}(\widehat\beta_u)
=
S_te^{-Q_{t,T_c}}\Phi(d_{1,c,t}),
\]

\[
X^K_{c,t}(\widehat\beta_u)
=
K_ce^{-R_{t,T_c}}\Phi(d_{2,c,t}),
\]

\[
C_{c,t}^{\mathrm{BSM}}(\widehat\beta_u)
=
X^S_{c,t}(\widehat\beta_u)-X^K_{c,t}(\widehat\beta_u).
\]

BSM restriction 等价于：

\[
\text{intercept}=0,
\qquad
(\gamma_1,\gamma_2)=(1,-1).
\]

不得自由拟合两个 arbitrary coefficients 再将其称为 BSM identification。

### 4.4 Put formula

对 put：

\[
P_{c,t}^{\mathrm{BSM}}(\widehat\beta_u)
=
K_ce^{-R_{t,T_c}}\Phi(-d_{2,c,t})
-S_te^{-Q_{t,T_c}}\Phi(-d_{1,c,t}).
\]

必须直接使用 option-type-specific formula，不能从一条可能已经 mutation 的 call quote 推导 put。

### 4.5 Canonical constrained price-term validation

Stage 2 不再定义独立的 \(\widehat\theta_c\)。对每个 eligible contract series，直接计算：

\[
\widehat P_{c,t}^{\mathrm{cf}}
=
P_{c,t}^{\mathrm{BSM}}(\widehat\beta_u),
\qquad
e_{c,t}
=
P_{c,t}^{\mathrm{obs}}-\widehat P_{c,t}^{\mathrm{cf}}.
\]

Series-level BSM validation statistic 为：

\[
\mathcal L_c^{\mathrm{BSM}}
=
\sum_{t\in\mathcal T_c}
w_{c,t}\,
\rho\!\left(
\frac{e_{c,t}}{s_{c,t}}
\right).
\]

其中：

- \(w_{c,t}\) 为公开且固定的 row weight；
- \(s_{c,t}\) 为固定 quote-noise scale 或 normalization；
- \(\rho\) 为冻结的 squared、Huber 或其他 deterministic regression loss；
- \(\widehat\beta_u\) 必须来自 Stage 1 的 canonical public estimator；
- Stage 2 不得根据 option residual 重新调整 \(\widehat\beta_u\)，否则 mutation 会被 counterfactual 吸收并改变已校准的 FP/FN contract。

这仍然是 BSM \(d_1,d_2\) price-term constrained regression/validation：intercept 与两个 term coefficients 由 BSM 固定，随机误差来自 public-path diffusion fit 与 quote noise。它不是把裸 \(d_1,d_2\) 当作自由线性 predictors。

### 4.6 Optional coefficient diagnostic

可以额外报告：

\[
P_{c,t}^{\mathrm{obs}}
=
a_c+b_{1,c}X^S_{c,t}(\widehat\beta_u)
+b_{2,c}X^K_{c,t}(\widehat\beta_u)
+\varepsilon_{c,t},
\]

并检查 call 的 BSM restriction：

\[
(a_c,b_{1,c},b_{2,c})=(0,1,-1).
\]

这只能作为模型诊断。Hard verifier 的核心仍是固定 BSM formula、Stage-1-linked diffusion、constrained price residual 与 fitted counterfactual；不需要把辅助 coefficients 变成自由定价参数。

### 4.7 必须冻结的 Stage-2 contract

- option-series grouping key；
- eligible-series selection rule；
- BSM formula/version；
- call/put convention；
- rate、dividend/carry 与 day-count integration；
- linked-diffusion variant ID；
- Stage-1 diffusion reference/key；
- option-maturity integrated-variance rule；
- interpolation/extrapolation；
- quote observation field；
- row weights；
- noise normalization；
- robust loss 与 residual threshold；
- BSM validation statistic 与 failure rule；
- dtype、rounding 与 serialization precision。

### 4.8 IV 的最终定位

Independent per-row IV inversion：

- 可以作为题目显式要求的派生诊断；
- 应输出 finite_iv 或 no_finite_iv；
- 不能替代 contract-level BSM regression；
- 不能作为所有 post-mutation quotes 的 publication gate；
- 不能因为某条 mutation quote 超出 finite-IV 区间就自动排除该 mutation。

给每个 row 独立释放一个 volatility 会吸收几乎所有处于静态价格界内的 quote，因此无法识别整条报价序列是否符合 linked generator 的共享 diffusion 结构，也会让 Stage-1 diffusion estimation error 无法传播到 model signal。

---

## 5. Stage 2.5：mutation localisation

对每条 option series 定义 residual：

\[
e_{c,t}
=
P_{c,t}^{\mathrm{obs}}
-\widehat P_{c,t}^{\mathrm{cf}}.
\]

使用 frozen quote-noise scale、standardization、threshold、grouping 和 tie-breaking rule，生成：

~~~yaml
mutation_groups:
  - mutation_group_id:
    affected_row_ids:
    affected_dates:
    observed_quotes:
    fitted_clean_counterfactual_quotes:
    residuals:
    localisation_score:
    proposed_mutation_family:
    inferred_direction:
    estimated_mutation_magnitude:
~~~

统一语义：

- Canonical localisation 必须由 public child data 和固定 estimator 复算；
- private mutation lineage 用于 authoring QA，不应直接替代 solver-visible diagnosis；
- fitted clean counterfactual quote 不是泄漏的 private clean quote；
- model residual 与 fitted counterfactual 是 Stage-3 model-signal input；它们不能覆盖或伪装成独立的 v4 executable cash-flow audit；
- task 若不要求恢复 private operator identity，应评分 deterministic diagnosis，而不是强迫 Agent 猜 authoring 内部枚举 ID。

---

## 6. Stage 3：model-based X/U/T signal classification

### 6.1 Signature

\[
(X,U,T)\in\{0,1\}^3,
\]

合法 signatures：

~~~text
000, 001, 010, 011, 100, 101, 110, 111
~~~

其中：

- X：single-expiry cross-strike/static-shape **relative-value signal**；
- U：option 与 underlying/cash/dividend fitted-consistency **signal**；
- T：跨 maturity calendar/call-stock-flip fitted-consistency **signal**。

这些名称保留 v4 catalogue 的 X/U/T 经济结构与 candidate IDs，但 v5 bit 表示 canonical fitted model 发现的 post-cost signal，不表示对所有 terminal states 的 model-free arbitrage theorem。

### 6.2 Candidate signal edge

对 family \(f\in\{X,U,T\}\) 的 directional candidate \(i\)，令 \(L_i\) 为冻结的 portfolio value functional，\(s_i\in\{-1,+1\}\) 为枚举中固定的方向，\(c_i\) 为 bid/ask、option fees、underlying execution cost、funding、dividend/carry 与 multiplier 形成的总 hurdle。定义：

\[
\widehat a_i
=
s_i\Bigl[
L_i(P^{\mathrm{obs}})-L_i(\widehat P^{\mathrm{cf}})
\Bigr]-c_i.
\]

当 \(L_i\) 对 option values 为线性时，也可写成：

\[
\widehat a_i=s_iL_i(e)-c_i.
\]

同一经济关系的两个方向必须作为两个 stable directional candidate IDs 枚举，不能看完 residual 后临时翻转方向。Family bit 定义为：

\[
\widehat b_f
=
\mathbf 1\!\left\{
\max_{i\in\mathcal C_f}\widehat a_i>0
\right\},
\qquad
\widehat b=(\widehat b_X,\widehat b_U,\widehat b_T).
\]

严格不等式、tick rounding、tie-breaking 与 candidate ordering 必须公开并冻结。当前合同中的 option fee 为每张、每边 \$0.50，underlying execution cost 为 5 bps；若 versioned config 不同，以 task contract 为准。

每个 active signal witness 至少报告：

- family、candidate ID、row IDs、legs 与 direction；
- observed executable leg values；
- fitted counterfactual leg values；
- gross model edge；
- spread、fees、underlying costs、funding/carry；
- net signal edge \(\widehat a_i\)；
- parameter-induced standard error \(s_i^{\mathrm{param}}\) 与 standardized margin；
- canonical active/inactive verdict。

### 6.3 与 v4 executable certificate 的边界

v4 oracle 继续独立计算：

\[
\operatorname{InitialCashflow}(\pi)>0,
\qquad
\operatorname{TerminalPayoff}(\pi;\omega)\ge0
\quad\forall\omega.
\]

v5 可以把该结果附在 `execution_audit` 中，用于回答“这个 fitted signal 是否同时构成 executable static arbitrage”。但是：

- v5 scored signature 来自 \(\widehat a_i\)，不是 executable certificate；
- `execution_audit.signature` 可以与 `model_signal.signature` 不同；
- 不得因两者不同而改写 public canonical answer；
- 文档、schema 与 Agent prompt 必须使用 “model-based signal”，不能省略限定词直接声称 arbitrage proof。

### 6.4 Diffusion uncertainty 传播到 option counterfactual

对 remaining maturity \(\tau=T_c-t\)：

\[
\bar\sigma_{u;t,T_c}(\beta)
=
\sqrt{\frac{\beta^\top Q_{u;t,T_c}\beta}{\tau}},
\qquad
\nabla_\beta\bar\sigma
=
\frac{Q_{u;t,T_c}\beta}{\tau\bar\sigma}.
\]

BSM delta method 给出：

\[
\nabla_\beta P_{c,t}^{\mathrm{cf}}
=
\operatorname{Vega}_{c,t}\,
\frac{Q_{u;t,T_c}\beta}{\tau\bar\sigma_{u;t,T_c}}.
\]

令 \(G\) 收集所有相关 option rows 的 gradients，则：

\[
\Sigma_{P^{\mathrm{cf}}}
\approx
G\Sigma_{\widehat\beta}G^\top.
\]

对 candidate \(i\)：

\[
g_i=\nabla_\beta\widehat a_i,
\qquad
\boxed{
(s_i^{\mathrm{param}})^2
=
g_i^\top\Sigma_{\widehat\beta}g_i
}.
\]

多腿 portfolio 必须保留 covariance；不能把各腿 SE 直接相加。Cross-strike、put-call 与 calendar legs 对共同 diffusion 的敏感度可能显著抵消，也可能同向放大。若 private-truth definition 排除 quote noise、rate/carry estimation 或其他 nuisance，则它们的 covariance 还应加到 total error；当前推荐 truth definition 使用最终 observed child bid/ask 与 private generator counterfactual，因此条件于 child quotes 时主要差异来自 counterfactual parameters。

### 6.5 Private truth、FP/FN 与 decay rate

对同一 candidate，用 private generator diffusion 构造：

\[
a_i^\star
=
s_i\Bigl[
L_i(P^{\mathrm{obs}})-L_i(P^{\mathrm{cf}}(\beta^\star))
\Bigr]-c_i,
\qquad
b_f^\star
=
\mathbf 1\!\left\{\max_{i\in\mathcal C_f}a_i^\star>0\right\}.
\]

这里 \(b^\star\) 是 private economic/model truth；它不同于 authoring requested signature，也不同于 mutation operator ID。Public canonical answer 始终是 \(\widehat b\)。

定义：

\[
\mathrm{FP}_f=\mathbf1\{\widehat b_f=1,b_f^\star=0\},
\qquad
\mathrm{FN}_f=\mathbf1\{\widehat b_f=0,b_f^\star=1\}.
\]

若局部近似：

\[
\widehat a_i=a_i^\star+\eta_i,
\qquad
\eta_i\sim N(0,s_i^2),
\]

则距离 zero-edge boundary 为 \(m_i=|a_i^\star|\) 时：

\[
P(\text{candidate classification error})
\approx
\Phi\!\left(-\frac{m_i}{s_i}\right).
\]

固定 node 数与固定非零 margin 下，\(s_i=A_i/\sqrt T\)，因此：

\[
P_{\mathrm{error}}(T)
=
\Phi\!\left(-\frac{m_i\sqrt T}{A_i}\right)
\sim
\frac{A_i}{m_i\sqrt{2\pi T}}
\exp\!\left(-\frac{m_i^2T}{2A_i^2}\right).
\]

所以结论必须分两层报告：

- diffusion-parameter SE 按 \(T^{-1/2}\) 衰减；
- 对固定、离边界非零的 signal margin，candidate FP/FN 近似按 \(e^{-cT}\) 衰减；
- 若 mutation margin 本身只有 \(O(T^{-1/2})\)，错误率不会趋于 0；
- 若 \(p\propto T\)、存在 volatility-risk-premium mismatch、模型偏差或 non-vanishing quote mechanism bias，则会出现 irreducible error floor。

### 6.6 3-node benchmark 对 price error 与 candidate FP/FN 的量级

以下只是 delta-method sanity check，不代替 full scan calibration。假设：

- \(S=100\)、\(\sigma=20\%\)；
- 30-day ATM option，Vega 约为 \$11.43 per share per unit volatility；
- signal 主要由一个 diffusion node 驱动；
- 使用 3-node benchmark；
- `price SE` 采用较保守的 boundary-node RSE；
- margin 已扣除全部 costs；
- estimator 近似 unbiased、single candidate、Gaussian error。

| Usable returns | Center-node price SE | Boundary-node price SE | Error at \$0.25 margin | Error at \$0.50 margin | Error at \$1.00 margin |
|---:|---:|---:|---:|---:|---:|
| 65 | \$0.40 | \$0.53 | 31.9% | 17.3% | 3.0% |
| 126 | \$0.29 | \$0.38 | 25.6% | 9.5% | 0.43% |
| 252 | \$0.20 | \$0.27 | 17.7% | 3.2% | 0.01% |
| 504 | \$0.14 | \$0.19 | 9.5% | 0.43% | 约 0% |

65-day、3-node 虽比 7-node 明显稳定，但对小 margin 仍会产生真实 quant workflow 中合理的 FP/FN。解决方法不是声称 exact private recovery，而是：

1. 使用 actual portfolio covariance 计算 \(s_i\)；
2. 增大 post-cost signal margin；
3. 对 near-boundary tasks 使用 uncertainty gate；
4. 用完整 Monte Carlo/parametric bootstrap 报告 scan-wide FP/FN。

### 6.7 Uncertainty margin 与 multiple-candidate scan

单 candidate 的常用 margin：

| Target one-sided candidate classification error | Required distance from boundary |
|---:|---:|
| 5% | \(m_i\ge1.645s_i\) |
| 1% | \(m_i\ge2.326s_i\) |
| 0.1% | \(m_i\ge3.090s_i\) |

但 task 会扫描大量 correlated candidates，不能用 per-candidate 1% 代替 task-level 1%。对每个 bootstrap replicate \(b\) 计算：

\[
M_b
=
\max_{i\in\mathcal C_{X}\cup\mathcal C_U\cup\mathcal C_T}
\frac{|\widehat a_i^{(b)}-a_i^{\star(b)}|}{s_i^{(b)}}.
\]

令 \(q_{1-\alpha}^{\max}\) 为 \(M_b\) 的 empirical quantile。Authoring 的 scan-wide ambiguity gate 应使用：

\[
|\widehat a_i|
\ge
q_{1-\alpha}^{\max}s_i
\]

来筛选希望稳定激活/稳定不激活的关键 candidates。是否启用 reject/ambiguous region 必须写入 variant；当前建议在 authoring 时拒绝 near-boundary task，但 Agent submission 仍输出确定的 3-bit signature，不提供运行时 abstention。

### 6.8 FP/FN calibration report

对每个 `(horizon, node_count, node_grid_shape, family, margin_bucket)`，从 clean parent seeds 与 mutation seeds 生成 independent task-level replicates，并执行与 public solver 完全相同的：

\[
\text{path fit}
\rightarrow
\text{counterfactual}
\rightarrow
\text{full candidate scan}
\rightarrow
\widehat b.
\]

Task-level events 定义为：

\[
\mathrm{any\_FP}
=
\mathbf1\{\exists f:\widehat b_f=1,b_f^\star=0\},
\qquad
\mathrm{any\_FN}
=
\mathbf1\{\exists f:\widehat b_f=0,b_f^\star=1\}.
\]

每个 release report 至少包含：

- candidate-level FPR/FNR；
- family-level \(\mathrm{FPR}_X,\mathrm{FPR}_U,\mathrm{FPR}_T\) 与 FNR；
- task-level `any_FP`、`any_FN`；
- exact-signature accuracy \(P(\widehat b=b^\star)\)；
- 8×8 signature confusion matrix；
- Hamming error 与 family/type confusion；
- error 对 post-cost margin、maturity、moneyness、node position、conditioning 的分层；
- analytic delta-method prediction 与 empirical rate 的差异；
- task-seed cluster bootstrap 或 one-sided Wilson/binomial confidence bounds。

Candidate rows 不能被当作 independent calibration samples；重采样单位至少是完整 task seed，必要时再按 parent/underlying 做 hierarchical bootstrap。

当前推荐 release gates 是：

| Metric | Recommended gate |
|---|---:|
| Clean task-level `any_FP` | one-sided 95% upper bound \(\le1\%\) |
| Mutated task-level `any_FN` | one-sided 95% upper bound \(\le5\%\) |
| 每个 family 的 FPR | one-sided 95% upper bound \(\le1\%\) |
| 每个 family 的 FNR | one-sided 95% upper bound \(\le5\%\) |
| Exact 3-bit signature accuracy | one-sided 95% lower bound \(\ge95\%\) |

Pilot 可先用不少于 2,000 个 independent task seeds/config 做诊断；若要认证 1% 级别 tail，release calibration 应继续增加 seeds，直到 confidence bound 本身通过，而不是只检查 point estimate。单个 task 的 \(\widehat b\neq b^\star\) 不自动删除；只要所属 calibrated cohort 通过 gates，它就是允许保留的真实 quant-style FP/FN。

---

## 7. v5 Verifier architecture

Semantic verifier 保持三层：

\[
\boxed{V=V_1\land V_2\land V_3}
\]

| Layer | Canonical verification target |
|---|---|
| \(V_1\) | 8 条 underlyings 的 3-node drift/diffusion estimator、observed Hessian/covariance 与 canonical uncertainty fields |
| \(V_2\) | eligible option series 的 linked-diffusion BSM \(d_1,d_2\) counterfactual、residuals 与 mutation localisation |
| \(V_3\) | 全 child DB 的 model-based X/U/T signal scan、candidate IDs、net signal edges 与 exclusivity |

在 semantic layers 之前增加不计作 trajectory step 的 \(V_0\) artifact precheck：

- schema/version；
- public DB logical checksum；
- finite/value-domain checks；
- stable IDs；
- public/private separation；
- recursive anti-leakage scan。

另设不进入 scored conjunction 的 `V_exec_audit`，调用 frozen v4 oracle 生成 executable X/U/T certificates。Authoring 侧还有 private-truth/lineage calibration；二者都是 QA/audit，不能作为 Agent shortcut 或覆盖 \(V_3\)。

src/synthetic_derivatives/verifier/f2a.py 应负责 version dispatch 与 v5 orchestration，并返回每层结构化 failure；不应把 Stage-1/2 regression 写进 f2a_oracle.py。

---

## 8. Hard-verification semantics

Exact hard verification 指：

> 双方在相同 public inputs、frozen algorithm contract 与 canonical serializer 下，得到完全一致的规范化输出。

它不表示对 optimizer 内部每个 raw floating-point operation 逐 bit 比较。

每个 scored numeric field 必须冻结：

1. input dtype；
2. preprocessing order；
3. solver/optimizer contract；
4. convergence/failure rule；
5. decimal quantization；
6. collection ordering；
7. canonical JSON representation；
8. exact comparison after canonicalization。

不同 semantic arrays 使用各自的 canonical ordering：

~~~text
underlying_fits:
  underlying_id ASC

option_fits:
  underlying_id ASC
  contract key ASC
  valuation_date ASC
  row_id ASC

mutation_diagnosis:
  mutation_group_id ASC
  affected row_id ASC

model_signals.slices:
  valuation_date ASC
  underlying_id ASC
  family order X < U < T
  candidate_id ASC

execution_audit.slices:
  valuation_date ASC
  underlying_id ASC
  family order X < U < T
  candidate_id ASC
~~~

Stage 3 的 hard core 至少比较：

- model-signal slice keys；
- canonical fitted XUT signatures；
- active families；
- candidate IDs；
- net model edges 与 standardized margins；
- duplicate/missing/extra candidates。

若 task 要求提交数值 witness，则在 quantization 后 exact compare。也可以由 verifier 从 DB 重算非核心 witness，而不要求 Agent 重复提交所有 float；schema 必须明确选择其中一种，不能临时增加 tolerance。

必须拒绝或重新生成 task 的情况：

- rank-deficient design；
- scored node 无 observation support；
- eligible option series 观测不足；
- parameterisation 不可识别；
- canonical solver 失败或存在未处理的 ambiguous minima；
- 参数非预期地 pinned to bound；
- conditioning/Hessian gate 失败；
- clean public fitted counterfactual 已产生不符合当前 cohort contract 的 model signals；
- public fitted signal rescan 不可重复或未覆盖 target families；
- quote field 不自洽；
- public artifact 泄漏 private answer；
- 所属 `(horizon,node_count,grid,family,margin)` calibration cohort 未通过 FP/FN gates。

不得因为单个 task 的 public signature 与 private signature 不同而在 verifier 中拒绝 Agent；这类差异只进入 private calibration report。

---

## 9. Parent、sampling 与 subset 数据模型

### 9.1 Universe 与 sampling

令：

\[
\mathcal U=\{u_1,\ldots,u_{22}\},
\qquad
\mathcal D=\{t_1,\ldots,t_{65}\}.
\]

第 \(b\) 个 sample：

\[
S_b\subset\mathcal U,
\qquad
|S_b|=8.
\]

Sampling contract：

- 单个 sample 内无放回；
- 不同 samples 之间有放回；
- underlying 是 cluster sampling unit；
- 不得独立 bootstrap option rows；
- 不得复制并重命名同一 underlying；
- 使用固定 sampling_seed；
- sample 内按 underlying_id canonical sort；
- sample_id 由 variant version、parent logical identity、seed 与 canonical underlying list 稳定生成。

严格统计术语是 repeated cluster subsampling，不是允许 cluster 在单个 sample 中重复出现的经典 bootstrap。

### 9.2 规模

若每个 market slice 有 56 条 option rows：

\[
8\times65\times56=29{,}120
\]

条 option rows。

若每个 slice 有 42 个 calendar candidates：

\[
8\times65\times42=21{,}840
\]

个 calendar candidates。

该规模适合 DuckDB streaming、完整 public model-signal rescan 与独立 private/execution audits。

### 9.3 Canonical keys

Market-slice key：

~~~text
(underlying_id, valuation_date)
~~~

Option-contract key 至少包含：

~~~text
(underlying_id, option_type, strike, expiry, settlement_style, multiplier)
~~~

Row ID 与 candidate ID 必须在整个 child DB 中稳定、唯一，或显式携带完整 composite scope。

### 9.4 Parent identity

记录：

- source DB canonical logical hash；
- table/schema versions；
- source row counts；
- canonical underlying universe；
- selected underlyings；
- sampling seed；
- sample-construction version。

DuckDB raw bytes 不保证稳定，identity 应优先基于 canonical sorted logical rows，不得只依赖文件 byte hash。

---

## 10. v5 parent reuse 与 node-horizon gate

当前已审计的数据约有 65 business dates，calendar-day offsets 约为 0–88；旧 seven-node grids 的部分 terminal offsets 约为 188–346。未影响任何 observed transition 的 future nodes 不可从 public path 估计，且 65-day/7-node 的 variance 明显过高。

优先方案：

> 为 v5 生成默认 **3 个 shared drift/diffusion event nodes**、且所有 scored basis functions 在 observation horizon 内获得充分 support 的 F2A-specific parent。

若复用现有 parent，只允许：

1. 评分所有 support 充分的 active nodes，并冻结 active-node boundary rule；或
2. 延长 public history 覆盖最后一个 scored node；或
3. 重新生成 node grid，使所有 scored nodes 落入 horizon。

不得要求 Agent 输出从未影响 observed transitions 的 node value。

Node-support gate 至少检查：

- 每个 scored basis 对 observed intervals 的 support；
- 最小有效 observation 数；
- design rank；
- conditioning；
- optimizer stability；
- boundary node rule；
- drift 与 diffusion grid 是否共享及其 schema 声明；
- observed Hessian/Schur-complement covariance finite、positive definite；
- 每个 diffusion node 的 RSE 与 \(n_{\mathrm{eff},j}^{I}\)；
- actual-grid counterfactual price SE 与 scan-wide standardized margins。

当前 65-day pilot 推荐：

- `diffusion_node_count = 3`；
- `drift_node_count = 3` 且与 diffusion grid shared；
- `max_diffusion_node_rse <= 0.25` 作为 initial diagnostic gate；
- 最终是否发布仍由 empirical task-level FP/FN gate 决定，而不是只由 25% RSE 决定。

若 actual event dates 导致 boundary RSE 超过 25%、Hessian condition number 超标或 signal margin 不足，应调整 node dates、减少 nodes、延长 horizon 或提高 mutation margin；不得通过隐藏 private values 或另拟合 per-contract IV 绕过。

---

## 11. Frozen DB adapter 与 integrity checks

保留现有 single-slice loader，新增 database-level streaming adapter：

~~~python
def iter_market_slices(
    db_path: Path,
    underlying_ids: tuple[str, ...],
) -> Iterator[MarketSlice]:
    ...
~~~

DuckDB 完成 predicate pushdown 与基础排序，Python 按：

~~~text
valuation_date ASC,
underlying_id ASC
~~~

逐 slice yield。不得把全部 8×65 rows 强制塞入一个巨型 MarketSlice；现有 X/U/T 数学继续以单 slice 为作用域。

每个 slice 至少检查：

- valuation date、underlying、currency 一致；
- spot、curves、quotes 全部 finite；
- \(0\le bid\le ask\)；
- multiplier 为正且属于允许集合；
- expiry > valuation_date；
- contract 为支持的 European/cash-settled style；
- calendar scan 至少有两个 live expiries；
- calendar pair 的 strike、multiplier 与 conventions 一致；
- quote tick alignment 符合 frozen dataset contract；
- row、contract、curve references 完整；
- 不静默补 quote、不静默重定价、不修改 parent。

若 frozen DB 已经完成 near-month、near-forward 与 liquidity selection，只验证并保留。若尚未完成，应按 valuation date 动态选择 live expiries，不得把静态四个 expiry IDs 硬编码到全部 65 dates。

---

## 12. 8-underlying sampler 与 subset extraction

### 12.1 Sampler

~~~python
def sample_underlyings(
    universe: Sequence[str],
    *,
    sample_size: int = 8,
    seed: int,
) -> tuple[str, ...]:
    ...
~~~

必须满足：

- deduplicated universe 恰好等于 parent universe；
- sample_size = 8 是当前 variant 的显式 contract；
- 输出无重复；
- 相同 parent/version/seed 得到相同 sample；
- 输出 canonical sorted tuple；
- seed 只进入 private generation manifest。

### 12.2 Subset extractor

~~~python
def extract_subset_db(
    parent_db: Path,
    output_db: Path,
    underlying_ids: tuple[str, ...],
) -> SubsetManifest:
    ...
~~~

必须复制：

- underlying/static metadata；
- full underlying histories；
- valuation-date spot rows；
- stable option contracts 与多日期 quotes；
- discount/funding curves；
- dividend/carry inputs；
- public node locations；
- Stage-1/2 fitting contracts；
- Stage-3 model-signal contract 与 optional execution-audit contract；
- public-only metadata。

Referential-integrity gates：

- public subset 只包含抽中的 8 个 underlyings；
- 无 orphan contracts、quotes 或 curves；
- 每个 public row 唯一归属到一个 slice 与 contract；
- expected dates 与 actual missing dates 进入 manifest；
- 所有 scored node/series support rules 可从 public subset 验证；
- 不复制 private mutation 或 generator values。

---

## 13. Quote-field consistency

Stage 2 的 observable quote 必须显式且反映 mutation。推荐：

\[
P_t^{\mathrm{obs}}=\frac{\mathrm{bid}_t+\mathrm{ask}_t}{2}.
\]

优先实现：

- public DB 只发布 mutated bid/ask；
- Stage 2 按 frozen rounding rule 从 bid/ask 计算 observed midpoint；
- 不单独发布 mid。

若必须发布 mid，则：

- mutation 同时一致更新 bid、ask、mid；
- verifier 检查 \(mid=(bid+ask)/2\) 的 canonical rounded equality。

绝不能把 clean mid 与 mutated bid/ask 同时公开，否则 clean counterfactual 会直接泄漏。

---

## 14. End-to-end authoring pipeline

### Phase A：freeze v4 与建立 v5 identity

1. Tag/freeze 当前 v4 behavior；
2. 保持 calendar enabled；
3. 增加独立 v5 config、schemas、fixtures 与 entry points；
4. 现有 v4 tests 必须继续原样通过。

### Phase B：parent qualification

1. 计算 parent logical identity；
2. 验证 schema 与 slice integrity；
3. 验证 node horizon/support；
4. 验证 stable option series；
5. 验证 public/private projection；
6. gate 失败则生成 v5-specific parent，不发布不合格 child。

### Phase C：sample 与 subset

1. 固定 seed；
2. 抽取 8 个 unique underlyings；
3. 保留全部有效 valuation dates；
4. materialise public-capable subset；
5. 完成 referential-integrity、support 与 checksum gates。

### Phase D：clean baseline dual scan

对 mutation 前的整个 subset DB 执行：

~~~python
def scan_model_signals(db_path: Path, fitted_state: FittedState) -> DatabaseSignalResult:
    results = []
    for market_slice in iter_market_slices(db_path, ...):
        results.append(scan_model_signal_slice(market_slice, fitted_state))
    return canonicalize_database_results(results)
~~~

同时运行 frozen v4 executable oracle，得到独立的 `clean_execution_audit`。两套 signature 不能混写。

Clean public model signal 出现 non-000 时：

1. 不得归因于 mutation；
2. 不得静默修 quote；
3. 同时记录 public fitted signature 与 private generator signature；
4. 若二者不同，将其计入 clean FP/FN calibration；
5. 不再逐 task 自动删除，但所属 cohort 必须通过 task-level FP gate；
6. 若 fitted score 落入 scan-wide ambiguity region，则当前 task 仍可因 stability gate 被拒绝。

### Phase E：clean Stage-1/2 fit gates

在 clean subset 上运行 canonical estimators：

- 每条 underlying 的 Stage 1 必须收敛、finite、supported、well-conditioned；
- 每条 underlying 必须输出 finite positive-definite diffusion covariance；
- 每条 eligible option series 的 linked BSM counterfactual、\(d_1,d_2\) 与 residual 必须 finite；
- base residual statistic 必须符合 frozen quote-noise rule；
- 任何 clean fit failure 都阻止 publication。

### Phase F：target slices 与 signatures

Pilot 建议为每个 subset 选择 7 个不同 target slices，分别尝试：

~~~text
001, 010, 011, 100, 101, 110, 111
~~~

其余大量 slices 提供 000 negative controls。

Selection contract：

- 只从 schema-valid、fit-valid、完整可扫描 slices 选择；
- 使用固定 mutation_seed；
- target slices 不重复；
- 第一版每个 target slice 最多一个 MutationSpec；
- selection order deterministic；
- requested private signature、private-truth realised signature、public fitted signature 与 executable-audit signature 分字段写入 private audit；
- private-truth realised signature 必须等于 requested signature；
- public fitted signature 不要求逐 task 等于 requested/private signature，其差异进入 FP/FN；
- 若需要保证 public output 的 8-signature coverage，应按 public canonical result 做 dataset balancing，但不得把 private requested signature写入 answer。

不得：

- 对全部 520 slices 穷举全部 mutation specs；
- 每尝试一个 tick 都完整重扫 8×65 DB；
- 根据 mutation kind 直接写 canonical answer；
- 假设 mutate 某个 T quote 必然只得到 001；
- 跳过 fitted-counterfactual refit、mixed-family side effects 或 full public scan；
- 用 v4 executable signature 代替 v5 model-signal signature。

### Phase G：local mutation authoring

~~~python
for requested_signature, target_slice in target_plan:
    spec = find_spec_for_private_signal_signature(
        clean_slice=target_slice,
        requested_signature=requested_signature,
        private_generator_state=private_state,
    )

    child_slice = apply_mutation(target_slice, spec)
    private_truth_result = private_truth_scan(child_slice, private_state)
    public_authoring_result = authoring_model_signal_scan(child_slice, fitted_state)
    public_reference_result = reference_model_signal_scan(child_slice, fitted_state)

    assert private_truth_result.signature == requested_signature
    assert public_authoring_result == public_reference_result
~~~

只有 private target realised、且两个独立 public model-signal implementations 一致，mutation 才能写入 child subset DB。不得增加 `public_signature == requested_signature` 的逐-task assertion。

当前 grammar 每 slice 可能约有 2,211 个 specs。Pilot 可在少量 target slices 上使用 deterministic search。后续可优化：

- candidate surplus 对 mutation ticks 的 affine response；
- integer tick interval 求解；
- interval 内代表 ticks 的完整 local scan；
- clean candidate enumeration cache。

任何 shortcut 都不能替代最终 public full rescan、private-truth calibration 与 optional execution audit，也不能改变 mutation grammar、candidate identity 或 verifier mathematics。

### Phase H：mutated Stage-2 localisation gate

对 public child 的 mutated option series 重跑 canonical Stage 2：

- target abnormal rows/groups 必须被固定 residual/grouping rule 定位；
- non-target quote noise 不得被大规模误报；
- recovered clean quote 来自 fitted counterfactual；
- 每个 affected candidate 计算 parameter-induced SE 与 standardized margin；
- estimator 结果必须 deterministic；
- private mutation lineage 与 public estimator diagnosis 的差异进入 localisation confusion report，不直接覆盖 scored result。

### Phase I：full child DB public signal rescan + private/execution audits

所有 mutations 写入后，必须重扫全部：

\[
8\times65=520
\]

个 nominal slices（实际以有效 dates 为准），而不是拼接七个 local results。

Public full rescan 必须证明：

- canonical public fitted signature 可从 public child + frozen contract 唯一复算；
- active candidate IDs 与独立 reference result 一致；
- 所有 target 与 non-target slices 都被扫描；
- 不存在遗漏或重复的 cross-slice model signals；
- 全部结果按 canonical order 序列化。

随后另行运行：

- private generator counterfactual full scan，产生 \(b^\star\) 与 candidate margins；
- frozen v4 executable full scan，产生 `execution_audit`；
- public/private candidate matching，产生本 task 的 FP/FN 与 signature confusion audit。

这两个 audit 不能进入 public Agent inputs，也不能改写 public canonical signature。

### Phase J：canonical answer

Canonical v5 answer 只能从：

- public child DB；
- frozen public estimator/solver contracts；
- Stage-1 fitted diffusion 与 covariance；
- final full child public model-signal scan

构造。

Private lineage 可以验证 generation intent，但不能直接写入 canonical semantic answer。

### Phase K：cohort calibration 与 release

1. 按 horizon、node count、grid shape、family 与 margin bucket 汇总 independent task seeds；
2. 计算 candidate/family/task-level FP/FN、8×8 confusion matrix 与 confidence bounds；
3. 估计 bootstrap maximum-statistic critical value；
4. 只有整个 cohort 通过 Section 6.8 gates 才可 release；
5. 单个 retained mismatch task 保留为真实 quant-style estimation error，不把 private truth 强行写成 Agent label。

---

## 15. Public/private task package

### 15.1 Public package

~~~text
task_<sample_id>/
├── market_subset.duckdb
├── task.md
├── answer_schema.json
└── public_manifest.json
~~~

Public DB/manifest 至少提供：

- 8 个 sampled underlying IDs；
- full public underlying histories；
- stable option contract histories；
- valuation-date coverage；
- rates、dividends/carry；
- physical node dates/offsets；
- linked-diffusion reference（指向 physical diffusion nodes）；
- Stage-1/2 fitting/counterfactual contracts；
- Stage-3 model-signal contract；
- optional execution-audit contract；
- row/contract/slice IDs；
- dataset/task/schema versions；
- public tables 与 row counts；
- public logical checksum。

Public task instruction 应要求 Agent：

1. 对 8 条 underlyings 完成 canonical 3-node fitting 并报告 covariance/uncertainty；
2. 使用 linked diffusion 对 deterministic eligible option series 构造 BSM \(d_1,d_2\) fitted counterfactual；
3. 输出 canonical residual diagnosis 与 mutation groups；
4. 扫描全部 valuation dates 和 underlyings；
5. 返回所有非 000 slices 的 model-based XUT signature、candidate IDs、net signal edges 与 standardized margins；
6. 若 schema 要求，另行返回 execution audit；不得把 audit signature 冒充 model-signal signature。

### 15.2 Private package

~~~text
private_<sample_id>/
├── canonical_answer.json
├── generation_manifest.json
├── mutation_lineage.json
├── clean_baseline_scan.json
├── clean_fit_audit.json
├── child_fit_audit.json
├── child_public_signal_scan.json
├── child_private_truth_scan.json
├── execution_audit.json
└── fp_fn_calibration_report.json
~~~

Private package 可保存：

- physical generator node values 与 linked \(\mathbb Q\)-diffusion provenance；
- clean pre-noise 与 pre-mutation quotes；
- quote-noise realizations；
- RNG seeds；
- private correlation/dependence provenance；
- target slices；
- requested signatures；
- mutation operators、directions、magnitudes；
- authoring certificates；
- Stage-1 estimator covariance/recovery error；
- private counterfactual candidate edges 与 signatures；
- per-task FP/FN/type-confusion fields；
- cohort calibration counts、confidence bounds 与 maximum-statistic quantiles；
- canonical answer cache。

Private lineage 必须通过 schema 与 semantic validation；semantic validation 应从 public child 重算关键量，而不是只验证 boolean shapes。

### 15.3 严禁 public 泄漏

Public artifact 不得含：

- drift/diffusion node values；
- 完整含 values 的 physical_dynamics；
- 完整含 values 的 pricing_dynamics.volatility_function；
- clean quotes；
- quote-noise realization；
- mutation target、operator、direction、size；
- requested signature；
- private generator-counterfactual signature \(b^\star\)；
- per-task private FP/FN flags 与 unreleased calibration tables；
- sampling/mutation seeds；
- canonical answer；
- private certificate；
- 可直接反推出答案的 generator config。

必须递归审计 solver_visible.pricing_metadata 及所有 nested objects。仅重命名 private object 不能消除泄漏。

---

## 16. Public fitting contracts

### 16.1 Physical contract

~~~yaml
physical_fitting_contract:
  time_origin:
  time_axis: calendar_day_offset
  day_count: Actual365Fixed
  dynamics_form: time_inhomogeneous_gbm
  drift_function_type: piecewise_linear
  diffusion_function_type: piecewise_linear
  default_drift_node_count: 3
  default_diffusion_node_count: 3
  drift_node_dates_or_offsets:
  diffusion_node_dates_or_offsets:
  shared_node_grid:
  interpolation: linear
  extrapolation: flat
  drift_bounds:
  diffusion_bounds:
  canonical_loss:
  canonical_solver:
  initialization:
  stopping_rule:
  tie_breaking:
  covariance_method: observed_hessian_schur_complement
  information_effective_sample_size_rule:
  node_rse_diagnostic_gate:
  input_dtype:
  output_precision:
~~~

### 16.2 Pricing counterfactual contract

~~~yaml
pricing_counterfactual_contract:
  candidate_model_family: BSM
  option_series_grouping_key:
  eligible_series_selection_rule:
  quote_observation_rule:
  rate_curve_inputs:
  dividend_or_carry_inputs:
  volatility_parameterization: linked_stage1_piecewise_linear_diffusion
  stage1_diffusion_reference:
  integrated_variance_rule:
  interpolation:
  extrapolation:
  row_weights:
  quote_noise_normalization:
  validation_statistic:
  residual_threshold:
  grouping_rule:
  price_uncertainty_method: delta_method_full_covariance
  optional_iv_diagnostic: false
  input_dtype:
  output_precision:
~~~

### 16.3 Model-signal contract

~~~yaml
model_signal_contract:
  supported_families: [X, U, T]
  candidate_enumeration_version:
  directional_candidate_rule:
  observed_quote_execution_rule:
  fitted_counterfactual_rule:
  option_fee:
  underlying_transaction_cost:
  contract_multiplier_rule:
  funding_rule:
  dividend_or_carry_rule:
  net_edge_rule:
  strict_inequality_rule:
  uncertainty_propagation_rule:
  scan_wide_ambiguity_quantile:
  canonical_order:
  output_precision:
~~~

### 16.4 Optional execution-audit contract

~~~yaml
execution_audit_contract:
  supported_families: [X, U, T]
  bid_ask_rule:
  option_fee:
  underlying_transaction_cost:
  contract_multiplier_rule:
  funding_rule:
  dividend_or_carry_rule:
  intermediate_liquidation_rule:
  terminal_certificate_rule:
  strict_inequality_rule:
  candidate_enumeration_version:
  output_precision:
~~~

### 16.5 Private calibration contract

该 contract 不进入 public task bundle，但必须版本化：

~~~yaml
fp_fn_calibration_contract:
  private_truth_definition: generator_counterfactual_vs_final_child_quotes
  independent_unit: task_seed
  stratification_keys:
    [horizon, node_count, grid_shape, family, margin_bucket]
  analytic_method: delta_method
  empirical_method: parametric_bootstrap
  multiple_scan_method: bootstrap_maximum_statistic
  confidence_bound_method:
  min_pilot_task_seeds: 2000
  clean_task_any_fp_upper_95: 0.01
  mutated_task_any_fn_upper_95: 0.05
  family_fp_upper_95: 0.01
  family_fn_upper_95: 0.05
  exact_signature_accuracy_lower_95: 0.95
~~~

---

## 17. Suggested v5 submission schema

~~~yaml
task_id:
variant_id:

underlying_fits:
  - underlying_id:
    node_dates_or_offsets:
    fitted_drift_node_values:
    fitted_diffusion_node_values:
    diffusion_covariance_matrix:
    diffusion_rse_by_node:
    information_effective_sample_size_by_node:
    objective_value:
    solver_status:

option_fits:
  - option_contract_id:
    underlying_id:
    valuation_row_ids:
    option_type:
    strike:
    expiry:
    pricing_family: BSM
    linked_diffusion_underlying_id:
    integrated_variance_by_row_id:
    fitted_counterfactual_prices_by_row_id:
    fitted_counterfactual_price_se_by_row_id:
    d1_values_by_row_id:
    d2_values_by_row_id:
    standardized_residuals_by_row_id:
    objective_value:
    fit_status:
    optional_iv_diagnostics:

mutation_diagnosis:
  - mutation_group_id:
    affected_row_ids:
    affected_dates:
    observed_quotes:
    fitted_clean_counterfactual_quotes:
    residuals:
    localisation_score:
    proposed_mutation_family:
    inferred_direction:
    estimated_mutation_magnitude:

model_signals:
  slices:
    - underlying_id:
      valuation_date:
      signature: XUT
      active_signals:
        - family:
          candidate_id:
          row_ids:
          legs:
          observed_executable_value:
          fitted_counterfactual_value:
          gross_model_edge:
          option_fees:
          underlying_costs:
          funding_and_dividend:
          net_signal_edge:
          parameter_standard_error:
          standardized_margin:
  full_scan_summary:

execution_audit:
  required: false
  slices:
  full_scan_summary:

verifier_digest:
~~~

v5 不得继续只用 orm_answer 作为唯一 semantic output。

---

## 18. Reference solver 与 verifier responsibilities

### 18.1 Reference solver

Reference solver：

- 只依赖 public package；
- 不 import verifier 或 authoring implementation；
- 独立实现 versioned Stage-1 fit/covariance 与 Stage-2 linked BSM counterfactual contract；
- 独立实现公开 Stage-3 model-signal candidate contract；
- 输出 canonical v5 submission。

Database-level Stage-3 orchestration：

~~~python
def solve_f2a_database(db_path: Path) -> CanonicalDatabaseAnswer:
    fitted_state = fit_underlyings_and_counterfactuals(db_path)
    answers = []
    for market_slice in iter_market_slices(db_path, ...):
        answer = solve_model_signal_slice(market_slice, fitted_state)
        if answer.signature != "000":
            answers.append(answer)
    return canonicalize_database_answers(answers)
~~~

### 18.2 Verifier

Verifier：

1. 验证 variant/schema/checksum；
2. 解析并 canonicalize submission；
3. 从 public data 复算 Stage 1；
4. 从 public data 复算 linked BSM counterfactual、uncertainty 与 localisation；
5. 对 full child DB 独立执行 Stage-3 model-signal scan；
6. 比较 canonical outputs；
7. 返回按 layer 分类的 failure；
8. 不读取或比较 private requested/private-truth signature；
9. 允许 residual/counterfactual 进入 model signal，但拒绝把 model signal 伪称为 executable proof；
10. execution audit 若存在，单独验证且不覆盖 model signature；
11. 不引入未定义 tolerance。

---

## 19. Required code changes

| 路径/组件 | 统一后的改动 |
|---|---|
| src/synthetic_derivatives/verifier/f2a.py | version dispatch；v5 的 V1/V2/V3 orchestration；保留 v4-compatible entry point |
| src/synthetic_derivatives/verifier/f2a_contract.py | 保护 execution primitives；另增 typed model-signal edge/counterfactual fields |
| src/synthetic_derivatives/verifier/f2a_oracle.py | 保持 v4 executable X/U/T oracle；不加入 fitting 或 v5 signal verdict |
| 新 Stage-1 verifier module | basis、exact interval integrals、canonical 3-node estimator、Hessian/Schur covariance |
| 新 Stage-2 verifier module | linked diffusion、BSM \(d_1,d_2\) counterfactual、price SE、residuals、localisation |
| 新 Stage-3 model-signal module | directional candidates、post-cost fitted edge、uncertainty margin、full scan |
| 新 private calibration module | private-truth scan、task-seed bootstrap、FP/FN/confusion/maximum statistic；不得进入 public verifier |
| src/synthetic_derivatives/solver/f2a.py | 增加 versioned full-trajectory solver；不静默改变 v4 |
| src/synthetic_derivatives/authoring/f2a_child_materializer.py | 生成 8-underlying multi-date public child、fitting contracts 与 stable IDs |
| src/synthetic_derivatives/authoring/schema.py | 拆分 public node locations 与 private values；修复 nested leakage |
| database-level adapter | iter_market_slices、subset scan、logical checksum |
| sampler/subset extractor | repeated cluster subsampling 与 referentially complete DuckDB |
| schemas/trajectory.schema.json | 增加 typed Stage-1/2/diagnosis/Stage-3 outputs；必要时 version |
| schemas/submission-v4.schema.json | 保持 frozen；新增独立 v5 schema |
| configs/variants/bsm_arbitrage_finding_f2a_v4.json | 保持 frozen；新增 v5 config |
| authoring/oracle metadata | private 保存 generator、noise、mutation lineage、private truth 与 FP/FN report；不进入 public projection |
| CLI | 单入口生成 sample、mutate、rescan、package 与 verify |
| Documentation/AGENTS | 明确 model-based X/U/T signal 是 v5 scored target；v4 executable arbitrage 是独立 audit |

---

## 20. CLI 与 reproducibility

建议单一入口：

~~~bash
uv run python scripts/materialize_f2a_agent_tasks.py \
  --variant bsm_model_reconstruction_xut_signal_f2a_v5 \
  --parent-db path/to/qualified_v5_parent.duckdb \
  --sample-size 8 \
  --physical-node-count 3 \
  --num-samples 1 \
  --sampling-seed 20260808 \
  --mutation-seed 20260808 \
  --target-signatures 001,010,011,100,101,110,111 \
  --output-dir artifacts/f2a_agent_tasks
~~~

相同 parent logical hash、code/version contracts 与 seeds 必须生成：

- 相同 8-underlying sample；
- 相同 eligible series；
- 相同 target slices；
- 相同 MutationSpecs；
- 相同 canonical logical child rows；
- 相同 Stage-1 covariance、Stage-2 counterfactual 与 uncertainty results；
- 相同 full child public model-signal scan；
- 相同 private truth/execution audits（authoring environment only）；
- 相同 canonical answer；
- 相同 logical checksums。

不得依赖：

- filesystem iteration order；
- Python hash randomization；
- 未排序 SQL results；
- wall-clock timestamps；
- 非冻结 solver defaults。

---

## 21. Authoring publication gates

每个 v5 task/cohort 必须依次通过：

1. **Parent identity gate**：source logical hash、schemas 与 versions 明确；
2. **Slice integrity gate**：quotes、curves、contracts、dates 与 keys 合法；
3. **3-node support gate**：每个 scored node 有充分且非退化 support，default node count = 3；
4. **Underlying-fit/information gate**：canonical Stage 1 deterministic convergence，covariance finite positive-definite，RSE/conditioning 合格；
5. **Option-series support gate**：每条 eligible series 观测充分；
6. **Clean BSM-counterfactual gate**：linked \(d_1,d_2\) outputs finite，pre-mutation residual 满足 quote-noise criterion；
7. **Clean dual-scan gate**：public model signals 与 executable audit 均被完整记录；自然 non-000 不自动删除，进入 calibration；
8. **Mutation-localisation gate**：public-child estimator 按固定规则定位 target rows/groups；
9. **Private target gate**：private-truth realised signature 等于 requested signature；不要求 public fitted signature 相等；
10. **Signal uncertainty gate**：actual portfolio covariance、standardized margins 与 scan-wide ambiguity rule 合格；
11. **Public full-scan gate**：full scan 无遗漏/重复 signals，独立 public implementations 一致；
12. **Execution-audit gate**：v4 oracle 可完整运行并独立存档；不要求其 signature 等于 model signal；
13. **FP/FN cohort gate**：task-level/family-level confidence bounds 与 exact-signature accuracy 通过 Section 6.8；
14. **Reference-solver gate**：仅依赖 public package 得到 canonical fitted answer；
15. **Leakage gate**：public DB、metadata、logs 与 bundle 均无 private values/truth/calibration flags；
16. **Determinism gate**：重复运行得到相同 canonical public artifacts。

不要增加“所有 post-mutation quotes 都有 finite IV”的 gate，也不要增加逐-task `public_signature == private_signature` gate。

---

## 22. Required tests

### 22.1 v4 preservation

- 现有 X/U/T catalogue tests 原样通过；
- calendar runtime 继续 enabled；
- 相同 public slice 的 v5 `execution_audit` 与 v4 certificate 完全一致；
- v5 model-signal signature 允许与 v4 executable signature 不同；
- 不出现 arbitrary position grid。

### 22.2 Sampler

- 相同 seed 得到相同 sample；
- 不同 seed 能产生不同 sample；
- 每个 sample 恰好 8 个 unique underlyings；
- IDs 均来自 parent universe；
- canonical ordering 稳定。

### 22.3 Subset DB

- 只含选中的 underlyings；
- full histories 与 eligible option series 完整；
- 无 orphan rows；
- rows/counts 与 manifest 一致；
- option/curve/spot keys 不冲突；
- expired/unsupported contracts 被明确拒绝；
- logical checksum 可重复。

### 22.4 Stage 1

- hat basis tests；
- exact \(A_k,Q_k\) integration；
- weekend、irregular interval 与 cross-node cases；
- flat extrapolation；
- constant special case；
- synthetic estimator recovery；
- exact 3-node equal-grid Fisher benchmark：center/boundary RSE scaling；
- 65-return 3-node benchmark 与 \(n^{-1/2}\) regression test；
- observed Hessian 与 drift-profiled Schur covariance；
- covariance positive-definiteness、RSE、\(n_{\mathrm{eff}}^I\)；
- node ordering/bounds；
- node-horizon rejection；
- rank/conditioning gates；
- multi-underlying isolation；
- canonical rounding/serialization。

### 22.5 Stage 2

- call/put \(d_1,d_2\) formulas；
- constant volatility；
- node-based integrated variance；
- Stage-1 diffusion 正确 linked 到 every eligible option series；
- Stage 2 不得另拟合 per-contract volatility；
- fixed coefficients：call intercept 0、coefficients \(1,-1\)；
- put-specific formula；
- per-row IV 不作为 core estimator；
- quote-noise-only residuals 低于 threshold；
- known mutations 定位到正确 rows/groups；
- recovered fitted counterfactual；
- expiry/near-zero maturity；
- extreme moneyness；
- invalid/non-finite inputs；
- linked maturity/extrapolation 与 series-support rejection；
- price-gradient、full covariance 与 delta-method price SE；
- canonical rounding/serialization。

### 22.6 Stage-3 model signals 与 uncertainty

- X/U/T directional candidate IDs 固定且双方向独立枚举；
- observed/counterfactual portfolio functional 与 cost decomposition；
- net edge strict inequality、tick rounding 与 tie-break；
- multi-leg covariance 包含 off-diagonal terms；
- vega cancellation 与 vega amplification fixtures；
- family bits 来自 max candidate edge；
- full DB model-signal scan 无 missing/duplicate candidates；
- execution audit 与 model signal 不互相覆盖。

### 22.7 Quote consistency 与 visibility

- public 只含 node dates，不含 node values；
- public 不含 generator seeds；
- public 不含 clean pre-noise/pre-mutation quotes；
- public mid 若存在必须等于 mutated bid/ask midpoint；
- recursive nested metadata leakage scan；
- private audit metadata 可用但无法进入 solver-visible projection。

### 22.8 Mutation authoring

- 七种 private-truth non-zero signatures 由真实 mutation + private scan 达到；
- 每 target slice 至多一个 MutationSpec；
- 两个独立 public model-signal implementations 一致；
- requested/private-truth realised signatures 一致；
- public/private signature mismatch 被记录为 FP/FN 而非改写；
- model residual 只有经过 frozen candidate functional 与 costs 才能激活 Stage 3 signal；
- v5 signal 不得被序列化成 executable certificate。

### 22.9 FP/FN calibration

- analytic \(T^{-1/2}\) parameter-SE benchmark；
- fixed-margin Gaussian error 随 \(T\) 的 decay sanity check；
- task-seed 而非 candidate-row bootstrap；
- 8×8 signature confusion matrix counts conservation；
- family/task FPR/FNR denominators 正确；
- one-sided confidence bounds；
- maximum-statistic quantile 与 correlated candidates；
- single mismatch task 不被 public verifier 错拒；
- failed cohort 阻止 release；passed cohort 允许 release。

### 22.10 Full rescan 与 verifier negative controls

至少拒绝：

- missing/extra slice；
- wrong signature/family；
- wrong/missing/extra candidate ID；
- duplicate candidate；
- 被禁止的 legacy maximal_spread 等旧输出；
- malformed JSON；
- non-finite numeric evidence；
- perturbed drift/diffusion node output；
- perturbed linked integrated variance/counterfactual/residual/localisation；
- perturbed covariance、price SE、net signal edge 或 standardized margin；
- wrong observed execution side/leg/cost decomposition；
- stale clean-parent answer；
- 只根据 mutation target 猜测、未处理 mixed-family side effects 的答案；
- leaked-node shortcut；
- row-by-row IV shortcut；
- BSM residual 未经 candidate functional/costs 被直接报告为 signal；
- model signal 被错误标成 executable proof；
- private truth、requested signature 或 FP/FN flags 泄漏进 public submission。

### 22.11 End-to-end

- 每个 XUT signature 至少一个 deterministic fixture；
- full solver 通过 \(V_1\land V_2\land V_3\)；
- public/private mismatch fixture 仍按 public canonical answer 通过；
- v4 execution audit 保持独立正确；
- reference solver 仅使用 public package；
- package 从空临时目录可运行；
- 相同 inputs 重跑得到 byte-identical canonical JSON 与相同 verdict。

---

## 23. Pilot milestones

### Milestone A：single-sample v5 end-to-end

- qualification 或生成一个合格 v5 parent；
- 固定 seed；
- 抽取 8 个 underlyings；
- 保留全部有效 dates；
- 使用 default 3-node shared grid；
- clean Stage-1/2 gates 通过；
- 记录 clean public/private/execution 三套 signatures；
- 生成至少一个 private 001 与一种 mixed signature，目标为七种 private non-zero signatures；
- mutated Stage-2 localisation 通过；
- full child public model-signal rescan + private truth/execution audits；
- diffusion covariance、candidate SE 与 standardized margins；
- public/private package；
- reference solver 通过；
- negative controls 全部被拒绝。

### Milestone B：multi-seed generation

- 生成 8–32 个 samples；
- underlyings 可跨 sample 重复；
- 记录 runtime、search attempts、failed signatures、fit rejections 与 coverage；
- 当前不做 train/validation/test split。

### Milestone C：FP/FN calibration

- 每个主要 config 先运行不少于 2,000 个 independent task seeds；
- 输出 family/task FP/FN、8×8 signature confusion、margin buckets 与 confidence bounds；
- 估计 bootstrap maximum-statistic critical value；
- 对 1% tail 继续增加 seeds，直到 confidence bound 而非 point estimate 通过；
- 固化 passed cohort IDs 与 calibration-report checksum。

### Milestone D：performance optimization

只在 pilot 暴露实际瓶颈后实施：

- affine mutation windows；
- DuckDB predicate pushdown；
- market-slice streaming；
- clean scan/candidate cache；
- linked counterfactual/gradient cache；
- calibration replicate 安全并行；
- 不同 slices 的安全并行；
- 最终 canonical order 保持串行确定性。

性能优化不得改变 calibrated publication gates 或数学 contract。

---

## 24. 推荐实施顺序

1. Freeze/tag v4 behavior 与 tests；
2. 新增 v5 variant/config/schema；
3. 修复 public/private metadata projection；
4. qualification 当前 parent；
5. 若 qualification 失败，生成 v5-specific parent；
6. 实现 sampler、subset extractor 与 database adapter；
7. materialise 8-underlying underlying/option histories；
8. 实现 canonical 3-node Stage-1 estimator、Hessian/Schur covariance 与 scaling tests；
9. 实现 linked Stage-2 BSM \(d_1,d_2\) counterfactual、gradients、price SE 与 localisation；
10. 实现独立的 v5 model-signal X/U/T scanner；
11. 接入现有 v4 X/U/T oracle 作为 `execution_audit`；
12. 完成 clean dual scan、local mutation authoring 与 three-way full rescan；
13. 扩展 reference solver 与 submission；
14. 实现 private-truth scan、FP/FN/confusion 与 maximum-statistic calibration；
15. 增加 visibility、identifiability、determinism 与 negative tests；
16. 生成 single-sample pilot；
17. 再进行 multi-seed calibration 与性能优化。

---

## 25. Definition of Done

- [ ] v4 保持 runnable、EXECUTABLE、runtime enabled；
- [ ] v5 有独立 variant、config、schema 与 fixtures；
- [ ] 一个命令可生成至少一个完整 v5 task；
- [ ] 每个 task 恰好包含 8 个 unique underlyings；
- [ ] public task 含完整 supported underlying histories 与 stable option series；
- [ ] public 只暴露 node locations，不暴露 node values；
- [ ] 每个 scored node 真正影响 public observations；
- [ ] 65-business-day pilot 默认只使用 3 个 shared drift/diffusion nodes；
- [ ] Stage 1 仅在已知 grid 上拟合 node heights，使用 exact interval contract，并输出 covariance/RSE/\(n_{\mathrm{eff}}^I\)；
- [ ] Stage 2 使用 Stage-1 linked diffusion 构造 BSM \(d_1,d_2\) fitted counterfactual，不另拟合 per-contract volatility；
- [ ] per-row IV 仅为 optional diagnostic；
- [ ] mutated bid/ask 不泄漏 clean mid；
- [ ] clean public model signal、private truth 与 executable audit 分开保存；
- [ ] clean Stage-1/2 fit gates 通过；
- [ ] mutation localisation、model signal 与 executable certification 三者分离；
- [ ] 至少覆盖一个 private 001 与一种 mixed signature，pilot 目标覆盖七种 private non-zero signatures；
- [ ] mutation 后执行 full child public-signal rescan、private-truth scan 与 execution audit；
- [ ] public fitted signature 由 net post-cost counterfactual edge 唯一决定；
- [ ] 每个 active signal 报告 parameter SE 与 standardized margin；
- [ ] FP/FN、8×8 confusion 与 confidence bounds 按 task seed 校准；
- [ ] cohort 通过 1% FP、5% FN、95% exact-signature 推荐 gates；
- [ ] 单个 public/private mismatch 不被 hard verifier 错误拒绝；
- [ ] canonical answer 只由 public child + frozen contracts + final scan 构造；
- [ ] public/private artifacts 严格分离；
- [ ] reference solver 只依赖 public data；
- [ ] hard verifier 对规范化输出 exact compare，不引入 tolerance；
- [ ] negative controls 全部被拒绝；
- [ ] 相同 parent/version/seeds 生成相同 logical task；
- [ ] 现有 v4 executable X/U/T math、candidate IDs 与 mutation grammar 未被擅自重写。

---

## 26. Final acceptance criterion

返修完成后，以下 shortcut 均不能通过：

- 读取泄漏的 physical/pricing node values；
- 对每个 quote 独立做 IV inversion；
- 用 BSM residual 不经过 frozen candidate functional、costs 与 edge rule 就直接激活 signal；
- 把 model-based X/U/T signal 伪装成 model-free executable proof；
- 根据 private requested signature 或 mutation kind 猜答案；
- 只扫描 target slices 而不扫描整个 child DB；
- 忽略 bid/ask、fees、underlying costs、funding、dividends 或 multiplier。

最终 acceptance 分为 public Agent submission 与 private authoring release 两层，合起来必须保证：

\[
\boxed{
\begin{aligned}
&\text{public-path node estimator 输出正确；}\\
&\text{linked BSM }d_1,d_2\text{ counterfactual、uncertainty 与 localisation 正确；}\\
&\text{所有报告的 model-based X/U/T signals 使用 frozen post-cost edge；}\\
&\text{full public signal scan 不存在遗漏或重复；}\\
&\text{private cohort FP/FN 与 signature-confusion gates 通过；}\\
&\text{execution audit 与 model signal 保持独立。}
\end{aligned}
}
\]

其中前四项由 public reference solver/hard verifier 检查；后两项由 authoring environment 检查，绝不能要求 Agent 读取 private truth 或 calibration report。

这才是最终闭合的 F2A agent task：public child 上的 canonical estimator 与 model-based X/U/T signal 仍能 deterministic hard-verify；相对 private generator truth 的少量 FP/FN 被显式量化并控制，而不是被错误地宣称为不存在；v4 transaction-cost-aware executable certificates 继续作为独立、受保护的 realism audit。
