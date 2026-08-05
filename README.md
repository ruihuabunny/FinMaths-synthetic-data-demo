# FinMaths Synthetic Data Demo

面向 LLM 训练的确定性合成金融衍生品数据项目。项目目标是把市场数据生成、任务定义、模型解题、独立校验和训练数据导出分成清晰的边界，并保证每个样本都可以通过固定配置与 seed 重放。

当前已完成 authoring pipeline 的第一版：使用 QuantLib 生成 underlying 路径和 European option 报价，通过 DuckDB 事务增量写入 snapshot。在此基础上，仓库已加入最小可运行的六维 `task_space` registry、受约束 `mutation` engine 和 adaptive `curriculum` scheduler；它们只按 id/hash 引用 snapshot，不修改 authoring 数据。Solver、verifier 与训练数据构建尚未实现。

## 设计流程

```text
Authoring
  -> 生成并冻结 market snapshot
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

## Authoring smoke test

仓库内置 [QuantLib generator 配置](configs/generators/quantlib_bsm_smoke_v1.json) 和 [DuckDB smoke snapshot](snapshots/public/quantlib_bsm_smoke_v1.duckdb)。这里的“5 种 option”表示 5 个 option contract templates；它们分别实例化到 5 个 underlyings 上，因此 5 个交易日的数据规模为：

| 对象 | 行数 |
|:---|---:|
| Underlying master | 5 |
| Option contract master | 25（5 × 5） |
| `underlying_daily` | 25（5 × 5 日） |
| `option_daily` | 125（25 × 5 日） |
| `pricing_metadata` | 25（5 × 5 日） |

先创建或幂等同步基准数据：

```bash
make install
make smoke
make snapshot-summary
```

加入第 6 个交易日只会新增该日的 5 条 underlying、25 条 option 和 5 条 metadata：

```bash
make append-day
```

增加 underlying 或 option template 时，先在 generator config 的相应数组中追加定义，然后运行：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  sync-config
```

`sync-config` 会为新增品种回填当前已有日期区间；已存在的业务主键和相同 row hash 不会重写。完整的 schema、主键、增量规则和命令见 [Authoring Pipeline](docs/authoring_pipeline.md)。

## Deterministic physical drift / volatility

Generator config schema `1.1.0` 支持把 underlying 的 P-measure
`physical_drift` 和 `physical_volatility` 配置成时间的确定性分段线性函数。原来的
scalar 写法继续支持，并等价于 constant function。当前 checked-in
`quantlib_bsm_smoke_v1` 保留为 constant baseline；新 job 可以从已使用时间函数的
[QuantLib/BSM generator 模板](authoring/templates/quantlib_bsm_generator.template.json)
复制配置。核心配置片段如下；完整可运行字段以模板为准。

