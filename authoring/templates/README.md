# Authoring templates

本目录提供小型、可复制的教学模板；它们用于理解字段和快速 smoke，不代表当前 22-metal
生产配置。新 authoring 优先从 config `1.6.0` 的 successor 配置复制，再更换完整 identity。

`quantlib_bsm_generator.template.json` 是与当前
`synthetic_derivatives.authoring` v1 实现同步的最小可运行配置。它包含一个
underlying 和一组平值 European call/put option templates；每个 option template
都会实例化到每个 underlying。

`quantlib_bsm_correlated_underlyings.template.json` 是 config `1.2.0` 的双 underlying
示例。它只用 factor-loading correlation 改变 $\mathbb P$-measure underlying close
paths；option pricing 不读取该相关矩阵。

完整、当前可写的 option-chain 例子见
[`configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json`](../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json)。
它使用 config `1.6.0` 的 static candidate grid、liquidity filter、quote noise 和共同 Q
pricing identity，实际生成 22 × 4 × 7 × 2 个流动性合约，并继续保证相关矩阵只排列
22 个 underlyings。相邻的 `...smoke_v1.json` 是 legacy v3 snapshot 的历史合同；当前
pipeline 可以读取它，但拒绝用它创建、追加或冻结数据。

F2A 不直接从两个教学模板起步。V4 executable audit 使用
[`quantlib_bsm_metals_f2a_parent_v2.json`](../../configs/generators/quantlib_bsm_metals_f2a_parent_v2.json)；
v5.1 full-trajectory pilot 使用独立的 126-business-date
[`quantlib_bsm_metals_f2a_v5_parent_v1.json`](../../configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json)。
二者都要求 `0.01 USD` underlying/option ticks，但 snapshot identity、时间范围和任务合同不同，不能
互相 fallback。

## 如何选择起点

| 目标 | 推荐起点 | 说明 |
|:---|:---|:---|
| 最小单标的 smoke | `quantlib_bsm_generator.template.json` | Legacy scalar 参数与逐条 option templates，适合快速理解表结构。 |
| 两标的 P-measure dependence | `quantlib_bsm_correlated_underlyings.template.json` | 展示 `Lambda/D/R`，不改变 option pricing。 |
| 当前完整 authoring | `configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json` | Config 1.6、static liquid chain、共同 Q identity，不生成 IV answer。 |
| F2A v4 executable parent | `configs/generators/quantlib_bsm_metals_f2a_parent_v2.json` | 65 日、v4 catalogue 的冻结 mutation source；生产必须显式选择已物化 parent。 |
| F2A v5.1 pilot parent | `configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json` | 126 日、三组 shared drift/diffusion node locations；供 8-underlying full-trajectory task package。 |

复制完整配置时，`snapshot_id`、`generator_config_id` 和 `generator_version` 是一组
materialization identity。改变经济参数、随机法则、价格 increment 或 output contract 时，
必须同时启用新的 identity，并写入新 DuckDB 文件。

## 使用方式

先复制模板并修改副本，不要直接把模板当作正式配置：

```bash
cp authoring/templates/quantlib_bsm_generator.template.json \
  authoring/configs/my_generator_v1.json
```

至少应替换以下标识和业务参数：

- `generator_config_id`：一个 snapshot 生命周期内不可更改；
- `snapshot_id`：修正已有实体或历史数据时必须使用新的 ID；
- `seed`、`start_date` 和 `business_days`；
- `underlyings` 与 `option_templates`（legacy）或 `option_chain`（`1.3.0+`）中的示例定义。

若复制 config `1.6.0`，还应检查 `underlying_minimum_price_increment`、
`option_minimum_price_increment`、`underlying_simulation`、`q_pricing` 和 `quote_model`。
`q_pricing` 只声明定价测度、numeraire、rate path 与 P-to-Q diffusion mapping；IV root
method 和 canonical answer 属于 task/verifier 配置，不能放回 generator JSON。

V5 parent 还带 `f2a_parent_contract`，它只声明 distinct-parent role、三节点 shape 和 tick contract；
public task 的 80-step BSM inversion、linked validation、model-signal costs 与 output schema 分别来自
variant/dataset configs，不能复制进 generator 作为 latent answer。

创建 DRAFT snapshot：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  create-smoke
```

确认 summary 和 manifest 后再冻结：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  summary

.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  freeze
```

## v1 字段约束

