# Public metals snapshot

本目录提交一个已冻结、可只读重放的 DuckDB 市场快照。数据库文件名沿用早期
`quantlib_bsm_smoke_v1` 以保持路径兼容；当前逻辑世界由 manifest 和数据库内
`snapshot_id` 标识，不再是旧的 5-underlying smoke。

## Snapshot identity

| 字段 | 值 |
|:---|:---|
| Database | `quantlib_bsm_smoke_v1.duckdb` |
| Manifest | `quantlib_bsm_smoke_v1.manifest.json` |
| `snapshot_id` | `DERIVATIVES-METALS-LIQUID-BSM-v1` |
| Status / revision | `FROZEN` / `1` |
| Authoring schema | `2.3.0` |
| Generator config | `quantlib-bsm-metals-liquid-option-chain-v1` |
| Generator version | `0.5.0` |
| QuantLib / DuckDB | `1.39` / `1.5.5` |
| Seed | `20260806` |
| Business-date range | `2026-08-03` through `2026-10-30` |

对应的可重放配置是
[`configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json`](../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)。

## Logical size

| 对象 | 行数 |
|:---|---:|
| `market.underlyings` | 22 |
| `market.underlying_dependence` | 1 |
| `market.option_chain_specs` | 1 |
| `market.option_contracts` | 1,232 |
| `market.underlying_daily` | 1,430 |
| `market.option_daily` | 60,368 |
| `market.pricing_metadata` | 1,430 |

65 个 business dates × 22 个 underlyings 得到 1,430 条 underlying/metadata rows。
Option quotes 只生成到 expiry 前，因此不是 `1,232 × 65`：

| Expiry | 合约数 | 有报价日期数 | Quote rows |
|:---|---:|---:|---:|
| `2026-09-02` | 308 | 22 | 6,776 |
| `2026-10-02` | 308 | 44 | 13,552 |
| `2026-11-02` | 308 | 65 | 20,020 |
| `2027-02-01` | 308 | 65 | 20,020 |

## Database layout

DuckDB 包含三个 SQL schemas：

| Schema / object | 可见性 | 主键或粒度 | 说明 |
|:---|:---|:---|:---|
| `solver_visible.underlying_daily` | Solver-safe | snapshot/date/underlying | 65 日 OHLCV、adjusted close、dividend、corporate action。 |
| `solver_visible.option_daily` | Solver-safe | snapshot/date/option | 未到期合约的 bid/ask/mid、静态 terms、volume/open interest。 |
| `solver_visible.pricing_metadata` | Solver-safe（过渡合同） | snapshot/timestamp/underlying | Curves、rate/dividend、calendar/day count 和 pricing context。 |
| `market.underlyings` | Authoring | snapshot/underlying | 22 个 underlying master definitions。 |
| `market.option_contracts` | Authoring | snapshot/option | 1,232 个 frozen listed contracts 与 listing provenance。 |
| `market.underlying_dependence` | Private provenance | snapshot/dependence spec | P-measure $\Lambda/D/R$ 及 driver order。 |
| `market.option_chain_specs` | Private provenance | snapshot/chain | Candidate grid、liquidity filter、quote model 与 listing/roll rules。 |
| `metadata.snapshots` | Audit | snapshot | Schema/config/generator version、status、seed 和 revision。 |
| `metadata.generation_runs` | Audit | run | Operation、date range、table stats、status 和 error。 |
| `metadata.snapshot_revisions` | Audit | snapshot/revision | 每个 revision 的逻辑 row counts。 |

“Private provenance”表示没有对应的 `solver_visible` view；由于这是单文件 demo，拥有完整
DuckDB 文件的 maintainer 仍可读取 `market`/`metadata`。生产任务应导出 solver views 或用
数据库权限真正隔离 authoring provenance。

## Underlyings and dependence

22 个 synthetic metal underlyings 包括 aluminum、copper、zinc、lead、nickel、tin、
gold、silver、platinum、palladium、cobalt、molybdenum、iron ore、steel rebar、
lithium、uranium、rhodium、magnesium、manganese、chromium、vanadium 和 tungsten。

Physical close paths 使用 config `1.4.0` 继承的 P-measure factor-loading contract：

$$
D=\operatorname{diag}(1-\lVert\lambda_i\rVert^2),
\qquad R=\Lambda\Lambda^\top+D.
$$

`driver_order` 恰好只含这 22 个 underlying IDs。`market.underlying_dependence` 保存
$\Lambda/D/R$，不建立 solver-visible view；1,232 个 option contracts 不进入相关矩阵。

## Liquid option chain

Authoring config 声明 6 × 11 × 2 的 candidate grid：

- expiry offsets: `30, 60, 90, 180, 270, 365` calendar days；
- listing moneyness: `0.70, 0.80, 0.85, ..., 1.15, 1.20, 1.30`；
- paired European call/put。

Private `liquidity_filter` 使用 inclusive rules：

