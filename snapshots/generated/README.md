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

该 legacy snapshot 的历史配置为
[`configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json`](../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)。
根级 [`AGENTS.md`](../../AGENTS.md) 是 active snapshot identity 和生命周期约束的权威来源。

这个数据库当前是 `DRAFT`，不能描述成 frozen parent，也不能直接用于要求 frozen parent 的 F2A
materialization。Freeze、mutation 或 regeneration 必须是显式任务，并遵守新的 config/snapshot
identity 规则。数据库缺失时应报告，不能用当前 pipeline 重建旧 identity，也不能静默 fallback 到
`snapshots/public/quantlib_bsm_smoke_v1.duckdb`。

当前 pipeline 已移除 authoring-time IV solver，所以这份 v3/schema-2.4 DB 虽为
`DRAFT`，也必须保持只读，避免在同一 identity 下混合两种 output contract。新生成使用
[`quantlib_bsm_metals_option_chain_smoke_v2.json`](../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json)
和 v4 snapshot identity，写入新 DuckDB。

## F2A frozen parent

F2A 明确选择以下 tick-aligned clean parent；它不是默认 active development database：

| 字段 | 当前值 |
|:---|:---|
| Database | `f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/parent.duckdb` |
| Manifest | 同目录 `parent.manifest.json` |
| `snapshot_id` | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2` |
| Status / revision | `FROZEN` / `1` |
| Generator config | `quantlib-randomized-tdgbm-metals-f2a-parent-v2` |
| Generator version | `0.8.0` |
| Config schema | `1.6.0` |
| Minimum price increments | Underlying `0.01 USD`; option `0.01 USD` |
| Business-date range | `2026-08-03` through `2026-10-30` |

对应配置是
[`configs/generators/quantlib_bsm_metals_f2a_parent_v2.json`](../../configs/generators/quantlib_bsm_metals_f2a_parent_v2.json)。
它在当前 pipeline 下从新 identity materialize，经过 quality gates 后冻结；没有生成
authoring-time IV answers。该 parent 不得原地追加、同步或 mutation。每个 child 使用
`f2a/children/<child_snapshot_id>/` 下的新 identity、manifest 和 private lineage。

Parent 的完整 table/field dictionary、业务键关联图、JSON 字段、eligible mutation slices、child
allowlist、mutation gates 与可抽取的 LLM task instructions 见
[`F2A parent DuckDB 数据字典`](f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md)。

## F2A v5.1 full-trajectory parent contract

V5.1 使用另一份 tick-aligned parent；它不替代 v4 executable parent，也不能从 active/public v3
fallback：

| 字段 | 当前合同 |
|:---|:---|
| Generator config | `configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json` |
| `snapshot_id` | `DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1` |
| Required status / revision | `FROZEN` / `1` |
| Generator / config schema | `0.8.0` / `1.6.0` |
| Business dates | 126，`2026-08-03` through `2027-01-25` |
| Shared physical nodes | 每 underlying 3 个 drift/diffusion locations；values 保持 private |
| Minimum price increments | Underlying `0.01 USD`; option `0.01 USD` |
| Materialized logical size | 22 underlyings、1,232 contracts、2,772 underlying/metadata rows、79,156 option rows、0 IV-audit rows |

该大文件与 v5.1 public/private task packages 都是 ignored local artifacts；clean clone 只保证 config、
runtime、schemas 和 tests 存在。生产或 pilot authoring 必须显式给出已经 materialize/freeze 的 parent
path，并可先运行：

```bash
.venv/bin/python scripts/materialize_f2a_agent_tasks.py \
  --parent-db /path/to/f2a-v5-parent.duckdb \
  --qualify-only
```

Qualification 会核对 distinct identity、126-day/22-underlying/三节点 shape 和 estimator gate；失败时
应报告，不能改用本页前两节的 v3 或 v4 数据库。当前 v5.1 output contract 是
`model-reconstruction-xut-full-trajectory-v2`，submission schema 是
`schemas/submission-v5.1.schema.json`。

## Active v3 logical size

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

F2A frozen parent 的 underlyings、contracts、daily rows 和 metadata counts 与上表相同，唯一区别是
`market.option_pricing_audit` 为 schema-retained empty table，row count 为 `0`。

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
market.option_pricing_audit  # schema-2.4 legacy rows; current pipeline does not append
metadata.snapshots
metadata.generation_runs
metadata.snapshot_revisions
```

当前单文件 development DB 的 maintainer 可以读取全部 schemas；这不代表生产 Solver 有相同权限。
生产任务必须只导出或挂载 solver-visible child，并隔离 parent、authoring tables、private lineage 和
hidden verifier。

## Active v3 的只读查询

Active v3 与 public v3 使用相同 logical snapshot ID 和 schema，因此应把
[`snapshots/public/sql_query/`](../public/sql_query/README.md) 中的查询连接到本目录的 active
database。不要使用本目录的 [`sql_query/`](sql_query/README.md)：后者参数固定为另一个
F2A tick-aligned frozen parent。

Public v3 query catalog 提供：

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
    "snapshots/public/sql_query/option_chain.sql"
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
- 这份 legacy `DRAFT` 在当前 pipeline 下只读；新 authoring 使用 v4 config/snapshot identity。
- F2A v2 parent 是 `FROZEN` revision `1`，IV audit count 为 `0`；不得原地写入或 mutation。
- F2A v5.1 parent 必须使用 distinct model-signal identity、126 dates 与三节点 shape；它同样只读，
  且不能 fallback 到 v4 parent。
- 每个 materialized child 必须位于 `f2a/children/<child_snapshot_id>/`，使用新 identity/revision
  和 private lineage。
- V5.1 task materializer 写 task-specific public/private packages；这些是 pilot artifacts，不等于
  gate-passing cohort 或已发布 training dataset。
- SQL 查询结果是 inspection output，不是 canonical hidden answer；authoring-only query 不能进入 Solver bundle。
- Generated DuckDB、manifest、WAL、private lineage 和 derived datasets 不提交 Git；只提交本目录文档和 SQL 模板。

Authoring schema、重放和 freeze 命令见
[authoring package README](../../src/synthetic_derivatives/authoring/README.md) 与
[authoring pipeline](../../docs/authoring_pipeline.md)。
