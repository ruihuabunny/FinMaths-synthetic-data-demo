# Public DuckDB snapshot 查询与管理说明

本目录保存可以公开读取的 DuckDB market snapshot。当前示例文件为：

```text
quantlib_bsm_smoke_v1.duckdb
quantlib_bsm_smoke_v1.manifest.json
```

DuckDB 文件保存市场数据和生成审计信息，manifest 保存 snapshot 当前 revision、
逻辑内容 hash 和各表行数。日常分析或 Solver 输入应优先读取
`solver_visible` views；只有 authoring 排查和审计才需要读取完整的 `market`、
`metadata` schemas。

## 当前基准 snapshot

当前提交的 smoke snapshot 为：

| 属性 | 值 |
|:---|:---|
| `snapshot_id` | `DERIVATIVES-QUANTLIB-SMOKE-v1` |
| 状态 | `DRAFT` |
| Revision | `1` |
| 日期范围 | `2026-08-03` 至 `2026-08-07` |
| Underlyings | 5 |
| Option contracts | 25 |
| Underlying daily rows | 25 |
| Option daily rows | 125 |
| Pricing metadata rows | 25 |

这些数值描述当前提交的基准文件。运行增量 authoring 命令后，应以同名
manifest 或 `metadata.snapshots` 中的最新结果为准。

## Schema 分层

数据库使用三个 SQL schema：

| Schema | 用途 |
|:---|:---|
| `market` | 完整业务表，以及 authoring 使用的 row hash 和生成血缘。 |
| `metadata` | Snapshot 状态、revision、生成批次和数据库 schema 版本。 |
| `solver_visible` | 去掉 authoring 内部字段的只读 views，供分析和 Solver 使用。 |

查看数据库中的表和 view：

```sql
SELECT
    table_schema,
    table_name,
    table_type
FROM information_schema.tables
WHERE table_schema IN ('market', 'metadata', 'solver_visible')
ORDER BY table_schema, table_name;
```

查看具体字段：

```sql
DESCRIBE market.option_daily;
DESCRIBE solver_visible.option_daily;
```

## Market 表关系

五张业务表的逻辑关系如下：

```text
market.underlyings
├── market.underlying_daily
├── market.option_contracts
│   └── market.option_daily
└── market.pricing_metadata
```

所有主键和跨表关联都包含 `snapshot_id`。当前使用方式通常是一份 DuckDB 文件
对应一个 snapshot，但表结构仍通过 `snapshot_id` 显式隔离数据。

### `market.underlyings`

Underlying 主表。一行代表一个标的的固定生成和定价参数。

业务主键：

```text
(snapshot_id, underlying_id)
```

主要字段包括 `initial_spot`、`physical_drift`、`physical_volatility`、
`risk_free_rate`、`dividend_yield` 和 `base_implied_volatility`。

```sql
SELECT
    underlying_id,
    initial_spot,
    physical_drift,
    physical_volatility,
    risk_free_rate,
    dividend_yield,
    base_implied_volatility
FROM market.underlyings
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
ORDER BY underlying_id;
```

### `market.option_contracts`

Option contract 主表。一行代表一个静态期权合约。

业务主键：

```text
(snapshot_id, option_id)
```

每个 option template 会实例化到每个 underlying，稳定 ID 为：

```text
option_id = underlying_id + "-" + template_id
```

例如 `SYNTH-U01-CALL-100-090D`。当前 5 个 underlying 与 5 个 template
形成 25 个 contracts。

```sql
SELECT
    option_id,
    underlying_id,
    template_id,
    call_put,
    strike,
    expiry,
    exercise_style,
    settlement_type,
    contract_multiplier
FROM market.option_contracts
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND underlying_id = 'SYNTH-U03'
ORDER BY expiry, call_put, strike;
```

### `market.underlying_daily`

Underlying 日行情表。一行表示某个交易日、某个 underlying 的 OHLCV 数据。

业务主键：

```text
(snapshot_id, date, underlying_id)
```

查询一个 underlying 的完整时间序列：

