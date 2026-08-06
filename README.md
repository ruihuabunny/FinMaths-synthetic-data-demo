# FinMaths Synthetic Data Demo

面向 LLM 训练的确定性合成金融衍生品数据项目。项目目标是把市场数据生成、任务定义、模型解题、独立校验和训练数据导出分成清晰的边界，并保证每个样本都可以通过固定配置与 seed 重放。

当前已完成 authoring pipeline 的第一版、correlated-underlying simulator 第一阶段以及
带流动性筛选和报价噪声的 `OptionChainBuilder`：
QuantLib 生成 underlying 路径和 European option 报价，DuckDB 事务增量写入 snapshot；
config `1.2.0` 可以用 factor loading $\Lambda$ 派生 $D$ 与 PSD correlation matrix $R$，
并只对物理测度 $\mathbb P$ 下的 underlying close shocks 做联合耦合；config `1.3.0`
则以 expiry × listing-moneyness × call/put 网格生成稳定 option contracts，挂牌时把
moneyness 转为绝对 strike 后冻结；config `1.4.0` 将网格视为候选集，通过不可变的
expiry/moneyness liquidity rule 只挂牌流动性较好的近月、近价合约，并以可重放的 quote
noise 独立扰动 bid/ask half-spread。Config `1.5.0` 为当前 metals snapshot 抽样并冻结
piecewise-linear $\mathbb P$-measure drift/volatility 节点，声明 money-market numeraire 下
drift-only Girsanov mapping，以相同 deterministic diffusion 在 $\mathbb Q$ 下定价，并从
canonical mid 调用 QuantLib 反解 IV。Option/derivative pricing 不读取 $\mathbb P$ 相关矩阵。
仓库同时包含最小可运行的六维 `task_space` registry、受约束
`mutation` engine 和 adaptive `curriculum` scheduler；Solver、verifier 与训练数据构建
尚未实现。

当前 single-asset vanilla baseline 已有 snapshot-wide $\mathbb Q$/numeraire/rate-path
identity；多资产 $\mathbb Q$ dependence 与 basket/index/spread joint pricing 仍是后续
pricing-context 工作。届时相关结构应定义在 $\mathbb Q$-measure underlying/model drivers
上，而不是定义在 derivative contracts 之间。

## 设计流程

```text
Authoring
  -> 生成并冻结 market snapshot（correlated profile 同时冻结 underlying dependence spec）
  -> 注册六维 task variant 与 compatibility decision
  -> Mutation Engine 生成带 lineage 的 candidate pool
  -> Curriculum Scheduler 按 mastery 选择训练分布
  -> Solver 手工计算 IV / Greeks / smile
  -> Trusted verifier 独立复算并执行 exact equality
  -> 记录 ORM outcome 与 agent trajectory
  -> 导出 LLM training dataset
```

Authoring、Solver 和 Trusted verifier 是三个不同的权限边界：

- Authoring 可以使用固定版本的 QuantLib 和固定 seed 生成市场数据。
- Solver 只能读取公开快照与合同，不得调用现成的定价、IV、Greeks 或 smile API。
- Trusted verifier 可以使用固定版本的金融与数值包独立复算，但不能向 Solver 暴露 oracle 或 hidden tests。

## Public metals snapshot

仓库内置 [22-metal generator 配置](configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)
和 [DuckDB public snapshot](snapshots/public/quantlib_bsm_smoke_v1.duckdb)。文件名保留旧的
`quantlib_bsm_smoke_v1` 路径以兼容现有入口，但逻辑 snapshot 已更新为
`DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3`：

| 对象 | 行数 |
|:---|---:|
| Underlying master | 22 |
| Option contract master | 1,232（22 × 56） |
| `underlying_daily` | 1,430（22 × 65 日） |
| `option_daily` | 60,368（只在 expiry 前报价） |
| `pricing_metadata` | 1,430（22 × 65 日） |
| `option_pricing_audit` | 60,368（private） |

Option candidate grid 为 6 个期限 × 11 个 listing-moneyness × call/put。Inclusive
liquidity filter 只保留 30/60/90/180 天和 0.85–1.15 moneyness，因此每个 underlying
挂牌 56 个 static contracts。每个 valuation/expiry 区间先对 deterministic
$\sigma_Q^2(t)$ 精确积分并取 RMS，再用 QuantLib analytic BSM engine 计算 mid；
`mid = settlement_price`，deterministic clipped-Gaussian quote noise 只作用于 bid/ask
half-spread。Private audit 从这个已量化 canonical mid 反解 IV，不把定价输入 volatility
直接冒充 IV。

查看已冻结 public snapshot：

```bash
make install
make snapshot-summary
```