```json
{
  "schema_version": "1.1.0",
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
      "dividend_yield": 0.01,
      "base_implied_volatility": 0.23
    }
  ]
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
- [generator.py](src/synthetic_derivatives/authoring/generator.py)：按真实日期区间计算等效参数并注入 QuantLib process；
- [pipeline.py](src/synthetic_derivatives/authoring/pipeline.py)：增量生成时保留每条路径的真实 previous date，保证一次生成和 append 的结果一致；
- [公开测试](tests/public/test_authoring_template.py)与[单元测试](tests/unit/test_authoring_config.py)：覆盖每日参数变化、精确积分、append invariance、配置校验与函数定义不可变性。

`market.underlyings.physical_drift` 和 `physical_volatility` 为兼容现有 schema，保存
函数在 `day_offset = 0` 的值。完整函数、当日实际使用的有效参数、区间起止日期和
reduction method 写入 `market.pricing_metadata.physical_dynamics`。这一扩展只改变
underlying 的 physical dynamics；期权 Q-measure 定价仍使用原有的
risk-free/dividend curves 和 deterministic $K,T$ smile。

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

### Authoring realism roadmap

建议按下面顺序增强，而不是先堆叠多个 pricing engines：

1. **真实 option chain。** 把当前 strike 与 maturity 一一绑定的 5 个 templates 改成同一
   expiry 下多个 puts/calls 和 strikes、同一 strike/moneyness 附近多个 expiries 的完整
   $K\times T$ 网格；合约在上市后跨 valuation dates 保持身份不变，并按规则新增 weekly、
   monthly 或 quarterly series。
2. **全曲面无套利 quality gates。** 在加 spread、tick rounding 和缺失报价之后再次检查
   单合约价格上下界、put-call parity、call 对 strike 的单调性与 convexity、以及相邻
   maturities 的 calendar consistency。`bid <= mid <= ask` 只是最基础的一层。
3. **公共市场状态与私有 DGP 隔离。** 先完成上面的 metadata split，再扩充模型；否则
   更复杂的 Heston/Bates parameters 只会成为更明显的答案泄漏。
4. **曲线、日历与合约约定。** 用非 flat discount/dividend/borrow term structures、离散
   dividend/corporate actions、真实交易所 holidays/early closes、strike increments、
   multiplier、exercise/settlement style 和 valuation timezone 替换 smoke 简化项。
5. **报价微观结构与流动性状态。** Spread 应随 premium、moneyness、maturity、vega 和
   liquidity 改变；加入合法 tick、bid/ask size、zero bid、stale/missing quote 与 quality
   flags。Volume/open interest 应有跨日持续性，不能每天独立均匀抽样。
6. **联合且跨日一致的 P/Q latent state。** Underlying 路径与 option surface 应共享 spot、
   variance/regime/jump state，同时用明确的 risk premia 区分 P-measure path dynamics 与
   Q-measure pricing dynamics，避免每天独立重画一张互不相关的 smile。
7. **多个 DGP family，但每个 snapshot 仍只有一个。** 保留当前 deterministic-smile BSM
   作为可解释 baseline，再分别生成 Heston、Bates/jump-diffusion、local-vol 等独立
   snapshot；每个模型先选 canonical analytic/FD engine，再用另一 engine 做 verifier。
8. **更长的历史与 regime coverage。** 从 5-day smoke 扩展到覆盖 calm/high-vol、earnings、
   jump 和 liquidity stress 的多时间段、多 seed scenario；最后再升级 underlying OHLC、
   overnight gap、volume 和 corporate-action 细节。

每次生成都应在未舍入理论价格、最终可见报价和 derived IV 三层分别保留 private audit，
但公开 snapshot 只暴露市场可观察量和任务明确允许的 conventions。这样既能保留可复现性，
又不会把生成模型误当成市场事实。

### 下一步实现计划

后续实现按依赖顺序推进；每一阶段都必须保持固定 config/seed 可重放、one-shot 与 append
结果一致、frozen snapshot 不可修改以及 logical hash 稳定。

| 阶段 | 模块 | 首次实现范围 | 完成标准 |
|:---|:---|:---|:---|
| 1 | `OptionChainBuilder` | 在 config 中声明 expiry schedule、call/put、strike 或 moneyness grid、listing/roll rules；在 listing date 把 moneyness 转成绝对 strike 后固定，不随每日 spot 重写合约 | 同一 expiry 有多个 strikes 和成对 call/put；合约 id 跨日稳定；one-shot、append 与 `sync-config` 结果一致 |
| 2 | `SurfaceQualityGate` | 按 `unrounded theoretical price -> spread/tick rounding -> visible quote -> quality gate` 的顺序检查单合约 bounds、put-call parity、strike monotonicity/convexity 和 calendar consistency | 正常 $K\times T$ chain 可 freeze；故意修改价格、spread 或末位 tick 的反例会被明确的 gate 拒绝 |
| 3 | `MarketContext` / `AuthoringProvenance` split | Public 层只留 curves、calendar、timestamp、settlement 与 precision；private 层保存 generation model/engine、P/Q latent state、seed/RNG 和未舍入价格 | `solver_visible` 中不存在 DGP parameters、seed、RNG 或 generator engine；private audit 仍可完整重放 snapshot |
| 4 | `ExchangeProfile` 与 `QuoteModel` | 加入真实 holiday/early-close、timezone、expiry/settlement、strike/tick rules，以及随 moneyness、maturity、vega、premium、liquidity 变化的 spread；补充 size、stale/missing/zero-bid 与 quality flags | 所有公开报价符合声明的 exchange profile；volume/open interest 和 liquidity state 具有跨日持续性 |
| 5 | `CurveState` 与 joint P/Q state | 支持非 flat discount/dividend/borrow curves、离散 dividend/corporate actions，并让 underlying 与 option surface 共享跨日 variance/regime/jump state | 同一 valuation timestamp 的 spot、curves、dividend 与 surface 使用同一个 market-state version；P/Q 差异由显式 risk premia 描述 |
| 6 | `GenerationModel` / `GenerationEngine` registry | 先保留 deterministic-smile BSM baseline，再分别加入 Heston、Bates/jump-diffusion、local-vol snapshot DGP；每个 snapshot 只选一个 canonical engine，另一个 engine 做 verifier | 不同 DGP 使用不同 `snapshot_id`；同一 snapshot/合约/时间仍只有一条市场报价；cross-engine error 在预设 tolerance 内 |
| 7 | Scenario campaign | 扩展日期跨度、seed、calm/high-vol/earnings/jump/liquidity-stress regimes，并升级 OHLC、overnight gap、volume 与 corporate actions | manifest 记录 scenario lineage；dataset split 按 snapshot/scenario 分组，避免同一 latent world 跨 train/test 泄漏 |

最小可审阅的提交顺序建议为：

1. 先只实现 `OptionChainBuilder`、配置校验与 contract-id/append-invariance tests，不改变
   当前 BSM 报价公式；smoke target 可使用 3 个 expiries × 7 个固定 strikes × call/put。
2. 在完整 chain 上实现 `SurfaceQualityGate` 及 adversarial tests，然后才允许新的 snapshot
   freeze。
3. 完成 public/private metadata migration；此后 Heston/Bates 等新 DGP 才能接入，避免把
   richer latent parameters 暴露给 Solver。

因此第一项代码工作应是 `OptionChainBuilder`，第二项是 `SurfaceQualityGate`；pricing
model registry 是建立好市场数据结构、无套利约束和权限边界之后的工作。

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
│   └── manifests/                 # 数据集版本、split、来源和 hash 清单
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
│   └── public/                    # 小型、可公开且带 logical hash 的 DRAFT/FROZEN 快照
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

存放出题端的配置与模板，不放 Solver 可见的数据。Authoring pipeline 使用 pinned QuantLib、generator config、seed 和 RNG 生成 `underlying_daily`、`option_daily` 与 `pricing_metadata`，随后冻结并计算 hash。内部 audit 与 oracle 输出应保持私有。

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
- `quantlib_bsm_smoke_v1.manifest.json`：revision、行数与 logical SHA-256；
- `sql_query/`：只读查询模板，不包含 canonical answer 或 hidden oracle。

IV、Greeks、smile、surface、VaR 和 ES 是从统一快照派生的任务结果，不在这里维护彼此独立的 truth tables。

### `schemas/`

定义跨边界的数据结构，包括 market snapshot、task variant、agent trajectory、Solver submission、verification report 和 dataset record。Schema 用于在进入下一阶段前拒绝缺字段、错单位、错顺序或非法数值。

### `src/synthetic_derivatives/`

项目的 Python 源码根目录。权限边界模块与训练编排模块分开：

- `authoring/`：允许使用 QuantLib，负责生成、质量门控、冻结与 hash。
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

`generated/` 存放可重建的训练集，因此被 `.gitignore` 排除；`manifests/` 存放应提交的版本信息、数据来源、snapshot grouping、train/validation/test split 和内容 hash。训练集不得包含 hidden oracle、hidden tests 或 verifier 私有输出。

### `examples/`

保存能够公开审阅的小型产物：canonical submission、正向 trajectory、first-error 负轨迹等。示例用于解释合同，不能被生产 hidden verifier 当作唯一 oracle 来源。

### `tests/`

- `fixtures/` 提供稳定的小型输入。
- `public/` 检查公开 schema、snapshot identity、method contract 和端到端接口。
- `unit/` 检查 task grammar、受约束 mutation、lineage 与 curriculum sampling。
- `integration/` 检查 task manifest 仅按 id/hash 引用 authoring snapshot。
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
- [金融衍生品 Task Mutation 与 Curriculum 扩展](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum.md)
- [合成期权链 IV、Greeks 与 Smile Agent Trajectory 样例](docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)