- `max_expiry_days = 180`；
- `0.85 <= listing_moneyness <= 1.15`。

因此每个 underlying 只挂牌 `4 expiries × 7 strikes × 2 call/put = 56` 个合约。
270/365 日和 moneyness band 外的 candidates 不进入 contract master，也不会生成 quote。
90/180 日到期日按 `WeekendsOnly/Following` 调整，故实际日期分别比原始 offset 多 1/2
个日历日。

Moneyness 只在 listing date 用 initial spot 转换一次，并以 0.5 strike increment 和
`ROUND_HALF_EVEN` 冻结 absolute strike。后续 spot 变化不会重新定 strike，也不会让
合约动态进出 liquidity band。

## BSM pricing and quote noise

全部 option mid 使用：

- `QuantLib.BlackScholesMertonProcess`；
- flat continuous risk-free/dividend yields；
- constant volatility per underlying（smile skew/curvature/term slope 均为 0）；
- `QuantLib.AnalyticEuropeanEngine`；
- 8-decimal `ROUND_HALF_EVEN` canonicalization。

QuantLib NPV 保存为 `mid = settlement_price`。Baseline half-spread 是
`max(0.005, mid × 0.0025)`；bid 和 ask 使用独立、可重放的 Gaussian multipliers，clip
到 `[0.5, 1.5]`。Noise 只作用于 half-spread，不修改 BSM mid、volatility、discounting
或 underlying path，且所有 rows 满足 `0 <= bid <= mid <= ask`。

当前验收结果：put-call parity 最大绝对误差约 `1.0e-8`（来自 8 位价格量化），贴现
European call/put bounds 无违规。这里的 no-arbitrage 来自合法 BSM pricing model、共同
valuation convention 和 discounting；P-measure correlation matrix 本身不是
no-arbitrage 条件。

## Visibility boundary

Solver 可读 views：

- `solver_visible.underlying_daily`
- `solver_visible.option_daily`
- `solver_visible.pricing_metadata`

Private authoring/audit tables（包括 dependence、candidate-grid liquidity rule、quote-noise
contract、run lineage）没有对应 solver view。当前 `pricing_metadata` 仍是项目早期的
过渡公开合同；更严格的 public/private pricing metadata split 属于后续阶段。

## Read-only queries

[`sql_query/`](sql_query/README.md) 提供 snapshot summary、Gold time series、Gold option
chain、spot moneyness、pricing context 和 generation audit 查询。默认返回规模：

| Query | Rows | 读取范围 |
|:---|---:|:---|
| `snapshot_summary.sql` | 1 | Authoring/audit |
| `underlying_time_series.sql` | 65 | Solver-safe |
| `option_chain.sql` | 56 | Solver-safe |
| `option_spot_moneyness.sql` | 56 | Solver-safe |
| `option_pricing_context.sql` | 56 | Solver-safe |
| `generation_audit.sql` | 1 | Authoring/audit |

示例：

```python
from pathlib import Path

import duckdb

database = "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
query = Path("snapshots/public/sql_query/option_chain.sql").read_text()
connection = duckdb.connect(database, read_only=True)
try:
    rows = connection.execute(query).fetchall()
finally:
    connection.close()
```

所有 SQL 顶部都有 `parameters` CTE。修改日期或 underlying 时应保留原有 `ORDER BY`；
非 business date、snapshot 范围外日期或已到期 option slice 会自然返回较少 rows 或空集。
`option_spot_moneyness.sql` 展示的是当日 `strike / spot_close`，不是用于挂牌筛选的 listing
moneyness；static contract 不会因为 spot 移动而重新进入或退出 chain。

快速核对 manifest 对应的数据库 identity：

```sql
SELECT snapshot_id, schema_version, status, current_revision,
       generator_config_id, generator_version
FROM metadata.snapshots;
```

快速核对 effective liquid chain：

```sql
SELECT
    count(*) AS contract_count,
    count(DISTINCT underlying_id) AS underlying_count,
    count(DISTINCT expiry) AS expiry_count,
    count(DISTINCT strike_moneyness) AS listing_moneyness_count,
    min(strike_moneyness) AS min_listing_moneyness,
    max(strike_moneyness) AS max_listing_moneyness
FROM market.option_contracts;
```

预期结果是 `1232, 22, 4, 7, 0.85, 1.15`。Candidate grid 和被 filter 排除的
expiry/moneyness 只保存在 `market.option_chain_specs`，不会作为空壳 contracts 出现在
master table。

## Rebuild without mutating the public file

Public snapshot 已冻结。重放时写到新路径：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-bsm-v1.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  create-smoke

.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-bsm-v1.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  freeze
```

固定 config、seed 和 RNG namespace 会重放相同 solver-visible market rows。若修改
underlyings、$\Lambda$、liquidity rule、quote model 或已挂牌合约，必须使用新的
`snapshot_id`，不能在现有逻辑 snapshot 内改写。