若要从配置重放，请生成到新文件；不要修改已冻结的 checked-in snapshot：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v2.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  create-smoke
```

完整 schema、主键、增量规则和命令见 [Authoring Pipeline](docs/authoring_pipeline.md)。
原 [5-underlying legacy smoke config](configs/generators/quantlib_bsm_smoke_v1.json) 仍用于
快速 additive/compatibility tests，不再对应 checked-in public DuckDB。当前 metals profile
是合成的市场规模近似，不宣称复刻某个真实交易所的 calendar、carry、expiry 或
settlement 规范。

## Deterministic physical drift / volatility

Generator config schema `1.1.0+` 支持把 underlying 的 P-measure
`physical_drift` 和 `physical_volatility` 配置成时间的确定性分段线性函数。原来的
scalar 写法继续支持，并等价于 constant function。当前 checked-in metals config
`1.5.0` 由 [sampling script](scripts/sample_physical_dynamics.py) 为每只 underlying 独立
抽取 sampling seed、node-offset grid、drift phi/std、log-vol phi/std 和最终节点值。所有
分布都有硬边界；22 组 offsets 和每一类 hyperparameter realized value 均强制互不相同。
脚本把 global/per-underlying seeds、bounds、realized parameters 和 nodes 全部冻结到 config；
重放 snapshot 时不会重新抽样。核心配置片段如下；完整 provenance 以 checked-in config
为准。

```json
{
  "schema_version": "1.5.0",
  "start_date": "2026-08-03",
  "underlyings": [
    {
      "underlying_id": "SYNTH-EXAMPLE-01",
      "initial_spot": 100.0,
      "physical_drift": {
        "type": "piecewise_linear",
        "nodes": [
          {"day_offset": 0, "value": 0.07},
          {"day_offset": 2, "value": 0.04},
          {"day_offset": 4, "value": 0.08}
        ],
        "extrapolation": "flat"
      },
      "physical_volatility": {
        "type": "piecewise_linear",
        "nodes": [
          {"day_offset": 0, "value": 0.22},
          {"day_offset": 2, "value": 0.30},
          {"day_offset": 4, "value": 0.18}
        ],
        "extrapolation": "flat"
      },
      "risk_free_rate": 0.03,
      "dividend_yield": 0.01
    }
  ],
  "q_pricing": {
    "risk_neutral_measure_id": "USD-MONEY-MARKET-Q-v1",
    "numeraire_id": "USD-MONEY-MARKET-ACCOUNT-v1",
    "rate_path_id": "USD-FLAT-CONTINUOUS-RATE-v1",
    "measure_change": "girsanov_drift_only",
    "volatility_mapping": "same_deterministic_diffusion",
    "implied_volatility_solver": {
      "method": "QuantLib.VanillaOption.impliedVolatility",
      "target_quote": "canonical_mid",
      "accuracy": 1e-12,
      "max_evaluations": 1000,
      "minimum_volatility": 1e-6,
      "maximum_volatility": 4.0
    }
  }
}
```

`day_offset` 是从 `start_date` 开始计算的日历日，不是 business-day index。节点间
使用线性插值；第一个节点必须为 0，节点必须按严格递增的整数 offset 排列，节点外
目前只支持 flat extrapolation。Physical volatility 的所有节点必须大于 0。例如
上面的 drift 在 day 0、1、2 分别为 `0.070`、`0.055`、`0.040`。

路径生成不是简单地取 close date 终点处的函数值，而是对每个
`previous close -> current close` 区间计算精确等效参数：

$$
\mu_{\mathrm{eff}}
=\frac{1}{\Delta t}\int_{t_0}^{t_1}\mu(t)\,dt,
\qquad
\sigma_{\mathrm{eff}}
=\sqrt{\frac{1}{\Delta t}\int_{t_0}^{t_1}\sigma^2(t)\,dt}.
$$

线性段端点为 $a,b$ 时，drift 的区间平均是 $(a+b)/2$，volatility 的区间
RMS 是 $\sqrt{(a^2+ab+b^2)/3}$。跨节点或跨周末的区间会在节点处拆分并按完整
日历区间积分。因此 Aug 3 到 Aug 4 的 drift 若从 `0.070` 线性走到 `0.055`，
传给 QuantLib 的 $\mu_{\mathrm{eff}}$ 为 `0.0625`；volatility 若从 `0.22`
走到 `0.26`，则 $\sigma_{\mathrm{eff}}$ 约为 `0.240277617`。

当前实现由项目代码完成 piecewise-linear 插值与精确积分，再把每个区间的等效参数
分别放入 QuantLib `FlatForward` 和 `BlackConstantVol`，最后调用
`BlackScholesMertonProcess.evolve`。这与 deterministic time-inhomogeneous GBM
在观测网格上的 exact transition 对齐：

$$
\log\frac{S_{t_1}}{S_{t_0}}
=\int_{t_0}^{t_1}\left(\mu(t)-\frac12\sigma^2(t)\right)dt
+\sqrt{\int_{t_0}^{t_1}\sigma^2(t)dt}\,Z.
$$

对应实现位置：

- [config.py](src/synthetic_derivatives/authoring/config.py)：配置解析、节点验证、线性插值、drift 积分与 volatility RMS；
- [generator_common.py](src/synthetic_derivatives/authoring/generator_common.py)：共享 pinned QuantLib、calendar/day-count、日期转换、价格量化与 namespaced RNG；
- [underlying_daily_generator.py](src/synthetic_derivatives/authoring/underlying_daily_generator.py)：按真实日期区间计算等效参数、生成 P-measure underlying path/dependence 和 pricing metadata；
- [option_daily_generator.py](src/synthetic_derivatives/authoring/option_daily_generator.py)：生成 frozen option contracts，并从 realized spot 生成 daily quotes；不提供 underlying path/dependence API；
- [pipeline.py](src/synthetic_derivatives/authoring/pipeline.py)：增量生成时保留每条路径的真实 previous date，保证一次生成和 append 的结果一致；
- [公开测试](tests/public/test_authoring_template.py)与[单元测试](tests/unit/test_authoring_config.py)：覆盖每日参数变化、精确积分、append invariance、配置校验与函数定义不可变性。

`market.underlyings.physical_drift` 和 `physical_volatility` 为兼容现有 schema，保存
函数在 `day_offset = 0` 的值。完整函数、当日实际使用的有效参数、区间起止日期和
reduction method 写入 `market.pricing_metadata.physical_dynamics`。`start_date` 行是
$S(t_0)=S_0$ 的 initial condition，不伪造前一日 transition；后续 business-date 行按真实
calendar interval 演化。

当前 config `1.5.0` 另声明一个明确、局部的模型假设：在这个 deterministic-diffusion
GBM baseline 中，Girsanov change of measure 只把 drift 从 $\mu_P(t)$ 改为 $r-q$，扩散函数
保持 $\sigma_Q(t)=\sigma_P(t)$。这不是“physical volatility 按定义等于 IV”，也不是通用
的 volatility-risk-premium 结论。European option 在 $[t,T]$ 使用

$$
\sigma_{Q,\mathrm{eff}}(t,T)
=\sqrt{\frac{1}{T-t}\int_t^T\sigma_Q^2(u)\,du},
$$

由 QuantLib 计算未舍入理论价；mid 量化到 8 位后，再由声明的 QuantLib solver 对这个
可见 canonical mid 反解 IV。完整 Q mapping、输入 volatility、未舍入价格、反解结果和
失败状态只保存在 private `market.option_pricing_audit`，没有 solver-visible view。

可以直接检查每天实际使用的 interval-equivalent 参数：

```sql
SELECT
    valuation_date,
    underlying_id,
    CAST(json_extract_string(physical_dynamics, '$.drift') AS DOUBLE)
        AS effective_drift,
    CAST(json_extract_string(physical_dynamics, '$.volatility') AS DOUBLE)
        AS effective_volatility,
    json_extract_string(physical_dynamics, '$.interval_start') AS interval_start,
    json_extract_string(physical_dynamics, '$.interval_end') AS interval_end
