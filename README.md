# FinMaths Synthetic Data Demo

面向 LLM 训练的可重放合成金融衍生品数据项目。项目把市场数据生成、任务定义、模型解题、
独立校验和训练数据导出划分为不同权限边界；固定完整配置、版本、seed、随机流与数值约定
后，同一 market snapshot 可以确定性重放。

当前可运行主线使用 QuantLib 生成物理测度 $\mathbb P$ 下的 underlying 路径和风险中性
测度 $\mathbb Q$ 下的 European option 报价，再由 DuckDB 事务化、增量地保存 snapshot。
仓库内置的 public metals snapshot 已冻结，包含 22 个 underlying、65 个 business dates、
1,232 个静态期权合约和 60,368 条有效期内报价。

## 当前状态

| 能力 | 状态 | 说明 |
|:---|:---:|:---|
| Authoring pipeline | 已实现 | 支持 create、range sync、append、NOOP、quality gates、revision、manifest 与 freeze。 |
| $\mathbb P$-measure underlying simulation | 已实现 | 分段线性 drift/volatility 按真实日历区间精确缩约；factor loading 只耦合 underlying shocks。 |
| Single-asset $\mathbb Q$ pricing | 已实现 | 共同 measure/numeraire/rate-path identity；QuantLib 生成 canonical mid，authoring 不反解或持久化 IV 答案。 |
| Static option chain | 已实现 | 固定 listing strike、到期日/价内外筛选、可重放 bid/ask spread noise。 |
| Task space / mutation / curriculum | 最小版本已实现 | 六维 compatibility registry、确定性 lineage 与 adaptive sampling weights。 |
| F2A arbitrage-finding | Frozen parent 与合同骨架已落地，runtime 未实现 | Tick-aligned config 1.6 parent 已冻结；当前 public variant 仍是 catalogue v2、禁用 calendar。Catalogue v3、type-signature selector、child/oracle/Solver/verifier 和 dataset 尚未落地。 |
| Solver / trusted verifier / dataset export | 未实现 | 目录边界已预留，但还没有可运行实现。 |
| 多资产 $\mathbb Q$ dependence 与 joint payoff | 未实现 | Basket/index/spread 不能由当前 single-asset baseline 推断或定价。 |

当前的 volatility mapping 是一个明确的 baseline 假设：Girsanov change of measure 只改变
drift，并令确定性扩散函数满足 $\sigma_Q(t)=\sigma_P(t)$。这不是“physical volatility
按定义等于 implied volatility”，也不是可推广到随机波动率、局部波动率或 jump model 的
通用结论。

## 快速开始

要求 Python `3.12.x`、`make`，并在类 Unix 环境中运行。依赖锁定为 QuantLib `1.39`、
DuckDB `1.5.5` 和 pytest `8.4.1`。

```bash
make install
make snapshot-summary
make test
```

`snapshot-summary` 只读 checked-in 的 frozen snapshot。若要生成或追加数据，默认目标位于
`/tmp`，不会改写仓库内的 public DuckDB：

```bash
make smoke
make append-day
```

也可以显式指定临时数据库：

```bash
DATABASE=/tmp/my-synthetic-market.duckdb make smoke
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/my-synthetic-market.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  summary
```

常用的只读 SQL 位于
[`snapshots/public/sql_query/`](snapshots/public/sql_query/README.md)；active development snapshot 的
本地数据库说明位于
[`snapshots/generated/`](snapshots/generated/README.md)。注意：其 `sql_query/` 当前显式面向
F2A tick-aligned frozen parent，不是默认 active v3 数据库。完整 CLI、schema、
增量规则与冻结语义见 [Authoring Pipeline](docs/authoring_pipeline.md)；authoring 包的模块边界
见 [`src/synthetic_derivatives/authoring/README.md`](src/synthetic_derivatives/authoring/README.md)。

常用入口及写入范围：

