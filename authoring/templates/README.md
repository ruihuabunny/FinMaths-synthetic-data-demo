# Authoring templates

`quantlib_bsm_generator.template.json` 是与当前
`synthetic_derivatives.authoring` v1 实现同步的最小可运行配置。它包含一个
underlying 和一组平值 European call/put option templates；每个 option template
都会实例化到每个 underlying。

`quantlib_bsm_correlated_underlyings.template.json` 是 config `1.2.0` 的双 underlying
示例。它只用 factor-loading correlation 改变 $\mathbb P$-measure underlying close
paths；option pricing 不读取该相关矩阵。

完整 option-chain 例子见
`configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json`。它使用 config
`1.3.0` 的 static `option_chain` 生成 20 × 3 × 7 × 2 合约网格，并继续保证相关矩阵只
排列 20 个 underlyings。

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
- `underlyings` 与 `option_templates`（legacy）或 `option_chain`（`1.3.0`）中的示例定义。

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
| `schema_version` | `1.0.0` 支持 scalar physical 参数；`1.1.0` 另支持 deterministic time functions；`1.2.0` 再增加 P-measure `underlying_simulation`；`1.3.0` 使用 static `option_chain`。 |
| `calendar` / `day_count` | 只支持 `WeekendsOnly` / `Actual365Fixed`。 |
| `start_date` | 使用工作日；首条 underlying path 必须从该日开始。 |
| `quote_decimal_places` | 使用 `8`，与当前价格量化精度一致。 |
| `underlying_id` | 配置内唯一；spot 和两类 volatility 必须为正数。 |
| `physical_drift` / `physical_volatility` | 可使用 scalar，或 `piecewise_linear` 时间函数；volatility 的所有节点必须为正数。 |
| `underlying_simulation` | `1.2.0/1.3.0` 必需；`measure=P`，`driver_order` 恰好覆盖全部 underlying，$\Lambda$ 每行 norm 不超过 1。 |
| `option_chain` | `1.3.0` 必需，且不能与 `option_templates` 混用；expiry/moneyness 严格递增、call/put 成对，当前只支持 `snapshot_start/static`。 |
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