FROM market.pricing_metadata
ORDER BY underlying_id, valuation_date;
```

## Correlated underlying simulator

Generator config `1.2.0` 在上述单资产 physical dynamics 之上增加
`underlying_simulation`。其 `driver_order` 只能包含 underlying ids，相关结构不接受
option ids：

```json
{
  "schema_version": "1.2.0",
  "underlying_simulation": {
    "dependence_spec_id": "SYNTH-P-SPOT-FACTOR-v1",
    "measure": "P",
    "driver_order": ["SYNTH-U01", "SYNTH-U02"],
    "formulation": "factor_loading",
    "factor_loading_matrix": [[0.8], [0.5]],
    "idiosyncratic_diagonal": "derive_from_row_norms",
    "matrix_dtype": "float64",
    "factorization_method": "factor_loading_direct",
    "factorization_order": "declared_driver_order",
    "time_grid": "business_daily",
    "regime_id": "constant"
  }
}
```

若 $\lambda_i^\top$ 是 $\Lambda$ 的第 $i$ 行，则 generator 确定性派生

$$
D=\operatorname{diag}\!\left(1-\lVert\lambda_1\rVert^2,\ldots,
1-\lVert\lambda_n\rVert^2\right),
\qquad
R=\Lambda\Lambda^\top+D,
$$

并按

$$
Z_t=\Lambda\eta_t+D^{1/2}\varepsilon_t
$$

替换每个 underlying GBM close transition 的标准正态冲击。Common factor 和
idiosyncratic streams 分别按 `dependence_spec_id/factor/date` 与
`dependence_spec_id/underlying_id/date` 派生，因此 one-shot 与 append 完全一致。

完整合同持久化在 private authoring 表 `market.underlying_dependence`，不建立对应的
solver-visible view；每个 `pricing_metadata.physical_dynamics` row 只记录关联的
dependence id 与 driver lineage。`option_daily_row()` 仍只读取固定 spot 和原有 pricing
inputs：在相同 spot 下切换 $R$，option quote 必须完全不变。可运行配置见
[correlated-underlying 模板](authoring/templates/quantlib_bsm_correlated_underlyings.template.json)，
完整增量规则见 [Authoring Pipeline](docs/authoring_pipeline.md)。

## OptionChainBuilder

Generator config `1.3.0` 用一个显式 `option_chain` 合同替代逐条
`option_templates`。当前第一版只支持 `listing_rule=snapshot_start` 与
`roll_rule=static`，避免在尚未定义 exchange schedule 时隐式滚动：

```json
{
  "schema_version": "1.3.0",
  "option_chain": {
    "chain_id": "STATIC-GRID-v1",
    "expiry_days": [30, 90, 180],
    "moneyness_grid": [0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2],
    "call_put": ["call", "put"],
    "listing_rule": "snapshot_start",
    "roll_rule": "static",
    "strike_increment": 0.5,
    "strike_rounding": "ROUND_HALF_EVEN",
    "exercise_style": "european",
    "settlement_type": "cash",
    "contract_multiplier": 100
  }
}
```

`OptionChainBuilder` 要求 `moneyness_grid` 与 `strike_grid` 恰好选择一个，再按 expiry、
grid value、call/put 的规范顺序展开完整 Cartesian grid。每个 moneyness 只在 listing
date 使用一次：以 listing spot 乘 moneyness，再按
`strike_increment` 与 `ROUND_HALF_EVEN` 得到绝对 strike。`market.option_contracts`
保存 listing date/spot/moneyness 与 frozen strike；后续 spot 变化、append 或
`sync-config` 都不会改写合约。若直接使用 `strike_grid`，输入 strike 只按相同 increment
规范化并冻结，不依赖 listing spot。`market.option_chain_specs` 私有保存 grid type/value、listing/roll
与 rounding 合同，不创建 solver-visible view。

同一 expiry/strike 必须同时存在 call 和 put，expiry 与所选 grid 必须正值且严格
递增；若 strike increment 使不同 grid inputs 舍入到同一 strike，配置会被拒绝。Chain
规则和已挂牌合约在同一 snapshot 内不可修改。schema `1.0`--`1.2` 的 legacy
`option_templates` 仍保持兼容。

Config `1.4.0` 把 expiry/moneyness arrays 作为 candidate grid，再按 inclusive
listing-moneyness band 与 maximum expiry 筛出实际挂牌合约。完整 liquidity rule 与 quote
model 保存在 private `market.option_chain_specs`；筛选不会随 daily spot 变化。其
side-specific deterministic Gaussian noise 只乘在 baseline bid/ask half-spread 上，
QuantLib BSM NPV 仍保存为 `mid = settlement_price`。

本阶段没有引入逐 option correlation。
共同 $\mathbb Q$/numeraire/rate path 和更严格的 coherent pricing model 合同仍属于下一阶段。

## Market snapshot、pricing model 与 pricing engine 的边界

下一阶段按“一个 snapshot 是一个已经实现的市场世界”设计。这里的市场事实是合约、
underlying 状态和报价，而不是生成这些报价时使用的模型。当前 schema 已通过
`(snapshot_id, date, option_id)` 主键保证同一个 snapshot 中同一合约同一天只有一条
`bid / ask / mid`；后续不能为了比较模型而在 `option_daily` 中增加 model 维度。

```text
private authoring DGP
  one generation model + one canonical generation engine + latent state/seed
                               |
                               v
