# 常用 DuckDB SQL Query

本目录保存针对 public snapshot 的可复用只读查询。每个 `.sql` 文件只包含一条
query，并在文件开头使用单行 `parameters` CTE 集中声明可编辑参数。所有结果都显式
指定 `ORDER BY`，避免依赖 DuckDB 未定义的天然行顺序。

| 文件 | 用途 | 默认参数 |
|:---|:---|:---|
| `snapshot_summary.sql` | Snapshot 状态、revision、日期范围和各表行数 | 当前 smoke snapshot |
| `underlying_time_series.sql` | 单个 underlying 的 OHLCV 时间序列 | `SYNTH-U03` |
| `option_chain.sql` | 指定日期和 underlying 的完整 option chain | `2026-08-03`, `SYNTH-U03` |
| `option_spot_moneyness.sql` | Option quote、spot 与 spot moneyness | `2026-08-03`, `SYNTH-U03` |
| `option_pricing_context.sql` | Option、spot、rate/dividend 和 pricing model 联表 | `2026-08-03`, `SYNTH-U03` |
| `generation_audit.sql` | Authoring generation runs 与 revision 审计 | 当前 smoke snapshot |

运行示例：

```python
from pathlib import Path

import duckdb

database = "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
query = Path("snapshots/public/sql_query/option_chain.sql").read_text()
connection = duckdb.connect(database, read_only=True)
try:
    columns = [item[0] for item in connection.execute(query).description]
    rows = connection.fetchall()
finally:
    connection.close()
```

使用其他 snapshot、日期或标的时，只修改目标 SQL 顶部的 `parameters` CTE。Solver
任务仍须由 task contract 固定查询范围、列和排序；这些文件是公共查询模板，不是
hidden verifier 或 canonical answer。
