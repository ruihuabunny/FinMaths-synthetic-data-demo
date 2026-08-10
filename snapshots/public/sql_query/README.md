# 常用 DuckDB SQL Query

本目录保存针对 `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3` public snapshot 的可复用只读
查询。每个 `.sql` 文件只包含一条 query，并在文件开头使用 `parameters` CTE 集中声明
可编辑参数。所有结果都显式指定 `ORDER BY`，避免依赖 DuckDB 未定义的天然行顺序。

## Query catalog

| 文件 | 默认结果 | 数据边界 | 用途 |
|:---|---:|:---|:---|
| `snapshot_summary.sql` | 1 row | Authoring/audit | Schema、runtime/config identity、日期范围、master/daily/provenance counts 和 IV 状态计数。 |
| `underlying_time_series.sql` | 65 rows | Solver-safe | `SYNTH-METAL-GOLD` 的完整 OHLCV 时间序列。 |
| `option_chain.sql` | 56 rows | Solver-safe | `2026-08-03` Gold 的 4-expiry × 7-strike × call/put liquid chain。 |
| `option_spot_moneyness.sql` | 56 rows | Solver-safe | 同一 chain 加当日 spot 和 `strike / spot_close`。 |
| `option_pricing_context.sql` | 56 rows | Solver-safe | 同一 chain 联表 spot、rate/dividend、day count、BSM model/engine 和 Q identity；不返回完整 latent dynamics JSON。 |
| `option_iv_task_inputs.sql` | 56 rows | Solver-safe | IV agent task 输入：canonical mid、BSM inputs、Q identity、时间与 root-solver contract，不含答案或生成 volatility。 |
| `option_iv_authoring_answers.sql` | 56 rows | Authoring/trusted | 与上述默认 slice 对齐的 QuantLib IV、状态、raw price 和 Q effective volatility；只供 trusted verifier。 |
| `generation_audit.sql` | 1 row | Authoring/audit | Generation run、逐表 stats、revision 和时间戳。 |
| `option_chain_authoring_spec.sql` | 1 row | Authoring/audit | Candidate grid、liquidity filter、quote model 和 materialized chain shape。 |
| `underlying_dependence.sql` | 1 row | Authoring/audit | P-measure driver order、$\Lambda/D/R$、factorization 和 regime。 |
| `underlying_dynamics_authoring_audit.sql` | 22 rows | Authoring/audit | 每只 underlying 的 day-0 master/function 一致性、piecewise-linear P drift/vol functions 及 Q mapping。 |

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

这些 SQL 查询单个 DuckDB 内部的数据与 provenance。若要从当前 generator JSON 完整重放并
与 public DuckDB 做跨库精确比较，请运行：

```bash
.venv/bin/python scripts/replay_snapshot.py
```

该脚本比较 8 张 `market` 表、4 张 `metadata` 表和 manifest；run UUID、审计时间戳及
DuckDB 物理布局不属于生成的逻辑市场数据，因此不参与一致性判定。

构造 IV agent task 时，优先使用 `option_iv_task_inputs.sql`。它只给求解所需公开输入；
`option_iv_authoring_answers.sql` 必须留在 trusted authoring/verifier 边界，不能拼入 prompt、
tool output 或 Solver 可访问的数据文件。默认日期的 56 条 Gold options 均有 finite IV；
改变日期/underlying 后必须保留 `iv_status`，不能给 `NO_FINITE_IV` 行补造答案。

使用其他 snapshot、日期或标的时，只修改目标 SQL 顶部的 `parameters` CTE，不要删除
稳定 `ORDER BY`。Solver 任务仍须由 task contract 固定查询范围、列和排序；这些文件是
公共查询模板，不是 hidden verifier 或 canonical answer。

## Expected semantics

- `option_chain.sql` 返回已挂牌且当日未到期的 contracts，不会动态重建 strike grid。
- `mid = settlement_price` 是经过 8-decimal canonicalization 的 BSM analytic NPV。
- Bid/ask quote noise 只改变 half-spread，所以所有 rows 满足
  `0 <= bid <= mid <= ask`。
- `option_pricing_context.sql` 中的 rate/dividend 是对应 underlying/date 的 flat continuous
  inputs，并返回 curve JSON、Q identity、measure change、volatility mapping、input precision
  和 canonicalization；
  pricing model/engine 应分别只有 `Black-Scholes-Merton` 和
  `QuantLib.AnalyticEuropeanEngine`。
- `option_iv_task_inputs.sql` 不返回 `q_effective_volatility`、raw theoretical price、derived
  IV 或整块 `pricing_dynamics`；这些字段只存在于 trusted answer query。
- Private liquidity rule、candidate grid、$\Lambda/D/R$ 和 RNG lineage 不在 Solver-safe
  queries 中；分别使用 `option_chain_authoring_spec.sql`、`underlying_dependence.sql` 和
  `generation_audit.sql` 审计。
- `snapshot_summary.sql` 和 `generation_audit.sql` 的 `option_pricing_audit_count` 应为
  60,368；summary 还应显示 `CONVERGED=59,836`、`NO_FINITE_IV=532`。具体 raw price、
  Q effective volatility 和 derived IV 只在 private
  `market.option_pricing_audit` 中维护，不进入 Solver-safe query。
- 当前 `option_chain_authoring_spec.sql` 应显示 22 个 underlyings、4 个 selected expiries、
  7 个 selected listing-moneyness levels 和 1,232 个 materialized contracts；candidate
  arrays 仍保留完整的 6 expiries 与 11 moneyness levels。
- 当前 `underlying_dependence.sql` 应显示 `measure=P`、22 个 drivers、
  `factor_loading` formulation 和 `factor_loading_direct` construction。
- `underlying_dynamics_authoring_audit.sql` 应返回 22 行，每行各有 7 个 drift nodes 与 7 个
  volatility nodes，且两个 day-0 match 标志都为 true；sampling seeds/hyperparameters 的
  完整 provenance 在 generator config，不作为 Solver task input。

以上计数和审计语义只描述 checked-in 的历史 config `1.5.0` authoring demo。Current
`1.6.0+` materialization 不写 authoring IV answers；accepted D4 agent task 使用独立三关系
DuckDB 并由 trusted adapters 读取，不复用本目录的 authoring-audit SQL。