```sql
SELECT
    date,
    underlying_id,
    spot_open,
    spot_high,
    spot_low,
    spot_close,
    adjusted_close,
    volume
FROM solver_visible.underlying_daily
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND underlying_id = 'SYNTH-U03'
ORDER BY date;
```

查询某天全部 underlyings：

```sql
SELECT *
FROM solver_visible.underlying_daily
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND date = DATE '2026-08-03'
ORDER BY underlying_id;
```

### `market.option_daily`

Option 日报价表。一行表示某个交易日、某个期权合约的报价和交易活动。

业务主键：

```text
(snapshot_id, date, option_id)
```

主要价格字段是 `bid`、`mid`、`ask` 和 `settlement_price`。静态字段
`call_put`、`strike`、`expiry` 等也被保留在 daily 表中，使 Solver 可以直接查询
option chain，而不必先 join `option_contracts`。

查询某天、某个 underlying 的 option chain：

```sql
SELECT
    option_id,
    call_put,
    strike,
    expiry,
    bid,
    mid,
    ask,
    volume,
    open_interest
FROM solver_visible.option_daily
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND date = DATE '2026-08-03'
  AND underlying_id = 'SYNTH-U03'
ORDER BY expiry, call_put, strike;
```

查询一个 option 的报价时间序列：

```sql
SELECT
    date,
    bid,
    mid,
    ask,
    volume,
    open_interest
FROM solver_visible.option_daily
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND option_id = 'SYNTH-U03-CALL-100-090D'
ORDER BY date;
```

### `market.pricing_metadata`

每行代表某个 valuation date、某个 underlying 的定价环境。

业务主键：

```text
(snapshot_id, valuation_timestamp, underlying_id)
```

主要内容包括：

- Risk-free 和 dividend curves；
- P-measure physical dynamics；
- Q-measure pricing dynamics；
- Pricing model 和 QuantLib engine；
- Seed、RNG、输入精度和舍入规则。

完整 `market` 表额外提供 `valuation_date`，方便与 daily 表关联：

```sql
SELECT
    valuation_date,
    valuation_timestamp,
    underlying_id,
    risk_free_rate,
    dividend_yield,
    borrow_or_carry_rate,
    pricing_model,
    pricing_engine,
    pricing_dynamics
FROM market.pricing_metadata
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND underlying_id = 'SYNTH-U03'
ORDER BY valuation_timestamp;
```

## 常用关联查询

### Option、spot 与 moneyness

```sql
SELECT
    q.date,
    q.underlying_id,
    q.option_id,
    u.spot_close,
    q.call_put,
    q.strike,
    q.expiry,
    q.bid,
    q.mid,
    q.ask,
    q.strike / u.spot_close AS spot_moneyness
FROM solver_visible.option_daily AS q
JOIN solver_visible.underlying_daily AS u
  ON u.snapshot_id = q.snapshot_id
 AND u.date = q.date
 AND u.underlying_id = q.underlying_id
WHERE q.snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND q.date = DATE '2026-08-03'
  AND q.underlying_id = 'SYNTH-U03'
ORDER BY q.expiry, q.call_put, q.strike;
```

### Option、spot 与 pricing metadata

Authoring 侧可以使用 `market.pricing_metadata.valuation_date` 做精确的日期关联：

```sql
SELECT
    q.date,
    q.option_id,
    u.spot_close,
    q.strike,
    q.expiry,
    q.mid,
    p.risk_free_rate,
    p.dividend_yield,
    p.day_count,
    p.pricing_model
FROM market.option_daily AS q
JOIN market.underlying_daily AS u
  ON u.snapshot_id = q.snapshot_id
 AND u.date = q.date
 AND u.underlying_id = q.underlying_id
JOIN market.pricing_metadata AS p
  ON p.snapshot_id = q.snapshot_id
 AND p.valuation_date = q.date
 AND p.underlying_id = q.underlying_id
WHERE q.snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
  AND q.date = DATE '2026-08-03'
  AND q.underlying_id = 'SYNTH-U03'
ORDER BY q.expiry, q.call_put, q.strike;
```

## Metadata 管理表

### `metadata.snapshots`

保存 snapshot 当前的总体状态：

