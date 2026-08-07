# Generated development snapshots

本目录保存本地 materialize 的开发期 DuckDB snapshots。数据库和 manifest 是可重建产物，继续由
`.gitignore` 排除；本 README 与 [`sql_query/`](sql_query/README.md) 作为可提交的操作文档和
只读查询模板保留。

## Active development snapshot

除非任务显式指定其他 snapshot，开发、数据检查、示例和端到端验证使用：

| 字段 | 当前值 |
|:---|:---|
| Database | `quantlib_bsm_metals_option_chain_smoke_v1_20260807.duckdb` |
| Manifest | `quantlib_bsm_metals_option_chain_smoke_v1_20260807.manifest.json` |
| `snapshot_id` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3` |
| Status / revision | `DRAFT` / `1` |
| Authoring schema | `2.4.0` |
| Generator config | `quantlib-randomized-tdgbm-metals-liquid-option-chain-v3` |
| Generator version | `0.7.0` |
| QuantLib / DuckDB | `1.39` / `1.5.5` |
| Seed | `20260806` |
| Business-date range | `2026-08-03` through `2026-10-30` |

可重放配置为
[`configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json`](../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)。
根级 [`AGENTS.md`](../../AGENTS.md) 是 active snapshot identity 和生命周期约束的权威来源。

这个数据库当前是 `DRAFT`，不能描述成 frozen parent，也不能直接用于要求 frozen parent 的 F2A
materialization。Freeze、mutation 或 regeneration 必须是显式任务，并遵守新的 config/snapshot
identity 规则。数据库缺失时应报告或从声明的 generator config 重建，不能静默 fallback 到
`snapshots/public/quantlib_bsm_smoke_v1.duckdb`。

## Logical size

当前 manifest 声明：

| 对象 | 行数 |
|:---|---:|
| Underlyings | 22 |
| Underlying dependence specs | 1 |
| Option chain specs | 1 |
| Option contracts | 1,232 |
| Underlying daily rows | 1,430 |
| Option daily rows | 60,368 |
| Pricing metadata rows | 1,430 |
| Option pricing audit rows | 60,368 |

65 个 business dates × 22 个 underlyings 得到 1,430 条 underlying/metadata rows。每个
underlying 在 listing date 有 `4 expiries × 7 strikes × call/put = 56` 个 liquid contracts；
到期后不再生成 option daily quote，因此 option daily count 小于 `1,232 × 65`。

## Visibility boundary

常用 Solver-safe views：

```text
solver_visible.underlying_daily
solver_visible.option_daily
solver_visible.pricing_metadata
```

Authoring、provenance 和 audit objects 包括：

```text
market.underlyings
market.option_contracts
market.underlying_dependence
market.option_chain_specs
market.option_pricing_audit
metadata.snapshots
metadata.generation_runs
metadata.snapshot_revisions
```

当前单文件 development DB 的 maintainer 可以读取全部 schemas；这不代表生产 Solver 有相同权限。
生产任务必须只导出或挂载 solver-visible child，并隔离 parent、authoring tables、private lineage 和
hidden verifier。

## Common read-only queries

[`sql_query/`](sql_query/README.md) 提供：

- snapshot identity、row counts 和 IV status summary；
- underlying OHLCV time series；
- option chain、spot moneyness 和 pricing context；
- Solver-safe IV inputs；
- trusted IV answers、generation runs、chain authoring spec、P-measure dependence 和 P/Q dynamics audit。

所有 `.sql` 文件只包含一条 query，顶部使用 `parameters` CTE 集中声明 snapshot/date/underlying，
并显式指定 `ORDER BY`。不要依赖 DuckDB 未定义的天然行顺序。

运行示例：

```python
from pathlib import Path

import duckdb

database = (
    "snapshots/generated/"
    "quantlib_bsm_metals_option_chain_smoke_v1_20260807.duckdb"
)
query = Path(
    "snapshots/generated/sql_query/option_chain.sql"
).read_text(encoding="utf-8")

connection = duckdb.connect(database, read_only=True)
try:
    result = connection.execute(query).fetchdf()
finally:
    connection.close()
```

若本地环境没有 Pandas，使用 `fetchall()`；不要为了只读检查修改数据库或安装额外 package。

## Lifecycle rules

- 默认以 `read_only=True` 打开现有 development DB。
- 检查 identity 时同时读取数据库内 `metadata.snapshots` 和旁边的 manifest；文件名不是逻辑 identity。
- `DRAFT` 允许显式 authoring 操作，但任何经济参数、随机法则、increment 或 generator/backend 变化都必须
  创建新的 config/snapshot identity。
- Frozen parent 不允许原地 mutation；每个 materialized child 必须使用新 identity/revision 和 private lineage。
- SQL 查询结果是 inspection output，不是 canonical hidden answer；authoring-only query 不能进入 Solver bundle。
- Generated DuckDB、manifest、WAL、private lineage 和 derived datasets 不提交 Git；只提交本目录文档和 SQL 模板。

Authoring schema、重放和 freeze 命令见
[authoring package README](../../src/synthetic_derivatives/authoring/README.md) 与
[authoring pipeline](../../docs/authoring_pipeline.md)。
