# Authoring templates

`quantlib_bsm_generator.template.json` 是当前 parser 仍支持的 config `1.1.0` 最小可运行
兼容性模板。它包含一个
underlying 和一组平值 European call/put option templates；每个 option template
都会实例化到每个 underlying。它不是当前 `1.8.0` observation-law 基线，也不包含
P/Q dependence pair。

`quantlib_bsm_correlated_underlyings.template.json` 是 config `1.2.0` 的双 underlying
示例。它只用 factor-loading correlation 改变 $\mathbb P$-measure underlying close
paths；option pricing 不读取该相关矩阵。两份模板用于 legacy/增量行为演示。

`quantlib_bsm_brownian_bridge_volume.template.json` 是当前 config `1.8.0` 的最小可运行
模板：它显式声明 P/Q dependence pair、64-step integrated-variance log-price bridge、
keyed mean-preserving lognormal volume，以及彼此隔离的 stream namespaces。正式 metals
baseline 是 `configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json`；复制模板
后必须分配新的 config/generator/snapshot/bridge/volume identities。

完整 option-chain 例子见
`configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json`。它使用 config
`1.5.0` 的 sampled physical functions、Q pricing、static candidate grid、liquidity filter
和 quote noise，实际生成 22 × 4 × 7 × 2 个流动性合约。它是 checked-in frozen demo，仍
保留 legacy private IV audit。`quantlib_bsm_metals_option_chain_smoke_v2.json` 是旧的
config `1.6.0` heuristic-OHLC/uniform-volume baseline；新的 writable 22-asset baseline
是 `quantlib_bsm_metals_option_chain_smoke_v3.json`（config `1.8.0`）。

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

创建 DRAFT snapshot：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  create-smoke
```

确认 summary、只读 replay/泄漏校验和 manifest 后再冻结：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  summary

.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  validate

.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/generated/my_snapshot_v1.duckdb \
  --config authoring/configs/my_generator_v1.json \
  freeze
```

## v1 字段约束

| 字段 | 当前实现约束 |
|:---|:---|
| `schema_version` | `1.0.0` 支持 scalar physical 参数；`1.1.0` 增加 deterministic time functions；`1.2.0` 增加 P-measure `underlying_simulation`；`1.3.0` 使用 static `option_chain`；`1.4.0` 增加 liquidity/noise；`1.5.0` 增加 Q pricing mapping；`1.6.0` 移除 authoring IV；`1.7.0` 使用 P/Q dependence specs；`1.8.0` 增加 bridge/volume observation contracts。 |
| `calendar` / `day_count` | 只支持 `WeekendsOnly` / `Actual365Fixed`。 |
| `start_date` | 使用工作日；首条 underlying path 必须从该日开始。 |
| `quote_decimal_places` | 使用 `8`，与当前价格量化精度一致。 |
| `underlying_id` | 配置内唯一；spot 和两类 volatility 必须为正数。 |
| `physical_drift` / `physical_volatility` | 可使用 scalar，或 `piecewise_linear` 时间函数；volatility 的所有节点必须为正数。 |
| `underlying_simulation` | `1.2.0+` 必需；`measure=P`，`driver_order` 恰好覆盖全部 underlying，$\Lambda$ 每行 norm 不超过 1。 |
| `underlying_dependence_specs` | `1.7.0+` 替代 `underlying_simulation`；必须严格按 P、Q 排列，使用独立 IDs，Q 显式记录 source/mapping/Q context，并在当前 drift-only baseline 下保持相同 $\Lambda/D/R$ 与 ordering policy。 |
| `intraday_bridge` | `1.8.0` 必需；固定 log-price bridge、uniform calendar-fraction grid、integrated-variance clock、published endpoints、quantized-grid extrema 与 marginal cross-asset policy；steps 范围为 2--4096，正式 baseline 为 64。 |
| `volume_model` | `1.8.0` 必需；固定 P-measure keyed mean-preserving lognormal、ties-to-even integer rounding、signed-int64 clipping 与独立 stream policy。 |
| `base_volume` / `volume_log_stddev` | `1.8.0` 每个 underlying 必需；前者是正 signed-int64 integer，后者是 $[0,2]$ 的有限无量纲 log standard deviation。 |
| `option_chain` | `1.3.0+` 必需，且不能与 `option_templates` 混用；expiry/moneyness 严格递增、call/put 成对，当前只支持 `snapshot_start/static`。 |
| `liquidity_filter` | `1.4.0` 必需；按 maximum candidate expiry 与 inclusive listing-moneyness band 选择挂牌合约。 |
| `quote_model.bid_ask_noise` | `1.4.0` 必需；deterministic clipped-Gaussian multiplier 只扰动 bid/ask half-spread。 |
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
volatility 取均方根。因此 QuantLib 从当前 published state 到未量化 proposal 的单步 GBM
transition 满足
`integral(mu(t) dt)` 与 `integral(sigma(t)^2 dt)`，跨周末时也会覆盖完整的日历日
区间。Proposal 随后按价格 quantum 做 `ROUND_HALF_EVEN`，published close 作为下一期状态；
最终 materialized path 是 rounded-state Markov chain。Legacy 模板的 `high/low` 仍是
synthetic range heuristic、volume 仍是 uniform activity rule。Config `1.8.0` 则把
published open/close 作为 exact endpoints，在 64-step grid 上生成 marginal
integrated-variance Brownian bridge，并先逐点 tick 量化再取 extrema；volume 使用独立 keyed
$b\exp(sZ-s^2/2)$ law。两者都不适合作为 continuous barrier、cross-asset synchronous
extrema 或市场微观结构任务真值。`market.underlyings` 中的 scalar `physical_drift` / `physical_volatility`
保存函数在 `day_offset = 0` 的值；完整函数和当日有效参数保存在
`pricing_metadata.physical_dynamics`。

`initial_spot` 的合同是 start-date initial condition，模板值均已与 underlying price quantum
对齐。当前 parser 未单独拒绝非对齐自定义值，而 generator 会先执行
`ROUND_HALF_EVEN`；自定义模板必须自行保持 tick alignment。该 $S_t$ 是 synthetic
ex-dividend spot，drift/volatility 分别使用 Actual/365 Fixed 下的 year$^{-1}$ 与
year$^{-1/2}$ 单位；`adjusted_close=close`、零 dividend 与 `corporate_action=none` 是显式规则。

v1/v1.1 的已有 DRAFT 配置只能追加 underlying 或 option template。config `1.2.0` 把
underlying 集合与完整 `underlying_simulation` 视为同一个不可变 path contract；修改
driver order、factor loading 或增删 underlying 都必须复制配置并启用新的
`snapshot_id`。所有版本都会拒绝修改已生成的实体定义或回填历史路径缺口。
config `1.3.0` 还把 chain spec 与全部 listed contracts 视为整体不可变；append 或
`sync-config` 不能重算 strike、改变 contract ID 或替换 listing/roll rule。
Config `1.4.0` 同时冻结 candidate grid、liquidity filter、baseline spread 与 noise
namespace/bounds；任何修改都需要新 `snapshot_id`。
