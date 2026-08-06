# 常用 DuckDB SQL Query

本目录保存针对 `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3` public snapshot 的可复用只读
查询。每个 `.sql` 文件只包含一条 query，并在文件开头使用 `parameters` CTE 集中声明
可编辑参数。所有结果都显式指定 `ORDER BY`，避免依赖 DuckDB 未定义的天然行顺序。

## Query catalog

| 文件 | 默认结果 | 数据边界 | 用途 |
|:---|---:|:---|:---|
| `snapshot_summary.sql` | 1 row | Authoring/audit | Schema、status、revision、日期范围、master/daily/provenance counts。 |
| `underlying_time_series.sql` | 65 rows | Solver-safe | `SYNTH-METAL-GOLD` 的完整 OHLCV 时间序列。 |
| `option_chain.sql` | 56 rows | Solver-safe | `2026-08-03` Gold 的 4-expiry × 7-strike × call/put liquid chain。 |
| `option_spot_moneyness.sql` | 56 rows | Solver-safe | 同一 chain 加当日 spot 和 `strike / spot_close`。 |
| `option_pricing_context.sql` | 56 rows | Solver-safe | 同一 chain 联表 spot、rate/dividend、day count、BSM model/engine。 |
| `generation_audit.sql` | 1 row | Authoring/audit | Generation run、逐表 stats、revision 和时间戳。 |
| `option_chain_authoring_spec.sql` | 1 row | Authoring/audit | Candidate grid、liquidity filter、quote model 和 materialized chain shape。 |
| `underlying_dependence.sql` | 1 row | Authoring/audit | P-measure driver order、$\Lambda/D/R$、factorization 和 regime。 |

“Solver-safe”表示 SQL 只读取 `solver_visible` views；“Authoring/audit”查询会读取
`market` 或 `metadata`，用于维护和验收 snapshot，不应直接作为 Solver task 输入。

默认 option-chain 行数只适用于 listing date。Static contracts 到期后不再生成 quote：
Gold 在 `2026-09-02` 前有 56 rows，在第一个 expiry 之后会减少 14 rows。若参数日期不是
business date，或不在 `2026-08-03` 至 `2026-10-30` 范围内，查询会返回空集。

## Parameters

查询顶部的参数采用以下含义：

| 参数 | 当前值/范围 | 说明 |
|:---|:---|:---|
| `snapshot_id` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3` | 逻辑 snapshot ID，不是 DuckDB 文件名。 |
| `market_date` | 65 个 business dates | Option rows 还要求 `market_date < expiry`。 |
| `underlying_id` | 22 个 `SYNTH-METAL-*` IDs | 示例统一使用 `SYNTH-METAL-GOLD`。 |

`option_spot_moneyness.sql` 计算的是 valuation date 的 current spot moneyness。Liquidity
filter 使用的是 listing moneyness，并在合约创建时只执行一次；因此 current spot
moneyness 后续移出 `[0.85, 1.15]` 并不表示合约应被删除。

## Running a query

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

默认 `option_chain.sql` 的 result columns 为：

```text
snapshot_id, date, underlying_id, option_id, call_put, strike, expiry,
days_to_expiry,
exercise_style, settlement_type, contract_multiplier,
bid, mid, ask, bid_ask_spread, relative_bid_ask_spread,
settlement_price, volume, open_interest
```

使用其他 snapshot、日期或标的时，只修改目标 SQL 顶部的 `parameters` CTE，不要删除
稳定 `ORDER BY`。Solver 任务仍须由 task contract 固定查询范围、列和排序；这些文件是
公共查询模板，不是 hidden verifier 或 canonical answer。

## Expected semantics

- `option_chain.sql` 返回已挂牌且当日未到期的 contracts，不会动态重建 strike grid。
- `mid = settlement_price` 是经过 8-decimal canonicalization 的 BSM analytic NPV。
- Bid/ask quote noise 只改变 half-spread，所以所有 rows 满足
  `0 <= bid <= mid <= ask`。
- `option_pricing_context.sql` 中的 rate/dividend 是对应 underlying/date 的 flat continuous
  inputs，并同时返回 curve JSON、pricing dynamics、input precision 和 canonicalization；
  pricing model/engine 应分别只有 `Black-Scholes-Merton` 和
  `QuantLib.AnalyticEuropeanEngine`。
- Private liquidity rule、candidate grid、$\Lambda/D/R$ 和 RNG lineage 不在 Solver-safe
  queries 中；分别使用 `option_chain_authoring_spec.sql`、`underlying_dependence.sql` 和
  `generation_audit.sql` 审计。
- `snapshot_summary.sql` 和 `generation_audit.sql` 的 `option_pricing_audit_count` 应为
  60,368；具体 raw price、Q effective volatility 和 derived IV 只在 private
  `market.option_pricing_audit` 中维护，不进入 Solver-safe query。
- 当前 `option_chain_authoring_spec.sql` 应显示 22 个 underlyings、4 个 selected expiries、
  7 个 selected listing-moneyness levels 和 1,232 个 materialized contracts；candidate
  arrays 仍保留完整的 6 expiries 与 11 moneyness levels。
- 当前 `underlying_dependence.sql` 应显示 `measure=P`、22 个 drivers、
  `factor_loading` formulation 和 `factor_loading_direct` construction。