| 命令 | 是否写数据 | 目标 |
|:---|:---:|:---|
| `make snapshot-summary` | 否 | 只读 checked-in legacy v3 public snapshot。 |
| `make smoke` | 是 | 用 config 1.6/v4 在 `DATABASE`（默认 `/tmp`）创建或同步 DRAFT。 |
| `make append-day` | 是 | 向同一个 v4 DRAFT 追加一个 business date。 |
| `make test` | 仅写临时目录 | 运行完整 pytest suite。 |
| `scripts/replay_snapshot.py --reference ...` | 是 | 在临时路径重放，并只与显式给出的同 identity reference 比较。 |

默认 active development 数据库仍是
`snapshots/generated/quantlib_bsm_metals_option_chain_smoke_v1_20260807.duckdb`。它是
legacy v3 `DRAFT`，在当前 pipeline 下只读；`make smoke` 使用独立 v4 identity，不会更新它。

## 阅读导航

- 想运行项目：从上面的“快速开始”和 [public tests](tests/public/README.md) 开始。
- 想修改 generator：先读 [authoring package](src/synthetic_derivatives/authoring/README.md) 和
  [unit-test invariants](tests/unit/README.md)。
- 想理解完整数学与训练框架：读
  [框架设计文档](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)。
- 想继续实现 F2A：先读
  [F2A 当前状态、数学合同与实施顺序](src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md)。
- 想查看可执行查询：读 [public SQL 说明](snapshots/public/sql_query/README.md)；只有任务显式选择
  F2A parent 时才使用 [generated SQL 说明](snapshots/generated/sql_query/README.md)。

## 设计流程

```text
已实现：Authoring
  -> 生成并冻结 market snapshot（correlated profile 同时冻结 underlying dependence spec）
  -> 注册六维 task variant 与 compatibility decision
  -> Mutation Engine 生成带 lineage 的 candidate pool
  -> Curriculum Scheduler 按 mastery 选择训练分布

规划中：
  -> Solver 手工计算 IV / Greeks / smile
  -> Trusted verifier 独立复算并执行 exact equality
  -> 记录 ORM outcome 与 agent trajectory
  -> 导出 LLM training dataset
```

Authoring、Solver 和 Trusted verifier 是三个不同的权限边界：

- Authoring 可以使用固定版本的 QuantLib 和固定 seed 生成市场数据。
- Solver 只能读取公开快照与合同，不得调用现成的定价、IV、Greeks 或 smile API。
- Trusted verifier 可以使用固定版本的金融与数值包独立复算，但不能向 Solver 暴露 oracle 或 hidden tests。