| 字段 | 当前实现约束 |
|:---|:---|
| `schema_version` | `1.0.0` 支持 scalar physical 参数；`1.1.0` 增加 deterministic time functions；`1.2.0` 增加 P-measure `underlying_simulation`；`1.3.0` 使用 static `option_chain`；`1.4.0` 增加 liquidity filter 与 bid/ask noise；`1.5.0` 是带 legacy authoring-IV contract 的历史版本；`1.6.0` 删除该 solver/answer。 |
| `calendar` / `day_count` | 只支持 `WeekendsOnly` / `Actual365Fixed`。 |
| `start_date` | 使用工作日；首条 underlying path 必须从该日开始。 |
| `quote_decimal_places` | 使用 `8`，与当前价格量化精度一致。 |
| `underlying_minimum_price_increment` / `option_minimum_price_increment` | 必须为正、可由声明的 decimal scale 精确表示；二者分别控制 restart spot state 与公开 option quote tick。 |
| `underlying_id` | 配置内唯一；spot 和两类 volatility 必须为正数。 |
| `physical_drift` / `physical_volatility` | 可使用 scalar，或 `piecewise_linear` 时间函数；volatility 的所有节点必须为正数。 |
| `underlying_simulation` | `1.2.0+` 必需；`measure=P`，`driver_order` 恰好覆盖全部 underlying，$\Lambda$ 每行 norm 不超过 1。 |
| `option_chain` | `1.3.0+` 必需，且不能与 `option_templates` 混用；expiry/moneyness 严格递增、call/put 成对，当前只支持 `snapshot_start/static`。 |
| `liquidity_filter` | `1.4.0` 必需；按 maximum candidate expiry 与 inclusive listing-moneyness band 选择挂牌合约。 |
| `quote_model.bid_ask_noise` | `1.4.0` 必需；deterministic clipped-Gaussian multiplier 只扰动 bid/ask half-spread。 |
| `q_pricing` | `1.5.0+` 必需；当前只支持 common USD Q/money-market numeraire、`girsanov_drift_only` 和 `same_deterministic_diffusion`。Config `1.6.0` 禁止 `implied_volatility_solver`。 |
| `template_id` | 配置内唯一；会与 `underlying_id` 拼成稳定的 `option_id`。 |
| `call_put` | 只能是 `call` 或 `put`。 |
| `exercise_style` | v1 只支持 `european`。 |
| `strike_moneyness` / `expiry_days` | 必须为正数；strike 基于初始 spot 计算。 |
| `strike_increment` / `strike_rounding` | Chain strike 在 listing date 以正 increment 和固定 `ROUND_HALF_EVEN` 转成绝对值；舍入后不得重复。 |

时间函数以 `start_date` 为 `day_offset = 0`，节点间线性插值，首个节点必须为
0，节点外只允许 flat extrapolation：

```json
{
  "type": "piecewise_linear",
  "nodes": [
    {"day_offset": 0, "value": 0.22},
    {"day_offset": 30, "value": 0.28},
    {"day_offset": 90, "value": 0.20}
  ],
  "extrapolation": "flat"
}
```

Underlying 每个 close interval 使用时间函数的精确区间缩约：drift 取算术平均，
volatility 取均方根。因此 QuantLib 的单步 GBM transition 满足
`integral(mu(t) dt)` 与 `integral(sigma(t)^2 dt)`，跨周末时也会覆盖完整的日历日
区间。`market.underlyings` 中的 scalar `physical_drift` / `physical_volatility`
保存函数在 `day_offset = 0` 的值；完整函数和当日有效参数保存在
`pricing_metadata.physical_dynamics`。

v1/v1.1 的已有 DRAFT 配置只能追加 underlying 或 option template。config `1.2.0` 把
underlying 集合与完整 `underlying_simulation` 视为同一个不可变 path contract；修改
driver order、factor loading 或增删 underlying 都必须复制配置并启用新的
`snapshot_id`。所有版本都会拒绝修改已生成的实体定义或回填历史路径缺口。
config `1.3.0` 还把 chain spec 与全部 listed contracts 视为整体不可变；append 或
`sync-config` 不能重算 strike、改变 contract ID 或替换 listing/roll rule。
Config `1.4.0` 同时冻结 candidate grid、liquidity filter、baseline spread 与 noise
namespace/bounds；任何修改都需要新 `snapshot_id`。

## 创建后的最小验收

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  summary

.venv/bin/pytest -q tests/public/test_authoring_template.py
git diff --check
```

Summary 至少应确认 identity、`DRAFT` status、revision、日期范围和 logical row counts。
冻结前还应完成与所选模型对应的数学/quality gates；不要把模板生成的 smoke 数据描述为真实
交易所市场，也不要把 P-measure volatility 或 hidden pricing volatility 称为 implied volatility。
V5.1 parent 冻结后还应先运行
`scripts/materialize_f2a_agent_tasks.py --parent-db <path> --qualify-only`；qualification 失败时报告原因，
不要改用 legacy v3 或 v4 parent。
