# Generated snapshot 常用 SQL

本目录保存 active development DuckDB 的参数化、只读查询。默认逻辑 snapshot 为
`DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v1`，默认 option slice 为
`2026-08-03 / SYNTH-METAL-GOLD`。

数据库路径：

```text
snapshots/generated/f2a/quantlib_bsm_metals_f2a_parent_v1_20260807.duckdb
```

数据库当前是 `DRAFT` revision `1`。这些查询用于开发检查，不会把它提升为 frozen parent。

## Query catalog

| 文件 | 默认结果 | 边界 | 用途 |
|:---|---:|:---|:---|
| `snapshot_summary.sql` | 1 row | Authoring/audit | Snapshot/config/runtime identity、日期范围、row counts 和 IV status counts。 |
| `underlying_time_series.sql` | 65 rows | Solver-safe | Gold 的完整 OHLCV、dividend 和 corporate-action time series。 |
| `option_chain.sql` | 56 rows | Solver-safe | Listing date Gold 的完整 liquid option chain。 |
| `option_spot_moneyness.sql` | 56 rows | Solver-safe | Chain 加当日 spot、spot moneyness 和 log-moneyness。 |
| `option_pricing_context.sql` | 56 rows | Solver-safe | Chain 加 spot、curves、calendar/day count、BSM/Q identities。 |
| `option_iv_task_inputs.sql` | 56 rows | Solver-safe | Canonical mid、BSM inputs 和冻结的 IV root contract，不含答案。 |
| `option_iv_authoring_answers.sql` | 56 rows | Trusted only | QuantLib IV、raw price、Q effective volatility 和 solver status。 |
| `generation_audit.sql` | 1 row | Authoring/audit | Generation run、table stats、revision 和 error。 |
| `option_chain_authoring_spec.sql` | 1 row | Authoring/audit | Candidate grid、liquidity filter、quote model 和 materialized shape。 |
| `underlying_dependence.sql` | 1 row | Authoring/audit | P-measure driver order、factor loadings、idiosyncratic diagonal 和 correlation matrix。 |
| `underlying_dynamics_authoring_audit.sql` | 22 rows | Authoring/audit | Per-underlying deterministic P drift/volatility functions 与 Q mapping。 |

“Solver-safe”查询只读取 `solver_visible` views。“Trusted only”或“Authoring/audit”会读取
`market`/`metadata`，不得拼入 Solver prompt、tool output、public child 或训练记录。

## Editing parameters

只修改目标 SQL 顶部的 `parameters` CTE：

```sql
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v1',
        DATE '2026-08-03',
        'SYNTH-METAL-GOLD'
    )
)
```

保留文件末尾的稳定 `ORDER BY`。非 business date、snapshot 范围外日期或已到期 slice 会返回空集
或较少 rows，这是数据合同的一部分，不应通过补造 quote 修复。

## Running all queries

单条查询示例：

```bash
.venv/bin/python -c '
from pathlib import Path
import duckdb
db = "snapshots/generated/f2a/quantlib_bsm_metals_f2a_parent_v1_20260807.duckdb"
sql = Path("snapshots/generated/sql_query/snapshot_summary.sql").read_text()
con = duckdb.connect(db, read_only=True)
try:
    print(con.execute(sql).fetchall())
finally:
    con.close()
'
```

在自动化验证中，应逐文件新建或复用一个 read-only connection，确认 query 能 execute，并记录
result column names 和 row count；不得执行 SQL 文本拼接、写操作或隐式 fallback 到 public DB。

## Expected invariants

- `snapshot_summary.sql` 返回 `status=DRAFT`、revision `1` 和 manifest 对应 counts。
- `option_chain.sql` 默认返回 56 行，并满足 `0 <= bid <= mid <= ask`。
- `option_pricing_context.sql` 使用 `Actual365Fixed`、共同 USD Q/numeraire/rate-path identities 和
  `QuantLib.AnalyticEuropeanEngine`。
- `option_iv_task_inputs.sql` 不返回 hidden Q effective volatility、raw theoretical price 或 derived IV。
- `option_iv_authoring_answers.sql` 是 trusted query，不属于 Solver allowlist。
- `underlying_dependence.sql` 的 measure 为 `P`，driver count 为 22；option contracts 不进入 correlation matrix。
- 所有 query 的第一层筛选都包含明确 snapshot identity，结果顺序只由声明的 `ORDER BY` 决定。

目录级生命周期和 visibility 说明见 [generated snapshot README](../README.md)。