六维坐标 $\tau=(L,P,M,A,D,R)$ 分别表示 reasoning、product、model、numerical method、
data/tool 和 risk output。坐标必须先通过 compatibility registry；不能把六个轴无条件做
Cartesian product。各 level 的完整含义见
[六维 Task Grammar](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md#六维-task-grammar-与难度空间)。

上述六维流程是当前可运行 v1 baseline。F2A 不原地改写 v1 schema、registry、manifests
或 deterministic child IDs；它使用并行的 task-space v2，在同一顶层 repo 设计中增加
string enum F 轴。F2A 市场数据 child 由 Authoring 边界物化和冻结；Mutation 层只生成
immutable spec、identity 和 lineage，Trusted verifier 再从 public child 独立复算 ORM truth。

## Public metals snapshot

仓库内置历史 [22-metal generator 配置](configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)
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
half-spread。这份 schema 2.4 public snapshot 仍保留当时 authoring 生成的 legacy
IV audit；当前 pipeline 不再生成该答案表行。

查看已冻结 public snapshot：

```bash
make install
make snapshot-summary
```

当前 authoring 已去掉 IV answer generation，因此使用新的 `v4 / 0.8.0`
identity 生成到新文件；不要使用旧 v3 identity 改写 checked-in snapshot：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v4.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
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
scalar 写法继续支持，并等价于 constant function。当前 successor metals config
[`1.6.0`](configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json) 由
[sampling script](scripts/sample_physical_dynamics.py) 为每只 underlying 独立
抽取 sampling seed、node-offset grid、drift phi/std、log-vol phi/std 和最终节点值。所有
分布都有硬边界；22 组 offsets 和每一类 hyperparameter realized value 均强制互不相同。
脚本把 global/per-underlying seeds、bounds、realized parameters 和 nodes 全部冻结到 config；
重放 snapshot 时不会重新抽样。核心配置片段如下；完整 provenance 以 checked-in config
为准。

```json
{
  "schema_version": "1.6.0",
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
    "volatility_mapping": "same_deterministic_diffusion"
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

当前 config `1.6.0` 沿用一个明确、局部的模型假设：在这个 deterministic-diffusion
GBM baseline 中，Girsanov change of measure 只把 drift 从 $\mu_P(t)$ 改为 $r-q$，扩散函数
保持 $\sigma_Q(t)=\sigma_P(t)$。这不是“physical volatility 按定义等于 IV”，也不是通用
的 volatility-risk-premium 结论。European option 在 $[t,T]$ 使用

$$
\sigma_{Q,\mathrm{eff}}(t,T)
=\sqrt{\frac{1}{T-t}\int_t^T\sigma_Q^2(u)\,du},
$$

由 QuantLib 计算未舍入理论价，再将 mid 量化到 8 位。Authoring 不对这个可见
canonical mid 反解 IV，也不持久化 derived IV 或 solver status。已有 schema 2.4 snapshot
中的 `market.option_pricing_audit` 是保留的 legacy authoring 数据，没有 solver-visible view；
新任务的 canonical IV 应由 trusted verifier 从 frozen visible price 独立重算。

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

本阶段没有引入逐 option correlation。Config `1.5.0+` 已声明 single-asset vanilla margins
共享的 $\mathbb Q$/numeraire/rate-path identity；尚未实现的是 Q-measure multi-asset driver
dependence 与 joint payoff pricing，二者不能由 P-measure correlation 代替。

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
2. **同币种 common-$\mathbb Q$ pricing context。** 当前 config `1.6.0` 已为 single-asset
   vanilla margins 冻结共同 $\mathbb Q$/money-market numeraire/rate-path identity 和
   drift-only Girsanov mapping；authoring 不再生成 quote-IV 答案。若联合 payoff 需要相关性，仍需
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

每次生成应保留价格模型、数值约定和最终可见报价的可重放 provenance；derived IV
不属于 authoring snapshot。它由 trusted verifier/private oracle 从 frozen task input 计算，
不得进入 Solver-visible snapshot。

### 下一步实现计划

后续实现按依赖顺序推进；每一阶段都必须保持固定 config/seed 可重放、one-shot 与 append
结果一致、frozen snapshot 不可修改以及 revision 稳定。

| 阶段 | 模块 | 首次实现范围 | 完成标准 |
|:---|:---|:---|:---|
| 0（已完成） | `UnderlyingSimulator` / `UnderlyingDependenceSpec` | config `1.2.0` 以 $\Lambda/D/R$ 耦合 P-measure underlying close shocks；private authoring 表冻结合同 | one-shot/append 一致；旧 config 无回归；固定 spot 时 option quote 不受 $R$ 影响；option ids 不进入矩阵 |
| 1（已完成） | `OptionChainBuilder` | config `1.3.0/1.4.0` 声明 candidate expiry/moneyness grid、成对 call/put、static listing/roll、liquidity filter 与 strike increment；listing date 冻结绝对 strike；quote noise 只扰动 BSM half-spread | 合约 id 跨日稳定；one-shot、append 与 `sync-config` 一致；22 品种、65 日 public profile 通过；far-expiry/far-strike candidates 不挂牌 |
| 2（部分完成） | `CommonQPricingContext` / `CommonQScenario` / `QUnderlyingDependenceSpec` | config `1.6.0` 已声明唯一的 $\mathbb Q$/numeraire/rate path 和 Girsanov diffusion mapping，authoring IV audit 已移除；下一步增加 Q-measure multi-asset driver dependence | vanilla contracts 无逐 option context override；option ids 不进入 $R_t$；后续联合 payoff 必须来自同一 joint underlying process |
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
3. 已为当前 vanilla profile 加入共同 $\mathbb Q$/numeraire/rate-path identity 与显式
   P-to-Q mapping；config `1.6.0` 已把 authoring-time IV solver/answer 移出 snapshot。
   需要多资产联合 payoff 时，下一步新增独立的 Q-measure underlying/model dependence
   spec，而不是 derivative correlation。
4. 完成 public/private metadata migration；此后 Heston/Bates 等新 DGP 才能接入，避免把
   richer latent parameters 暴露给 Solver。

因此 P-measure underlying correlation、static `OptionChainBuilder` 与 single-asset
common-$\mathbb Q$ baseline 已经完成；下一项联合定价工作是 Q-measure multi-asset
dependence。Pricing model registry 仍在市场数据结构、统一定价算子和权限边界建立之后推进。

常用的只读 DuckDB 查询集中保存在
[`snapshots/public/sql_query/`](snapshots/public/sql_query/README.md)，包括 snapshot 摘要、
underlying 时间序列、option chain、moneyness、pricing context 和 authoring audit。
每个 SQL 文件都在顶部提供可编辑的 `parameters` CTE，并显式固定结果排序。

## F2A arbitrage-finding 状态与兼容计划

F2A 是在现有 single-asset common-$\mathbb Q$ BSM baseline 上的并行 task-authoring 支线，
不改变前述 market-realism roadmap 的阶段顺序，也不声称已实现 Solver、Trusted verifier
或 dataset export。完整数学、交易和 ORM 合同见
[F2A Arbitrage-Finding Agent Task 计划](src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md)。

### 当前落地状态

| 部分 | 当前状态 |
|:---|:---|
| Clean parent | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1 / FROZEN` 已物化；报价与 spot 都按 `0.01 USD` tick 对齐。 |
| 声明式边界 | Generator、public variant skeleton、point-mutation config、private authoring config、七维 registry/curriculum 和 v2 schemas 已落位，并有 repo-contract tests。 |
| 待版本化合同 | 当前 variant 仍是 `bsm-f2a-candidate-catalogue-v2` 且 `calendar_family = null`；目标 catalogue v3 的 two-expiry bridge、pathwise cell/tail certificate 和公开 operation order 尚未写回合同。 |
| 待更新 selector | 当前 private authoring config 仍是 positive/negative balance；目标设计要求 `000` control 加七种 positive type signatures，以及 transaction-cost-aware active/inactive 双边 guard。 |
| 待实现 runtime | 七维 v2 dispatch、point mutation、child materializer、独立 oracle、Solver/verifier、受限 Solver image、task/split manifests 和 training export 均未实现；尚无 F2A child 或 dataset。 |

因此，frozen parent 只证明 clean source 已准备好，不代表端到端 F2A 已完成。在 catalogue v3、
calendar cashflow proof tests 和 signature selector 被正式 versioned 前，不得物化含 `calendar`
truth 的 child，也不得把目标设计中的七种 positive signatures 描述成当前可运行能力。

### 套利结论的作用域

F2A 的 ORM truth 是**有限 catalogue 范围内**的结论：Trusted verifier 从 public child 和 public
variant 重算每个 canonical candidate 的可执行净证书，再输出
`(arbitrage_opportunity, arbitrage_type)`。它必须与以下更宽或更窄的判断分开：

- 相对某个 BSM 或其他选定模型的 mispricing/model inconsistency；
- 考虑 bid/ask、费用、funding、carry 和 settlement 后的可执行 static/semi-static arbitrage；
- F2A 冻结有限 catalogue 内是否存在 positive exact certificate；
- 完整 admissible strategy class 中的 full-market dynamic/replication arbitrage。

一个 quote 偏离 BSM 只直接说明 model inconsistency；F2A 返回 `false/[]` 也只说明当前 public
variant 的有限 catalogue 没有 positive candidate，不能写成全市场“无套利”。一次 point mutation
可能同时激活任意多个已启用 family，最终 type array 必须从 public child 全量重扫，不能从 private
mutation intention 或 requested signature 复制。

### 不变的项目边界

- 现有 `snapshots/public/quantlib_bsm_smoke_v1.duckdb` 保持 byte-identical，只作为 legacy replay
  source。F2A 要求 `0.01 USD` minimum-price increments，因此使用新 generator config、
  generator version 和 snapshot identity 生成 tick-aligned clean parent。
- Authoring 仍是唯一可物化、质量门控、revision 和 freeze market DuckDB 的边界。
  `mutation/` 只生成确定性 point-mutation spec、child identity inputs 和 lineage record，不直接
  读写 DuckDB、不运行 oracle。
- Child snapshot 继续表示一个已物化市场世界，使用标准 `bid/ask/mid`，不新增
  第四个市场价格 `task_price`。BSM method、deterministic Q-volatility function、execution fee、
  candidate catalogue 和 ORM schema 是 public task convention，由 variant config 声明，不冒充为第二份
  market DGP。
- F2A 不是零交易成本任务：option 按方向使用 bid/ask，并按 public execution contract 对每条腿
  收取 `0.50 USD/contract/side`；underlying 每次成交按绝对 traded notional 收取单边 `5 bps`，
  cash account 保持无摩擦。非零 underlying cost 下不把 frictionless BSM dynamic replication
  当作可执行套利策略。
- 费用不是数值 tolerance。每个 candidate 必须按真实 option 腿数、方向和 underlying 交易时点
  逐项收费；改变费用只能移动各 family 的经济激活阈值，不能作为随样本调整 label 的旋钮。
- Trusted verifier 只从 public child、public task/variant contract 和 submission 独立重算
  `(arbitrage_opportunity, arbitrage_type)`；它不导入 Solver，不读 parent、private lineage、mutation
  intention 或 stored label。
- Solver 采用 allowlist-only image；variant 同时列出已知 banned imports 和 capability-based deny。
  禁止范围包括 QuantLib、py_vollib、mibian、rateslib 等现成 option pricing/IV/Greek/surface
  package，以及预制的 static/calendar arbitrage scanner；仅靠 package 名黑名单不构成隔离保证。
- Training 只导出已验证的 public task、trajectory、outcome 和 snapshot grouping，不打包
  private lineage、hidden diagnostics 或 oracle traces。

### 版本兼容

当前六维 task-space/schema/config 是 v1 public contract，不原地增加 string F 字段。F2A 计划
使用并行 v2：

```text
v1: (L, P, M, A, D, R)              # 现有 manifests 和 identity 保持不变
v2: (L, P, M, A, D, R, F)           # F 是 string enum，F2A task 使用 F="F2A"
```

V2 ordinary rules 显式使用 `F0`，F2A 使用 dedicated compatibility rule。Task-space registry、
mutation identity 和 curriculum selectors 必须一起增加 v2 dispatch；F enum 转移使用
`direction=any`，不对 `F0/F2A/...` 做数值比较。旧 v1 task 不在加载时隐式改写为
七字段，以避免改变 serialization 与 deterministic child ID。

### 配置和实现归属

F2A 不增加 `configs/arbitrage/` 或 `src/synthetic_derivatives/arbitrage/` 这类新顶层分区，
而是按现有 repo 责任边界归属：

| 现有边界 | F2A 计划中的责任 |
|:---|:---|
| `authoring/configs/` | Private parent selectors、requested type-signature balance、active/inactive guards、execution-profile feasibility policy、split 与 smoke/pilot 规模。 |
| `configs/generators/` | Tick-aligned clean parent DGP、minimum increments、rounding 和新 generator/snapshot identity。 |
| `configs/variants/` | Solver-visible Q/numeraire/rate-path、BSM method、execution cost、三类 candidate catalogue、calendar certificate 与 output contract。 |
| `configs/mutations/` | Logical option-price/spot operator IDs、integer-tick grids 和稳定枚举顺序。 |
| `configs/task_space/` | 保留 v1，并行增加七维 registry v2 和 F2A compatibility rule。 |
| `configs/curricula/` | 保留 v1，并行增加 v2；旧 stages 路由到 F0，另加 F2A stage。 |
| `schemas/` | V2 difficulty/task/mutation、F2A private-lineage 形状、trajectory 和 submission schemas。 |
| `authoring/` | Read-only parent selection、cost-adjusted signature/tick search、copy-on-write child materialization、market field 派生、quality gates、private lineage 和 freeze。 |
| `mutation/` | 纯 immutable spec、identity 与 lineage records；不持有 DB write 或 oracle 权限。 |
| `solver/` | 从 public child/variant 手工枚举 cross-sectional、cross-asset 和已启用的 calendar candidates，并生成 trajectory/submission。 |
| `verifier/` | 独立重算 candidate cashflow/certificate 与 canonical type bitmask，完成 submission projection 和 exact equality。 |
| `training/` | Verified records、snapshot-grouped split 和 JSONL/Parquet export。 |
| `scripts/` | 统一非交互 materialization/verification/manifest 编排入口；不存业务数学。 |

上述路径边界已经确定，但 checked-in config 只锁定了部分合同。Public variant 仍须补齐
catalogue v3 的 cross-sectional/cross-asset exact formulas、transaction-cost-aware calendar bridge、
pathwise cell/tail certificate 与 operation order；private authoring config 仍须把 positive/negative
balance 升级为 cost-adjusted type-signature policy。F2A Python 业务模块、child snapshots 和训练
artifacts 尚未创建。Repo 不提交空的 generated/private artifact 目录。

### 目标 type signatures 与 calendar catalogue

Catalogue v3 启用后，canonical type order 固定为 `cross-sectional`、`cross-asset`、`calendar`。
Authoring feasibility set 目标覆盖一个 clean control 与七种 positive signatures：

| Signature `(X, U, T)` | Canonical `arbitrage_type` |
|:---:|:---|
| `000` | `[]` |
| `100` | `["cross-sectional"]` |
| `010` | `["cross-asset"]` |
| `001` | `["calendar"]` |
| `110` | `["cross-sectional", "cross-asset"]` |
| `101` | `["cross-sectional", "calendar"]` |
| `011` | `["cross-asset", "calendar"]` |
| `111` | `["cross-sectional", "cross-asset", "calendar"]` |

每个 positive child 仍至多改变一个 logical quote 或 spot point。Selector 按固定 target/sign/tick
顺序搜索，只接受 realized signature 精确等于 requested signature、active families 通过 positive
guard 且 inactive families 通过 negative guard 的第一个 child；不可达时 deterministic skip，不能
随机 retry、扩大 tick grid 或临时修改 fee profile。Spot-only mutation 不能直接激活 option-only 的
cross-sectional family。

目标 calendar family 不是“同 strike 的长期限 raw price 应更高”，也不是 quote 与 BSM theoretical
value 的偏差。它是 catalogue-scoped 的 two-expiry terminal-spot bridge：option 在 valuation time
按 bid/ask 建仓并持有到 settlement，underlying 在 `t`、较早到期 `T1` 和较晚到期 `T2` 交易，
`T1` 只允许一次公开、有限、state-contingent rebalance。Verifier 必须把每次 `5 bps` underlying
cost 和每条 option fee 纳入 cashflow，并用 piecewise-affine cell vertices、one-sided boundaries
与 tail slopes 证明 terminal wealth 在完整正状态域上非负；有限 spot grid 或 Monte Carlo sampling
都不是 exact certificate。

### Snapshot、manifest 与训练 artifact

```text
snapshots/generated/f2a/parents/<parent_snapshot_id>/parent.duckdb
snapshots/generated/f2a/parents/<parent_snapshot_id>/parent.manifest.json
snapshots/generated/f2a/children/<child_snapshot_id>/child.duckdb
snapshots/generated/f2a/children/<child_snapshot_id>/child.manifest.json
snapshots/private/f2a/<task_id>/lineage.json

datasets/manifests/tasks/f2a/<task_id>.json
datasets/manifests/splits/f2a_v1.json
datasets/generated/f2a/<dataset_id>.jsonl
```

`snapshots/generated/` 与 `snapshots/private/` 已被 `.gitignore` 排除。Public task manifest 只引用
child snapshot id/revision、registry/rule 和 public contract IDs，不复制 DuckDB、parent、before/after、
oracle 或 reference answer。Private lineage 实例不放入应提交的 `datasets/manifests/`；
`datasets/generated/` 只保存最终可重建的训练 JSONL/Parquet。

当前 clean parent 是
`DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2`，位于上述 `parents/` 结构，状态为
`FROZEN` revision `1`。它只作为 immutable mutation source；不能原地追加或修改。
完整 schema、所有字段、表关联、eligible mutation slices 和可抽取的 LLM instructions 见
[`F2A parent DuckDB 数据字典`](snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md)。

每个 child 从 DRAFT 开始，经 authoring gates 后以新 `snapshot_id/revision` 冻结。Public
task/child IDs 不编码 operator、target、requested/realized signature 或 mutation status。Solver bundle
只挂载 public child、public task/variant contract 与 trajectory/submission schemas。

### 计划中的验收分层

- `tests/unit/`：v1/v2 schema/registry/curriculum、pure mutation spec、tick materialization、option fee、
  underlying 单边 `5 bps`、三类 candidate mathematics、calendar cell/tail certificate、signature
  selector 与 canonical type order。
- `tests/integration/`：parent read-only、child new identity/freeze/replay、task manifest 仅引用 child
  snapshot，private lineage 不影响 public-child truth。
- `tests/public/`：public child/variant/schema 与 child -> submission -> verifier smoke，覆盖 `000`
  和七种 positive signatures。
- `tests/verifier_robustness/`：错 bool、缺失/错序 `arbitrage_type`、多交 `maximal_spread`、错单位、
  contract ID 或 snapshot revision 必须被拒绝。
- Production hidden cases 仍只存在 Solver 无法读取的 verifier 环境，不提交到公开仓库。

## 仓库结构

```text
.
├── .venv/                         # 仓库本地 Python 虚拟环境，不提交 Git
├── authoring/
│   ├── configs/                   # Authoring job 的内部生成配置
│   └── templates/                 # 新 generator、snapshot 和 variant 的模板
├── configs/
│   ├── generators/                # 可发布的 generator 配置与版本声明
│   ├── task_space/                # 六维 v1 与并行七维 v2 compatibility registries
│   ├── mutations/                 # 坐标 mutation 与 F2A integer-tick point operators
│   ├── curricula/                 # v1/v2 stage、mastery bands 和采样 mixture
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
│   ├── generated/                 # 本地开发/F2A 派生快照；仅 README/通用 SQL 提交 Git
│   │   └── sql_query/             # F2A frozen parent 的参数化只读查询
│   └── public/                    # 小型、可公开且带 revision 的 DRAFT/FROZEN 快照
│       └── sql_query/             # 可复用、只读且显式排序的常用 DuckDB 查询
├── src/
│   └── synthetic_derivatives/
│       ├── authoring/             # 市场快照生成与冻结实现
│       ├── task_space/            # 六维 v1 runtime；七维 v2 runtime 待实现
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
- `task_space/` 保留 $L/P/M/A/D/R$ 六轴 v1，并行描述含 string enum $F$ 的七轴 v2 与合法组合。
- `mutations/` 描述允许改变的坐标轴，以及 F2A logical point mutation 的 integer-tick grid、顺序和 domain gates。
- `curricula/` 分别描述 v1/v2 stage、20/60/20 replay/current/explore mixture 与 mastery 调度区间。
- `variants/` 描述某一道任务的 snapshot、金融约定、method IDs、数值顺序、舍入规则、Solver 权限和输出格式。

配置文件只描述合同，不存放实现代码或 hidden reference answer。

### `snapshots/`

保存市场快照。`public/` 仅提交小型 demo；`generated/` 保存本地 DRAFT/派生快照，
其 DuckDB、manifest 和 lineage 不进入 Git，但目录说明与参数化只读 SQL 可提交。当前 active
development snapshot 的 identity、visibility boundary 和查询目录见
[`snapshots/generated/README.md`](snapshots/generated/README.md)。当前 public demo 使用 DuckDB，配套内容包括：

- `quantlib_bsm_smoke_v1.duckdb`：market、metadata 和 solver-visible views；
- `quantlib_bsm_smoke_v1.manifest.json`：snapshot/config 版本标识、revision 与行数；
- `sql_query/`：只读查询模板，不包含 canonical answer 或 hidden oracle。

IV、Greeks、smile、surface、VaR 和 ES 是从统一快照派生的任务结果，不在这里维护彼此独立的 truth tables。

### `schemas/`

定义跨边界的数据结构，包括 market snapshot、task variant、agent trajectory、Solver submission、verification report 和 dataset record。Schema 用于在进入下一阶段前拒绝缺字段、错单位、错顺序或非法数值。

### `src/synthetic_derivatives/`

项目的 Python 源码根目录。权限边界模块与训练编排模块分开：

- `authoring/`：允许使用 QuantLib，负责生成、质量门控、冻结与 revision。
- `task_space/`：当前 runtime 只判断六维 v1 坐标；并行七维 v2 config/schema 已落位，runtime dispatch 待实现。
- `mutation/`：从不可变母题生成确定性 child task 和 lineage，不读取模型表现。
- `curriculum/`：根据 stage 与 `pass@1` diagnostics 计算采样权重，不修改 frozen task 或二值 hard reward。
- `solver/`：只使用合同允许的基础原语，自行实现指定计算方法。
- `verifier/`：不得导入 Solver 的定价实现；使用独立 package oracle 复算并做 canonical exact equality。
- `training/`：把通过校验的任务、trajectory、证据和 outcome 转换为 LLM 训练记录，并按 snapshot 分组切分数据，避免泄漏。

生产部署时，四个子包不会共享同一个运行权限；代码分目录只是 repo 层面的组织方式。

### `environments/`

保存三个隔离环境的依赖与容器定义。Authoring/verifier 可以安装固定版本的金融包，Solver 环境只安装 allowlist 依赖，并禁用网络、动态安装和 hidden verifier 访问。
Solver 的 distribution、direct-import、NumPy/Pandas API、DuckDB query adapter 和 enforcement
验收合同统一记录在 [Solver Environment Allowlist](environments/solver/README.md)。当前该文档
已冻结目标设计，但 dependency lock 和 runtime enforcement 尚未完成。

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
- [F2A Arbitrage-Finding Agent Task 兼容计划](src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md)
- [金融衍生品联合模拟、Task Mutation 与 Curriculum 最终设计](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
- [合成期权链 IV、Greeks 与 Smile Agent Trajectory 样例](docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)