public frozen market snapshot
  one contract/time/venue -> one bid/ask/mid
                               |
                 +-------------+-------------+
                 v                           v
task convention + IV solver          valuation/calibration runs
  one derived BSM IV                 BSM / Heston / Bates / local vol ...
                                     theoretical price + residual + diagnostics
```

具体边界如下：

- 对固定的 spot、strike、expiry、discount/dividend curves 和 BSM convention，可行价格
  区间内部的一条 European option mid 对应唯一的 BSM implied volatility；bid/ask 则
  对应一个 IV interval。违反价格边界时应标记为“无有效 IV”，而不是产生第二个 IV。
- 同一报价可以被 BSM、Heston、Bates 或 local-vol 等多个 valuation model 解释；各模型
  的 theoretical price、calibrated parameters、residual 和 diagnostics 属于 task/run
  output，不是第二份市场报价，也不能覆盖 snapshot 中的 mid。
- 每个 snapshot 只选一个 private canonical generation model 和一个 canonical generation
  engine 产生未舍入理论价格。有限差分或 Monte Carlo 等其他 engine 用于 cross-engine
  verification、容差检查和数值诊断，不产生并列的 market truth。
- 若要比较不同 data-generating models，应由相同私有 `market_scenario_id` 派生不同的
  `snapshot_id`，形成 counterfactual market worlds；不能在同一个 snapshot 中按 model
  重复同一合约的价格。
- Market snapshot 默认只保存报价，不保存 IV truth table。若为了性能缓存 IV，它必须是
  带 `source_quote_id`、`iv_convention_id`、输入曲线版本、solver method 和 tolerance 的
  derived analytics，且可从报价重建。
- 只有显式建模多个 venue 时，报价业务键才增加 `venue_id`；consolidated snapshot 仍然
  只保留一组 NBBO-like bid/ask，而不是用 model id 伪装 venue。

元数据需要进一步拆成 public market context 与 private authoring provenance：

| 边界 | 应保存的内容 | 建议字段 |
|:---|:---|:---|
| Public snapshot | valuation timestamp、calendar/day count、currency、discount/dividend/borrow curves、settlement 与 quote precision | `iv_convention_id` 由 task contract 引用 |
| Private authoring | P/Q generation model、canonical engine、latent parameters/state、seed/RNG、未舍入理论价格 | `generation_model_id`、`generation_engine_id` |
| Task/run output | 用于解释同一市场报价的 model/engine、校准参数、理论价格、残差、收敛信息 | `valuation_model_id`、`valuation_engine_id` |

当前 `solver_visible.pricing_metadata` 仍暴露 `physical_dynamics`、`pricing_dynamics`、
`pricing_model`、`pricing_engine`、`generator_version`、`seed` 和 `rng`。这是 smoke v1 的
过渡结构；在产生训练快照前应完成上述 public/private split，避免 Solver 直接读到 DGP
和随机性 provenance。以上是下一阶段合同，尚未改变现有 DuckDB schema 或 generator。

### Underlying correlation 与 derivative no-arbitrage 边界

当前实现的 $R$ 是 $\mathbb P$-measure underlying path DGP 的一部分，不表示 option
之间的相关矩阵，也不直接进入 option pricing engine。后续同币种 pricing context 仍按
共同 $\mathbb Q$、numeraire 与共享利率路径设计，每个资产的完整 option surface 由合法
coherent marginal pricing model 生成。以 money-market account 为例：

$$
B_t=\exp\left(\int_0^t r_s\,ds\right),
\qquad
V_t^{(i)}
=B_t\,\mathbb E^{\mathbb Q}\!\left[
\frac{X_i}{B_{T_i}}\middle|\mathcal F_t
\right].
$$

本文保留 $B_t$ 专门表示 money-market numeraire；underlying factor-loading matrix
统一记为 $\Lambda_t$，不再复用字母 $B$。

若未来做 $\mathbb Q$-measure multi-asset Monte Carlo，生成器仍先模拟 underlying
drivers：给定共享利率路径后，各 underlying 的 idiosyncratic base noises 相互独立，再按
固定 `driver_order` 使用 $R_t$ 耦合。`driver_order` 不包含 option ids；同一 underlying
上的所有 strike、expiry 和 call/put 都读取同一 underlying state path。相关结构为：

$$
R_t=R_t^\top,
\qquad
\operatorname{diag}(R_t)=\mathbf 1,
\qquad
R_t\succeq0,
\qquad
D_t=\operatorname{diag}\!\left(
1-\lVert\lambda_{1,t}\rVert^2,\ldots,
1-\lVert\lambda_{n,t}\rVert^2
\right)\succeq0,
\qquad
R_t=\Lambda_t\Lambda_t^\top+D_t.
$$

若 $\Lambda_t$ 的第 $i$ 行为 $\lambda_{i,t}^{\top}$，则对相互独立的
$\eta_t\sim N(0,I_k)$ 与 $\varepsilon_t\sim N(0,I_n)$，联合冲击按

$$
Z_t=\Lambda_t\eta_t+D_t^{1/2}\varepsilon_t
$$

生成，且 $\operatorname{Cov}(Z_t)=R_t$。

因此，`conditional independence given the shared interest-rate path` 只描述相关扰动前的
underlying baseline。只要 $R_t$ 有非零非对角元，最终 underlying 在仅给定利率路径后
就是 conditionally correlated。对 Heston、stochastic-rate 或 hybrid models，
`driver_order` 和 $R_t$ 覆盖 underlying 的 spot/volatility/rate driver blocks，并保持
各边际模型原有的内部相关结构。

这一阶段的实现合同如下：

- 每个同币种 snapshot 只声明一个 `risk_neutral_measure_id`、一个 `numeraire_id` 和一个
  `rate_path_id`；不能逐 option 混用 measure、discounting convention 或 rate path。
- 每个资产的全部 strike、maturity 与合约变体必须来自同一个 coherent marginal model
  snapshot；相关矩阵只耦合随机驱动，不改变边际参数或完整 option surface。
- Underlying dependence 合同至少冻结 `dependence_spec_id`、measure、underlying
  `driver_order`、$\Lambda_t$、$D_t$、$R_t$、matrix dtype、time grid 与 regime id；
  derivative contracts 不在矩阵中占行或列。
- 合法 $R_t$ 默认由固定的 $\Lambda_t\Lambda_t^\top+D_t$ 构造，再按相同 dtype、顺序与
  canonicalization 重放；symmetry、单位对角与 PSD 都由构造保证，不依赖带 tolerance 的
  事后 eigenvalue 判定。
- 每个 scenario 先生成一次共享利率路径，再从稳定 underlying driver id 派生 base streams，
  最后按冻结的 $\Lambda_t$ 与 $D_t$ 耦合；固定 config/seed 时 one-shot、append 和 replay
  必须一致。
- 所有 basket、index、spread 或其他多资产 payoff 必须读取同一个 joint underlying
  process 与 dependence snapshot，不能独立生成 derivative prices 后再做相关拼接。
- No-arbitrage 来自共同 $\mathbb Q$/numeraire、coherent marginal models 与一致联合定价，
  而不是来自 conditional independence 或相关矩阵。Put--call parity、strike
  monotonicity/convexity 和 calendar consistency 作为实现层 sanity checks 保留。

当前 scope 只包含同一币种与同一市场计价体系，不包含 FX、quanto、cross-currency
derivatives、多币种利率或 numeraire conversion。该方案保证模型内部一致性与可复现性，
但不声称风险中性测度唯一。

### Authoring realism roadmap

建议按下面顺序增强，而不是先堆叠多个 pricing engines：

1. **Option chain baseline（已完成）。** config `1.3.0/1.4.0` 已支持同一 expiry 下多个
   puts/calls 和 strikes、多个 expiries 的完整 $K\times T$ 网格；listing moneyness 已冻结
   为绝对 strike，合约跨 valuation dates 保持身份不变；候选网格按期限和 listing
   moneyness 做 immutable liquidity filtering。Weekly/monthly/quarterly 动态 listing/roll
   仍待 exchange profile 阶段实现。
2. **同币种 common-$\mathbb Q$ pricing context。** 当前 config `1.5.0` 已为 single-asset
   vanilla margins 冻结共同 $\mathbb Q$/money-market numeraire/rate-path identity、
   drift-only Girsanov mapping 和 private quote-IV audit。若联合 payoff 需要相关性，仍需
   增加只耦合 Q-measure underlying/model drivers 的 PSD $R_t$；derivative contracts
   不进入 correlation matrix。
3. **公共市场状态与私有 DGP 隔离。** 先完成上面的 metadata split，再扩充模型；否则
   更复杂的 Heston/Bates parameters 只会成为更明显的答案泄漏。
4. **曲线、日历与合约约定。** 用非 flat discount/dividend/borrow term structures、离散
   dividend/corporate actions、真实交易所 holidays/early closes、strike increments、
   multiplier、exercise/settlement style 和 valuation timezone 替换 smoke 简化项。
5. **报价微观结构与流动性状态。** Spread 应随 premium、moneyness、maturity、vega 和
   liquidity 改变；加入合法 tick、bid/ask size、zero bid、stale/missing quote 与 quality
   flags。Volume/open interest 应有跨日持续性，不能每天独立均匀抽样。
6. **扩展且跨日一致的 $\mathbb P/\mathbb Q$ latent state。** 在基础相关耦合上加入共同
   variance/regime/jump state、time-varying $R_t$ 以及 spot--volatility/rate driver blocks，
   并用明确的 risk premia 区分 $\mathbb P$-measure path dynamics 与 $\mathbb Q$-measure
   pricing dynamics；扩展联合分布时仍保持共同 measure、numeraire 和统一定价算子。
7. **多个 DGP family，但每个 snapshot 仍只有一个。** 保留当前 deterministic-
   time-varying-diffusion BSM 作为可解释 baseline，再分别生成 Heston、Bates/jump-diffusion、local-vol 等独立
   snapshot；每个模型先选 canonical analytic/FD engine，再用另一 engine 做 verifier。
8. **更长的历史与 regime coverage。** 从 5-day smoke 扩展到覆盖 calm/high-vol、earnings、
   jump 和 liquidity stress 的多时间段、多 seed scenario；最后再升级 underlying OHLC、
   overnight gap、volume 和 corporate-action 细节。

每次生成都应在未舍入理论价格、最终可见报价和 derived IV 三层分别保留 private audit，
但公开 snapshot 只暴露市场可观察量和任务明确允许的 conventions。这样既能保留可复现性，
又不会把生成模型误当成市场事实。

### 下一步实现计划

后续实现按依赖顺序推进；每一阶段都必须保持固定 config/seed 可重放、one-shot 与 append
结果一致、frozen snapshot 不可修改以及 revision 稳定。

| 阶段 | 模块 | 首次实现范围 | 完成标准 |
|:---|:---|:---|:---|
| 0（已完成） | `UnderlyingSimulator` / `UnderlyingDependenceSpec` | config `1.2.0` 以 $\Lambda/D/R$ 耦合 P-measure underlying close shocks；private authoring 表冻结合同 | one-shot/append 一致；旧 config 无回归；固定 spot 时 option quote 不受 $R$ 影响；option ids 不进入矩阵 |
| 1（已完成） | `OptionChainBuilder` | config `1.3.0/1.4.0` 声明 candidate expiry/moneyness grid、成对 call/put、static listing/roll、liquidity filter 与 strike increment；listing date 冻结绝对 strike；quote noise 只扰动 BSM half-spread | 合约 id 跨日稳定；one-shot、append 与 `sync-config` 一致；22 品种、65 日 public profile 通过；far-expiry/far-strike candidates 不挂牌 |
| 2（部分完成） | `CommonQPricingContext` / `CommonQScenario` / `QUnderlyingDependenceSpec` | config `1.5.0` 已声明唯一的 $\mathbb Q$/numeraire/rate path、Girsanov diffusion mapping 和 single-asset IV audit；下一步增加 Q-measure multi-asset driver dependence | vanilla contracts 无逐 option context override；option ids 不进入 $R_t$；后续联合 payoff 必须来自同一 joint underlying process |
| 3 | `MarketContext` / `AuthoringProvenance` split | Public 层只留 curves、calendar、timestamp、settlement 与 precision；private 层保存 generation model/engine、P/Q latent state、seed/RNG 和未舍入价格 | `solver_visible` 中不存在 DGP parameters、seed、RNG 或 generator engine；private audit 仍可完整重放 snapshot |
| 4 | `ExchangeProfile` 与 `QuoteModel` | 加入真实 holiday/early-close、timezone、expiry/settlement、strike/tick rules，以及随 moneyness、maturity、vega、premium、liquidity 变化的 spread；补充 size、stale/missing/zero-bid 与 quality flags | 所有公开报价符合声明的 exchange profile；volume/open interest 和 liquidity state 具有跨日持续性 |
| 5 | `CurveState` 与 richer driver blocks | 在 common-$\mathbb Q$ 与 measure-qualified underlying dependence 合同下支持非 flat curves、离散 dividend/corporate actions、共享 variance/regime/jump state、time-varying $R_t$ 及 spot--volatility/rate blocks | 同一 valuation timestamp 使用同一个 market-state/dependence version；边际内部相关结构不被 cross-asset coupling 改写；$\mathbb P/\mathbb Q$ 差异由显式 risk premia 描述 |
| 6 | `GenerationModel` / `GenerationEngine` registry | 先保留 deterministic-time-varying-diffusion BSM baseline，再分别加入 Heston、Bates/jump-diffusion、local-vol snapshot DGP；每个 snapshot 只选一个 canonical engine，另一个 engine 做 verifier | 不同 DGP 使用不同 `snapshot_id`；同一 snapshot/合约/时间仍只有一条市场报价；cross-engine error 在预设 tolerance 内 |
| 7 | Scenario campaign | 扩展日期跨度、seed、calm/high-vol/earnings/jump/liquidity-stress regimes，并升级 OHLC、overnight gap、volume 与 corporate actions | manifest 记录 scenario lineage；dataset split 按 snapshot/scenario 分组，避免同一 latent world 跨 train/test 泄漏 |

最小可审阅的提交顺序建议为：

1. 已完成 `UnderlyingSimulator`、配置校验、private dependence persistence 与
   append-invariance/derivative-boundary tests。
2. 已完成 `OptionChainBuilder`、配置校验、private chain/liquidity/quote spec、冻结 listing
   strike 与 contract-id/append-invariance tests。当前 public profile 为 22 品种 × 每品种
   4 个液态 expiries × 7 个液态 strikes × call/put；BSM mid 不变，quote noise 只扰动
   bid/ask half-spread。
3. 已为当前 vanilla profile 加入共同 $\mathbb Q$/numeraire/rate-path identity、显式
   P-to-Q mapping 和 canonical-mid IV audit；需要多资产联合 payoff 时，下一步新增独立的
   Q-measure underlying/model dependence spec，而不是 derivative correlation。
4. 完成 public/private metadata migration；此后 Heston/Bates 等新 DGP 才能接入，避免把
   richer latent parameters 暴露给 Solver。

因此 P-measure underlying correlation、static `OptionChainBuilder` 与 single-asset
common-$\mathbb Q$ baseline 已经完成；下一项联合定价工作是 Q-measure multi-asset
dependence。Pricing model registry 仍在市场数据结构、统一定价算子和权限边界建立之后推进。

常用的只读 DuckDB 查询集中保存在
[`snapshots/public/sql_query/`](snapshots/public/sql_query/README.md)，包括 snapshot 摘要、
underlying 时间序列、option chain、moneyness、pricing context 和 authoring audit。
每个 SQL 文件都在顶部提供可编辑的 `parameters` CTE，并显式固定结果排序。

## 仓库结构

```text
.
├── .venv/                         # 仓库本地 Python 虚拟环境，不提交 Git
├── authoring/
│   ├── configs/                   # Authoring job 的内部生成配置
│   └── templates/                 # 新 generator、snapshot 和 variant 的模板
├── configs/
│   ├── generators/                # 可发布的 generator 配置与版本声明
│   ├── task_space/                # 六维坐标范围和 compatibility registry
│   ├── mutations/                 # 受约束 mutation operators
│   ├── curricula/                 # stage、mastery bands 和采样 mixture
│   └── variants/                  # Solver 可见的 task/method/output contracts
├── datasets/
│   ├── generated/                 # 构建出的训练 JSONL/Parquet，不提交 Git
│   └── manifests/                 # 数据集版本、split 和来源清单
├── docs/
│   ├── examples/                  # 完整设计样例
│   └── *.md                       # 框架、架构和方法说明
├── environments/
│   ├── authoring/                 # Authoring 隔离环境定义
│   ├── solver/                    # Solver allowlist、denylist 与沙箱定义
│   └── verifier/                  # Trusted verifier 隔离环境定义
├── examples/
│   ├── submissions/               # 可公开的 canonical submission 样例
│   └── trajectories/              # 可公开的正向/负向 agent trajectory 样例
├── runs/                          # 本地 solver/verifier 运行结果，不提交 Git
├── schemas/                       # Snapshot、variant、trajectory、submission schemas
├── scripts/                       # venv、生成、校验和数据集构建入口脚本
├── snapshots/
│   └── public/                    # 小型、可公开且带 revision 的 DRAFT/FROZEN 快照
│       └── sql_query/             # 可复用、只读且显式排序的常用 DuckDB 查询
├── src/
│   └── synthetic_derivatives/
│       ├── authoring/             # 市场快照生成与冻结实现
│       ├── task_space/            # 六维 task grammar 与 compatibility
│       ├── mutation/              # 确定性 child task 与 lineage
│       ├── curriculum/            # 不改 task/reward 的 adaptive sampling
│       ├── solver/                # 受限环境中的公式、求根、Greeks 与拟合实现
│       ├── training/              # Trajectory 清洗、split 和 LLM 数据导出实现
│       └── verifier/              # 独立 package oracle 与 hard verifier 实现
└── tests/
    ├── fixtures/                  # 小型冻结测试输入和公开期望结构
    ├── unit/                      # task-space、mutation、curriculum 单元测试
    ├── integration/               # task manifest 与 authoring snapshot 边界
    ├── verifier_robustness/       # 验证错误数字、方法和 schema 必须被拒绝
    └── public/                    # Snapshot、contract、重放和端到端公开测试