```sql
SELECT
    snapshot_id,
    status,
    current_revision,
    content_sha256,
    generator_config_id,
    generator_version,
    seed,
    rng,
    created_at,
    updated_at,
    frozen_at
FROM metadata.snapshots;
```

- `DRAFT` 可以继续追加数据；
- `FROZEN` 拒绝后续增量写入；
- `current_revision` 只在逻辑内容实际变化时增加；
- `content_sha256` 是五张 market 表有序 logical row hashes 的汇总 hash。

### `metadata.generation_runs`

每次 pipeline 操作对应一条运行记录：

```sql
SELECT
    run_id,
    operation,
    status,
    requested_start_date,
    requested_end_date,
    table_stats,
    error_message,
    started_at,
    completed_at
FROM metadata.generation_runs
ORDER BY started_at;
```

状态含义：

- `COMPLETED`：生成了新的逻辑数据；
- `NOOP`：幂等重跑，没有数据变化；
- `FAILED`：生成、约束或质量检查失败。

`table_stats` 是 JSON，包含各表的 `requested`、`inserted`、`updated` 和
`unchanged` 数量。

### `metadata.snapshot_revisions`

只有 snapshot 内容实际变化时才记录新 revision：

```sql
SELECT
    revision,
    run_id,
    content_sha256,
    underlying_count,
    option_contract_count,
    underlying_daily_count,
    option_daily_count,
    pricing_metadata_count,
    created_at
FROM metadata.snapshot_revisions
WHERE snapshot_id = 'DERIVATIVES-QUANTLIB-SMOKE-v1'
ORDER BY revision;
```

### `metadata.schema_migrations`

保存 DuckDB DDL 版本及 checksum。Pipeline 打开数据库时会校验它，避免代码在
未知或不兼容的 schema 上继续写入。

## Solver-visible views

供下游查询的 views 包括：

```text
solver_visible.underlying_daily
solver_visible.option_daily
solver_visible.pricing_metadata
```

这些 views 保留业务字段，但不暴露以下 authoring 内部字段：

- `row_sha256`；
- `generated_run_id`；
- `created_run_id`；
- `last_run_id`。

一般的 IV、Greeks、smile 或数据浏览应从这些 views 开始。

## 行排序规则

DuckDB 表没有可以依赖的天然行顺序。即使两次执行同一条 `SELECT *`，也不应假设
返回顺序固定。所有依赖顺序的查询都必须显式使用 `ORDER BY`。

推荐顺序：

| 查询内容 | 推荐 `ORDER BY` |
|:---|:---|
| Underlying panel | `date, underlying_id` |
| 单个 underlying 时间序列 | `date` |
| Option chain | `expiry, call_put, strike` |
| Option quote panel | `date, underlying_id, expiry, call_put, strike` |
| 单个 option 时间序列 | `date` |
| Snapshot revisions | `revision` |
| Generation runs | `started_at` |

## Authoring 写入管理

不要直接使用手工 `INSERT`、`UPDATE` 或 `DELETE` 修改这些表。Authoring pipeline
按以下流程管理写入：

```text
Generator config
  -> 生成增量 rows
  -> 写入临时 staging tables
  -> 按业务主键 MERGE
  -> 执行跨表 quality gates
  -> 更新 revision 和 content hash
  -> transaction commit
  -> 原子更新 manifest
```

管理规则包括：

- 同一业务主键且 `row_sha256` 相同的记录保持不变；
- 已存在的 underlying 和 option contract 定义不可原地修改；
- DRAFT 配置可以追加新的 underlying 或 option template；
- 不允许回填 underlying 历史路径中间的日期缺口；
- 任一步骤失败都会 rollback 整个批次；
- `FROZEN` snapshot 不允许继续写入；
- 表中不声明数据库级 foreign keys，跨表完整性由 pipeline quality gates 检查。

查看当前摘要也可以使用仓库命令：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  summary
```

数据库 DDL 定义见
[`src/synthetic_derivatives/authoring/schema.py`](../../src/synthetic_derivatives/authoring/schema.py)，
增量和 snapshot 生命周期逻辑见
[`src/synthetic_derivatives/authoring/pipeline.py`](../../src/synthetic_derivatives/authoring/pipeline.py)。
