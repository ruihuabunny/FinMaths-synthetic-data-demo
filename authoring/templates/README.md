# Authoring templates

`quantlib_bsm_generator.template.json` 是与当前
`synthetic_derivatives.authoring` v1 实现同步的最小可运行配置。它包含一个
underlying 和一组平值 European call/put option templates；每个 option template
都会实例化到每个 underlying。

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
- `underlyings` 与 `option_templates` 中的示例定义。

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
| `schema_version` | 必须为 `1.0.0`。 |
| `calendar` / `day_count` | 只支持 `WeekendsOnly` / `Actual365Fixed`。 |
| `start_date` | 使用工作日；首条 underlying path 必须从该日开始。 |
| `quote_decimal_places` | 使用 `8`，与当前价格量化精度一致。 |
| `underlying_id` | 配置内唯一；spot 和两类 volatility 必须为正数。 |
| `template_id` | 配置内唯一；会与 `underlying_id` 拼成稳定的 `option_id`。 |
| `call_put` | 只能是 `call` 或 `put`。 |
| `exercise_style` | v1 只支持 `european`。 |
| `strike_moneyness` / `expiry_days` | 必须为正数；strike 基于初始 spot 计算。 |

对已有 DRAFT 配置只能追加 underlying 或 option template。删除或修改已生成的
实体定义、回填历史路径缺口都会被 pipeline 拒绝；此类变更应复制配置并启用新的
`snapshot_id`。