```

`.agents/` 与 `.codex/` 是本地协作工具使用的目录，不属于项目的数据生产接口。

## 各目录的职责

### `authoring/`

存放出题端的配置与模板，不放 Solver 可见的数据。Authoring pipeline 使用 pinned QuantLib、generator config、seed 和 RNG 生成 `underlying_daily`、`option_daily` 与 `pricing_metadata`，随后冻结并记录 revision。内部 audit 与 oracle 输出应保持私有。

新增 authoring job 时可从 [QuantLib/BSM generator 模板](authoring/templates/quantlib_bsm_generator.template.json) 复制配置；字段约束和 DRAFT/freeze 用法见 [模板说明](authoring/templates/README.md)。

### `configs/`

保存可复现行为所需的声明式配置：

- `generators/` 描述模型、参数、随机数生成器、draw order、定价 engine 和 generator version。
- `task_space/` 描述 $L/P/M/A/D/R$ 六个轴与合法 product--model--method 组合。
- `mutations/` 描述允许改变的轴、单次最大变更轴数和方向约束。
- `curricula/` 描述 stage、20/60/20 replay/current/explore mixture 与 mastery 调度区间。
- `variants/` 描述某一道任务的 snapshot、金融约定、method IDs、数值顺序、舍入规则、Solver 权限和输出格式。

配置文件只描述合同，不存放实现代码或 hidden reference answer。

### `snapshots/`

保存生成后冻结的市场快照。`public/` 仅提交小型 demo；批量快照与私有快照不进入 Git。当前 public demo 使用 DuckDB，配套内容包括：

- `quantlib_bsm_smoke_v1.duckdb`：market、metadata 和 solver-visible views；
- `quantlib_bsm_smoke_v1.manifest.json`：snapshot/config 版本标识、revision 与行数；
- `sql_query/`：只读查询模板，不包含 canonical answer 或 hidden oracle。

IV、Greeks、smile、surface、VaR 和 ES 是从统一快照派生的任务结果，不在这里维护彼此独立的 truth tables。

### `schemas/`

定义跨边界的数据结构，包括 market snapshot、task variant、agent trajectory、Solver submission、verification report 和 dataset record。Schema 用于在进入下一阶段前拒绝缺字段、错单位、错顺序或非法数值。

### `src/synthetic_derivatives/`

项目的 Python 源码根目录。权限边界模块与训练编排模块分开：

- `authoring/`：允许使用 QuantLib，负责生成、质量门控、冻结与 revision。
- `task_space/`：只判断六维坐标和 task family 是否兼容，不生成任务、不决定采样。
- `mutation/`：从不可变母题生成确定性 child task 和 lineage，不读取模型表现。
- `curriculum/`：根据 stage 与 `pass@1` diagnostics 计算采样权重，不修改 frozen task 或二值 hard reward。
- `solver/`：只使用合同允许的基础原语，自行实现指定计算方法。
- `verifier/`：不得导入 Solver 的定价实现；使用独立 package oracle 复算并做 canonical exact equality。
- `training/`：把通过校验的任务、trajectory、证据和 outcome 转换为 LLM 训练记录，并按 snapshot 分组切分数据，避免泄漏。

生产部署时，四个子包不会共享同一个运行权限；代码分目录只是 repo 层面的组织方式。

### `environments/`

保存三个隔离环境的依赖与容器定义。Authoring/verifier 可以安装固定版本的金融包，Solver 环境只安装 allowlist 依赖，并禁用网络、动态安装和 hidden verifier 访问。

### `datasets/`

`generated/` 存放可重建的训练集，因此被 `.gitignore` 排除；`manifests/` 存放应提交的版本信息、数据来源、snapshot grouping 和 train/validation/test split。训练集不得包含 hidden oracle、hidden tests 或 verifier 私有输出。

### `examples/`

保存能够公开审阅的小型产物：canonical submission、正向 trajectory、first-error 负轨迹等。示例用于解释合同，不能被生产 hidden verifier 当作唯一 oracle 来源。

### `tests/`

- `fixtures/` 提供稳定的小型输入。
- `public/` 检查公开 schema、snapshot identity、method contract 和端到端接口。
- `unit/` 检查 task grammar、受约束 mutation、lineage 与 curriculum sampling。
- `integration/` 检查 task manifest 仅按 id/revision 引用 authoring snapshot。
- `verifier_robustness/` 主动修改末位数字、单位、method ID、行顺序或 import，确认 hard verifier 必须失败；该目录与正式 task mutation engine 无关。

生产 hidden tests 应放在 Solver 无法读取的独立环境中，不提交到公开仓库。

### `scripts/` 与 `runs/`

`scripts/` 提供统一、非交互的项目入口；`runs/` 只保存本地临时运行结果、日志和报告。`runs/` 中的内容可以删除并重新生成，不作为数据集或 oracle 的可信来源。

## 虚拟环境约定

本项目只在仓库本地 `.venv` 中运行。命令统一使用：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
```

`.venv/` 不提交 Git；可复现性由 Python 版本声明和锁定依赖文件保证。

## 设计文档

- [DuckDB + QuantLib Authoring Pipeline](docs/authoring_pipeline.md)
- [金融衍生品联合模拟、Task Mutation 与 Curriculum 最终设计](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
- [合成期权链 IV、Greeks 与 Smile Agent Trajectory 样例](docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)
