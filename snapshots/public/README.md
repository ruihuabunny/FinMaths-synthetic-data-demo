# Public metals snapshot

本目录提交一个已冻结、可只读重放的 DuckDB 市场快照。数据库文件名沿用早期
`quantlib_bsm_smoke_v1` 以保持路径兼容；当前逻辑世界由 manifest 和数据库内
`snapshot_id` 标识，不再是旧的 5-underlying smoke。

## Snapshot identity

| 字段 | 值 |
|:---|:---|
| Database | `quantlib_bsm_smoke_v1.duckdb` |
| Manifest | `quantlib_bsm_smoke_v1.manifest.json` |
| `snapshot_id` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3` |
| Status / revision | `FROZEN` / `1` |
| Authoring schema | `2.4.0` |
| Generator config | `quantlib-randomized-tdgbm-metals-liquid-option-chain-v3` |
| Generator version | `0.7.0` |
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
| `market.option_pricing_audit` | 60,368 |

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
| `market.option_pricing_audit` | Private provenance | snapshot/date/option | Q identity/mapping、effective volatility、未舍入理论价、canonical mid 和 QuantLib IV 反解状态。 |
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

Physical close paths 使用 config `1.5.0` 继承的 P-measure factor-loading contract：

$$
D=\operatorname{diag}(1-\lVert\lambda_i\rVert^2),
\qquad R=\Lambda\Lambda^\top+D.
$$

`driver_order` 恰好只含这 22 个 underlying IDs。`market.underlying_dependence` 保存
$\Lambda/D/R$，不建立 solver-visible view；1,232 个 option contracts 不进入相关矩阵。

每个 underlying 的 annualized instantaneous `physical_drift` 和
`physical_volatility` 都是 calendar-day-offset piecewise-linear functions。脚本先用记录的
parameter-generator seed `20260806` 在 `[1, 4294967295]` 内抽得 global sampling seed
`2504254580`，再按 underlying ID 分区，为 22 个 underlying 分别抽取互不相同的
per-underlying seed、node-offset grid、drift phi/std 和 log-vol phi/std。首 offset 固定为
0；五个内部 offset 从 `[14, 181]` 无放回抽样，末 offset 从 `[182, 365]` 抽样。

[`scripts/sample_physical_dynamics.py`](../../scripts/sample_physical_dynamics.py) 对每个 phi/std
也声明 continuous-uniform hard bounds；realized values、bounds、offsets 和 per-underlying
seed 全部保存在对应 underlying 的 `physical_sampling_parameters`。Drift value bound 仍为
`[-0.05, 0.15]`，volatility value bound 仍为 `[0.05, 0.80]`。所有参数只抽样一次并冻结；
snapshot replay 只读取 frozen nodes，不会重新抽样。

`2026-08-03` 的 OHLC 是精确 initial condition $S(t_0)=S_0$；没有从虚构的前一日先走一步。
之后每个 observation interval 对 drift 精确积分取平均，对 variance 精确积分取 RMS，再
调用 QuantLib 的 flat-coefficient GBM exact transition。周末跨度按 Actual/365 calendar
time 处理。

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

## Q-measure BSM pricing, IV audit, and quote noise

全部 option mid 使用：

- `QuantLib.BlackScholesMertonProcess`；
- flat continuous risk-free/dividend yields；
- snapshot-wide `USD-MONEY-MARKET-Q-v1` 与 money-market-account numeraire；
- drift-only Girsanov mapping：Q drift 为 $r-q$，且在这个明确的 baseline 假设下
  $\sigma_Q(t)=\sigma_P(t)$；
- valuation-to-expiry integrated variance 对应的 constant-equivalent RMS volatility；
- `QuantLib.AnalyticEuropeanEngine`；
- 8-decimal `ROUND_HALF_EVEN` canonicalization。

对 deterministic $\sigma_Q(t)$，使用
$\sigma_{Q,\mathrm{eff}}=\sqrt{(T-t)^{-1}\int_t^T\sigma_Q^2(u)du}$ 在 European BSM 中
与原 time-inhomogeneous diffusion 具有相同 terminal distribution，因此不是 endpoint-vol
近似。QuantLib NPV 量化后保存为 `mid = settlement_price`。Baseline half-spread 是
`max(0.005, mid × 0.0025)`；bid 和 ask 使用独立、可重放的 Gaussian multipliers，clip
到 `[0.5, 1.5]`。Noise 只作用于 half-spread，不修改 BSM mid、volatility、discounting
或 underlying path，且所有 rows 满足 `0 <= bid <= mid <= ask`。

每条 quote 都在 private `market.option_pricing_audit` 中保存未舍入理论价，并用
`QuantLib.VanillaOption.impliedVolatility` 对实际 canonical mid 反解 IV（bracket
`[1e-6, 4.0]`，accuracy `1e-12`，最多 1,000 evaluations）。59,860 条为 `CONVERGED`；
508 条临近到期、深度价内 quote 因 8 位 mid 落在所声明有限-vol bracket 的可达价格区间
之外而记为 `NO_FINITE_IV`，不会用 hidden pricing volatility 填充假答案。该 audit table
没有 solver-visible view。

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
contract、Q pricing/IV audit、run lineage）没有对应 solver view。当前 `pricing_metadata` 仍是项目早期的
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
| `option_iv_task_inputs.sql` | 56 | Solver-safe |
| `option_iv_authoring_answers.sql` | 56 | Authoring/trusted |
| `generation_audit.sql` | 1 | Authoring/audit |
| `underlying_dynamics_authoring_audit.sql` | 22 | Authoring/audit |

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
  --database /tmp/metals-liquid-tdgbm-q-v2.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  create-smoke

.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v2.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  freeze
```

固定 config、seed 和 RNG namespace 会重放相同 solver-visible market rows。若修改
underlyings、physical function nodes、P-to-Q mapping、$\Lambda$、liquidity rule、quote
model 或已挂牌合约，必须使用新的
`snapshot_id`，不能在现有逻辑 snapshot 内改写。
