# F2A frozen parent DuckDB 数据字典与使用合同

本文档是当前 F2A clean parent 的数据库级说明，面向三类使用者：

1. 实现 copy-on-write child materializer 和 point mutation 的 Authoring 开发者；
2. 编写数据关系、cashflow、oracle、integration 和 replay tests 的开发者；
3. 从 public child 测试 LLM 的任务构建者。

本文档描述的是**当前实际物化的 parent**。F2A 的完整数学、交易策略和 ORM 设计仍以
[`f2a_arbitrage_finding_agent_task_plan.md`](../../../../../src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md)
为准；generator source of truth 是
[`quantlib_bsm_metals_f2a_parent_v2.json`](../../../../../configs/generators/quantlib_bsm_metals_f2a_parent_v2.json)。

作用域仅限 executable F2A v4。Full-trajectory v5.1 必须使用 distinct
`DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1` 126-day parent；本 v2 parent 不能
作为 v5.1 qualification fallback，其 65-day/七节点路径也不满足 v5.1 三节点合同。

> 重要：这个文件既是数据字典，也包含 Authoring-only 字段说明。生产 Solver/LLM 不应获得整个
> parent、整个仓库或本文的 Authoring-only 部分。LLM task bundle 应只包含物化后的最小 public
> child、public task/variant contract、submission/trajectory schemas，以及本文第 12 节抽取出的任务指引。

## 1. 文件身份与只读规则

| 项目 | 值 |
|:---|:---|
| Database | `parent.duckdb` |
| Sidecar manifest | `parent.manifest.json` |
| Logical snapshot | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2` |
| Status / revision | `FROZEN / 1` |
| Authoring schema | `2.4.0` |
| Generator config id | `quantlib-randomized-tdgbm-metals-f2a-parent-v2` |
| Generator version | `0.8.0` |
| QuantLib / DuckDB | `1.39 / 1.5.5` |
| Seed | `20260806` |
| Date range | `2026-08-03` through `2026-10-30` |
| Observation calendar / clock | `WeekendsOnly` / `Actual365Fixed`; valuation time `16:00:00 UTC` |
| Currency | `USD` |
| Underlying / option minimum price increments | `0.01 USD / 0.01 USD` |
| Decimal storage | `DECIMAL(24,8)` with `ROUND_HALF_EVEN` at declared checkpoints |

生命周期规则：

- Parent 只能用 `read_only=True` 打开；禁止 `append-dates`、`sync-config`、`sync-range`、`freeze`
  重跑、SQL `INSERT/UPDATE/DELETE` 或直接文件编辑。
- Parent 缺失时必须报告，不能 fallback 到 active development v3 或 public v3。
- 每个 mutation 必须产生新的 child `snapshot_id/revision`，先 DRAFT materialize，通过 gates 后 freeze。
- Parent、child 和 task IDs 不编码 operator、target、requested/realized signature 或 positive/negative
  status。
- 不需要、也不应为普通开发检查计算 checksum；身份由路径、数据库内 metadata 和 manifest 的
  versioned IDs 确定。

只读连接：

```python
import duckdb

database = (
    "snapshots/generated/f2a/parents/"
    "DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/parent.duckdb"
)
connection = duckdb.connect(database, read_only=True)
```

`TIMESTAMPTZ` 会按 DuckDB session timezone 显示。连接关系必须按 UTC 日期处理：

```sql
CAST(valuation_timestamp AT TIME ZONE 'UTC' AS DATE)
```

不要直接把本地显示日期与 `market_date` 比较。例如 `2026-08-03 16:00:00 UTC` 在
`Asia/Shanghai` 可显示成 `2026-08-04 00:00:00+08`，但它的 valuation date 仍是
`2026-08-03`。

## 2. 数学与经济对象

### 2.1 Underlying history

`market.underlying_daily` 是物理测度 `P` 下的 synthetic ex-dividend spot history。当前 baseline 为
deterministic time-inhomogeneous GBM：

\[
\frac{dS_t}{S_t}=\mu_P(t)dt+\sigma_P(t)dW_t^P.
\]

- `start_date` 行是 `S(t0)=initial_spot`，不从虚构的前一日抽 shock。
- 后续 business observations 跨真实 calendar interval 演化；piecewise-linear drift 做区间积分平均，
  volatility 对方差率精确积分后取 RMS。
- Close 在每次 transition 后按 `0.01 USD` 量化；量化 close 是下一步 restart state，因此这是明确的
  rounded-state Markov chain。
- 22 个 P-measure close shocks 由 `market.underlying_dependence` 的 factor-loading law 联合生成。
- OHLC range、volume、dividend 和 corporate action 有独立规则，不是 spot GBM 自动产生的量。

### 2.2 Option quotes

Option quotes 属于共同 money-market-numeraire pricing measure
`Q = USD-MONEY-MARKET-Q-v1`。在当前 deterministic BSM baseline 中：

\[
\frac{dS_t}{S_t}=(r-q)dt+\sigma_Q(t)dW_t^Q,
\qquad \sigma_Q(t)=\sigma_P(t)
\]

是显式的 drift-only Girsanov baseline 假设，不是 physical volatility 与 IV 相等的通用事实。
每个 valuation/expiry 区间对 `sigma_Q(t)^2` 精确积分并取 RMS，再由
`QuantLib.AnalyticEuropeanEngine` 计算 European cash-settled BSM mid。

当前 flat continuously compounded inputs 为：

- `r = 0.03`；
- `q = 0.01`；
- `borrow_or_carry_rate = r - q = 0.02`（binary64 显示可能为
  `0.019999999999999997`）；
- option contract multiplier `M = 100`。

`mid = settlement_price` 在 parent 的全部 60,368 行成立。Bid/ask 只在 BSM mid 两侧加入
deterministic noisy half-spread；volume/open interest 来自单独的 deterministic activity rule。
当前 authoring 不求解或持久化 IV，所以 `market.option_pricing_audit` 保留 schema 但有 0 行。

### 2.3 Dependence 与 arbitrage 的边界

`market.underlying_dependence` 只描述 `P`-measure underlying close shocks。它不是 Q-measure
multi-asset dependence，不能用于 basket/joint option pricing，也不能把 option IDs 放入 correlation
matrix。

Parent quotes 来自一个 coherent BSM marginal generator，但这不等同于对任意交易成本策略类的全市场
no-arbitrage 证明。F2A truth 只由 public variant 的 frozen finite candidate catalogue 定义。Quote
与 BSM theoretical value 不同只直接表示 model inconsistency；`false/[]` 也只表示 catalogue 内没有
positive exact certificate。

## 3. 实际数据规模与形状

| Object | Rows | 说明 |
|:---|---:|:---|
| `metadata.schema_versions` | 1 | Schema `2.4.0`。 |
| `metadata.snapshots` | 1 | Parent identity 与 lifecycle。 |
| `metadata.generation_runs` | 1 | 一次 completed create/sync run。 |
| `metadata.snapshot_revisions` | 1 | Revision 1 row counts。 |
| `market.underlyings` | 22 | Immutable underlying master。 |
| `market.underlying_dependence` | 1 | 22-driver、3-factor P-measure dependence spec。 |
| `market.option_chain_specs` | 1 | Static chain/listing/filter/quote model。 |
| `market.option_contracts` | 1,232 | `22 × 4 expiries × 7 strikes × call/put`。 |
| `market.underlying_daily` | 1,430 | `22 × 65 business dates`。 |
| `market.option_daily` | 60,368 | 只在 `date < expiry` 时有 quote。 |
| `market.pricing_metadata` | 1,430 | 每 underlying/date 一条 P/Q context。 |
| `market.option_pricing_audit` | 0 | Legacy empty table。 |
| `solver_visible.underlying_daily` | 1,430 | Parent view。 |
| `solver_visible.option_daily` | 60,368 | Parent view。 |
| `solver_visible.pricing_metadata` | 1,430 | Parent view；当前不够窄，不能原样给 F2A Solver。 |

Option contracts 的四个 expiries 为：

| Expiry | Contracts | 每 underlying |
|:---|---:|---:|
| `2026-09-02` | 308 | 14 |
| `2026-10-02` | 308 | 14 |
| `2026-11-02` | 308 | 14 |
| `2027-02-01` | 308 | 14 |

因为 expiry 当日不报价，daily quote rows 分三段：

| Valuation dates | Rows/day | Live contracts/underlying | Live expiries |
|:---|---:|---:|---:|
| `2026-08-03` through `2026-09-01` | 1,232 | 56 | 4 |
| `2026-09-02` through `2026-10-01` | 924 | 42 | 3 |
| `2026-10-02` through `2026-10-30` | 616 | 28 | 2 |

V4 F2A authoring 要求完整 56-row chain，因此实际 eligible universe 是
`22 dates × 22 underlyings = 484` 个 `(valuation_date, underlying_id)` slices，日期范围为
`2026-08-03` through `2026-09-01`。后续 slices 必须 skip，不能补 quote 或缩小任务合同。

22 个 underlyings 与 initial spots：

| Underlying id | Initial spot | Underlying id | Initial spot |
|:---|---:|:---|---:|
| `SYNTH-METAL-ALUMINUM` | 100.00 | `SYNTH-METAL-MOLYBDENUM` | 108.00 |
| `SYNTH-METAL-CHROMIUM` | 102.00 | `SYNTH-METAL-NICKEL` | 115.00 |
| `SYNTH-METAL-COBALT` | 98.00 | `SYNTH-METAL-PALLADIUM` | 145.00 |
| `SYNTH-METAL-COPPER` | 105.00 | `SYNTH-METAL-PLATINUM` | 140.00 |
| `SYNTH-METAL-GOLD` | 150.00 | `SYNTH-METAL-RHODIUM` | 160.00 |
| `SYNTH-METAL-IRON-ORE` | 82.00 | `SYNTH-METAL-SILVER` | 135.00 |
| `SYNTH-METAL-LEAD` | 88.00 | `SYNTH-METAL-STEEL-REBAR` | 86.00 |
| `SYNTH-METAL-LITHIUM` | 118.00 | `SYNTH-METAL-TIN` | 125.00 |
| `SYNTH-METAL-MAGNESIUM` | 94.00 | `SYNTH-METAL-TUNGSTEN` | 112.00 |
| `SYNTH-METAL-MANGANESE` | 90.00 | `SYNTH-METAL-URANIUM` | 128.00 |
| `SYNTH-METAL-ZINC` | 92.00 | `SYNTH-METAL-VANADIUM` | 96.00 |

注意：master 中 `asset_class` 的实际值是 `synthetic_equity`；metal 只在 synthetic instrument ID/profile
中表达，不能把它解释成真实现货、期货或仓单市场。

## 4. 表关系总览

DuckDB 中定义了 primary keys 和 CHECK constraints，但**没有声明 FOREIGN KEY constraints**。
下面的关系是 authoring contract 的逻辑外键；materializer 和 tests 必须显式执行这些 joins 和
一致性检查。

```text
metadata.schema_versions

metadata.snapshots (snapshot_id)
├── metadata.generation_runs (snapshot_id, run_id)
│   ├── metadata.snapshot_revisions (snapshot_id, revision, run_id)
│   └── all market rows (..._run_id -> generation_runs.run_id)
│
├── market.underlyings (snapshot_id, underlying_id)
│   ├── market.underlying_daily (snapshot_id, date, underlying_id)
│   ├── market.pricing_metadata
│   │     (snapshot_id, UTC(valuation_timestamp)::DATE, underlying_id)
│   └── market.option_contracts (snapshot_id, underlying_id, option_id)
│         └── market.option_daily (snapshot_id, date, option_id)
│               ├── underlying_daily by (snapshot_id, date, underlying_id)
│               └── pricing_metadata by
│                   (snapshot_id, UTC timestamp date, underlying_id)
│
├── market.option_chain_specs (snapshot_id, chain_id)
│   └── market.option_contracts (snapshot_id, chain_id)
│
└── market.underlying_dependence (snapshot_id, dependence_spec_id)
    └── pricing_metadata.physical_dynamics.dependence_spec_id

market.option_pricing_audit (empty)
└── would join option_daily by (snapshot_id, date, option_id)
```

### 4.1 关联键与 cardinality

| From | To | Join condition | 当前 cardinality / invariant |
|:---|:---|:---|:---|
| Any table | `metadata.snapshots` | `snapshot_id` | 全库只有一个 snapshot id。 |
| `snapshot_revisions` | `generation_runs` | `(snapshot_id, run_id)` | Revision 1 来自唯一 completed run。 |
| Market masters/daily | `generation_runs` | `created_run_id` 或 `generated_run_id = run_id` | 当前所有 rows 都指向同一 run。 |
| `option_contracts` | `underlyings` | `(snapshot_id, underlying_id)` | 每 underlying 56 contracts。 |
| `underlying_daily` | `underlyings` | `(snapshot_id, underlying_id)` | 每 underlying 65 dates。 |
| `pricing_metadata` | `underlyings` | `(snapshot_id, underlying_id)` | 每 underlying 65 timestamps。 |
| `option_contracts` | `option_chain_specs` | `(snapshot_id, chain_id)` | 1,232 contracts 全部属于唯一 chain。 |
| `option_daily` | `option_contracts` | `(snapshot_id, option_id)` | Daily row 的 denormalized terms 必须与 master 完全相同。 |
| `option_daily` | `underlying_daily` | `(snapshot_id, date, underlying_id)` | 每 quote 使用同 slice 的 `spot_close`。 |
| `option_daily` | `pricing_metadata` | `snapshot_id`, `underlying_id`, `date = UTC(valuation_timestamp)::DATE` | 每 quote 有唯一 curve/model context。 |
| `physical_dynamics` JSON | `underlying_dependence` | `(snapshot_id, dependence_spec_id)` | 所有 1,430 rows 引用 `SYNTH-METALS-P-SPOT-FACTOR-v1`。 |

对当前文件的 orphan/mismatch audit 均返回 0，包括 contract→underlying、contract→chain、
daily→master、option daily→spot/pricing context，以及 option daily denormalized contract terms。
这不允许省略当前 child gates；child 仍必须重新检查自己的公开投影。

### 4.2 Option daily 的 denormalized contract terms

`market.option_daily` 重复保存 `underlying_id`、`call_put`、`strike`、`expiry`、
`exercise_style`、`settlement_type` 和 `contract_multiplier`，目的是让 daily quote 自包含。它们
不是新的可变字段，必须与
`market.option_contracts` 按 `(snapshot_id, option_id)` 连接后完全一致。

Mutation option price 时只改变一个 logical quote point 的 `mid`，再确定性派生 `bid/ask`；不能改变
任何 contract term。Mutation spot 时只改变 child 的 `spot_close`；不能改变 option rows 或 contract
terms。

## 5. `metadata` schema：全部字段

### 5.1 `metadata.schema_versions`

Primary key: `schema_version`。当前 1 row。

| Field | Type | 含义 |
|:---|:---|:---|
| `schema_version` | `VARCHAR` | DuckDB authoring schema version；当前 `2.4.0`。 |
| `applied_at` | `TIMESTAMPTZ` | Schema 首次应用时间。Authoring-only。 |

### 5.2 `metadata.snapshots`

Primary key: `snapshot_id`。当前 1 row。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | 全库逻辑 identity；所有 market rows 的第一关联键。 |
| `schema_version` | `VARCHAR` | 当前 `2.4.0`。 |
| `status` | `VARCHAR` | `DRAFT` 或 `FROZEN`；当前必须为 `FROZEN`。 |
| `generator_config_id` | `VARCHAR` | 生成该 snapshot 的 immutable config id。 |
| `generator_version` | `VARCHAR` | 当前 `0.8.0`。 |
| `quantlib_version` | `VARCHAR` | Authoring QuantLib version，当前 `1.39`。 |
| `duckdb_version` | `VARCHAR` | Materialization DuckDB version，当前 `1.5.5`。 |
| `seed` | `UBIGINT` | Authoring RNG seed；private provenance，不给 Solver。 |
| `rng` | `VARCHAR` | 完整 RNG/stream-partition identity；private provenance。 |
| `current_revision` | `INTEGER` | 当前 revision，值为 `1`。 |
| `created_at` | `TIMESTAMPTZ` | Snapshot 创建时间。 |
| `updated_at` | `TIMESTAMPTZ` | 最近 lifecycle update 时间。 |
| `frozen_at` | `TIMESTAMPTZ NULL` | Freeze 时间；FROZEN parent 必须非空。 |

### 5.3 `metadata.generation_runs`

Primary key: `run_id`。当前 1 row，operation `CREATE_OR_SYNC`、status `COMPLETED`。

| Field | Type | 含义 |
|:---|:---|:---|
| `run_id` | `VARCHAR` | 一次 transaction/run identity；被所有 `created_run_id/generated_run_id` 引用。 |
| `snapshot_id` | `VARCHAR` | 所属 snapshot。 |
| `operation` | `VARCHAR` | Authoring operation；当前 `CREATE_OR_SYNC`。 |
| `status` | `VARCHAR` | `RUNNING/COMPLETED/NOOP/FAILED`；当前 `COMPLETED`。 |
| `generator_config_id` | `VARCHAR` | 该 run 使用的 config id。 |
| `generator_version` | `VARCHAR` | 该 run 使用的 generator version。 |
| `requested_start_date` | `DATE NULL` | 请求生成区间起点；当前 `2026-08-03`。 |
| `requested_end_date` | `DATE NULL` | 请求生成区间终点；当前 `2026-10-30`。 |
| `table_stats` | `JSON NULL` | 每张 current-output table 的 `requested/inserted/unchanged` counts。 |
| `error_message` | `VARCHAR NULL` | Failed run error；当前为 null。 |
| `started_at` | `TIMESTAMPTZ` | Run 开始时间。 |
| `completed_at` | `TIMESTAMPTZ NULL` | Run 完成时间。 |

`table_stats` 当前包含 `underlyings`、`underlying_dependence`、`option_chain_specs`、
`option_contracts`、`underlying_daily`、`option_daily` 和 `pricing_metadata` 七个 keys；每个 value
形如：

```json
{"inserted": 1430, "requested": 1430, "unchanged": 0}
```

### 5.4 `metadata.snapshot_revisions`

Primary key: `(snapshot_id, revision)`。当前 1 row。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `revision` | `INTEGER` | Revision number；当前 `1`。 |
| `run_id` | `VARCHAR` | 产生该 revision 的 generation run。 |
| `underlying_count` | `BIGINT` | `market.underlyings` count，22。 |
| `option_contract_count` | `BIGINT` | `market.option_contracts` count，1,232。 |
| `underlying_daily_count` | `BIGINT` | `market.underlying_daily` count，1,430。 |
| `option_daily_count` | `BIGINT` | `market.option_daily` count，60,368。 |
| `pricing_metadata_count` | `BIGINT` | `market.pricing_metadata` count，1,430。 |
| `underlying_dependence_count` | `BIGINT` | P-dependence specs count，1。 |
| `option_chain_spec_count` | `BIGINT` | Chain specs count，1。 |
| `option_pricing_audit_count` | `BIGINT` | Legacy audit count，0。 |
| `created_at` | `TIMESTAMPTZ` | Revision commit time。 |

## 6. `market` master/provenance tables：全部字段

### 6.1 `market.underlyings`

Primary key: `(snapshot_id, underlying_id)`。当前 22 rows。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `underlying_id` | `VARCHAR` | Stable synthetic instrument id。 |
| `currency` | `VARCHAR` | Quote/settlement currency；全部 `USD`。 |
| `asset_class` | `VARCHAR` | 当前实际值 `synthetic_equity`。 |
| `initial_spot` | `DECIMAL(24,8)` | `start_date` 初始条件，正数且落在 `0.01` grid。 |
| `physical_drift` | `DOUBLE` | `P`-measure instantaneous annualized drift function 在 time origin 的值，单位 `year^-1`。完整函数见 `physical_dynamics`。 |
| `physical_volatility` | `DOUBLE` | `P`-measure instantaneous annualized return volatility function 在 time origin 的值，单位 `year^-1/2`。 |
| `risk_free_rate` | `DOUBLE` | Flat continuously compounded Q-pricing risk-free rate；全部 `0.03`。 |
| `dividend_yield` | `DOUBLE` | Flat continuous dividend/carry yield；全部 `0.01`。 |
| `base_implied_volatility` | `DOUBLE NULL` | Config <=1.4 legacy latent field；此 parent 22 rows 全部 null，不是 IV answer。 |
| `generator_config_id` | `VARCHAR` | Master row 的 generator config id。 |
| `created_run_id` | `VARCHAR` | 创建 master row 的 run id。 |

### 6.2 `market.underlying_dependence`（Authoring-only）

Primary key: `(snapshot_id, dependence_spec_id)`。当前 1 row。

因子模型为：

\[
D=\operatorname{diag}(1-\lVert\lambda_i\rVert^2),\quad
R=\Lambda\Lambda^\top+D,\quad
Z=\Lambda\eta+\sqrt{D}\epsilon.
\]

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `dependence_spec_id` | `VARCHAR` | 当前 `SYNTH-METALS-P-SPOT-FACTOR-v1`。 |
| `measure` | `VARCHAR` | 固定 `P`；不是 Q dependence。 |
| `driver_order` | `JSON` | 22 个 underlying driver IDs 的冻结矩阵顺序。 |
| `formulation` | `VARCHAR` | 固定 `factor_loading`。 |
| `factor_loading_matrix` | `JSON` | `22 × 3` binary64 factor loadings `Lambda`。 |
| `idiosyncratic_diagonal` | `JSON` | 长度 22 的 `D_ii = 1-||lambda_i||^2`。 |
| `correlation_matrix` | `JSON` | `22 × 22` symmetric PSD matrix `R`，unit diagonal。 |
| `matrix_dtype` | `VARCHAR` | 固定 `float64`。 |
| `factorization_method` | `VARCHAR` | 固定 `factor_loading_direct`。 |
| `factorization_order` | `VARCHAR` | 固定 `declared_driver_order`。 |
| `time_grid` | `VARCHAR` | 固定 `business_daily` observation grid；elapsed time 仍按 calendar/day-count。 |
| `regime_id` | `VARCHAR` | 当前 `constant-metals-22-liquid-chain`。 |
| `generator_config_id` | `VARCHAR` | Source generator config id。 |
| `created_run_id` | `VARCHAR` | 创建 row 的 run id。 |

`driver_order` 是矩阵和 random-stream identity 的一部分，不能排序后重解释。该表不能进入 F2A
Solver child；F2A vanilla catalogue 不使用 P-measure cross-underlying correlation。

### 6.3 `market.option_chain_specs`（Authoring-only）

Primary key: `(snapshot_id, chain_id)`。当前 1 row。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `chain_id` | `VARCHAR` | `METALS-LIQUID-STATIC-GRID-v1`。 |
| `listing_rule` | `VARCHAR` | 固定 `snapshot_start`。 |
| `listing_date` | `DATE` | Following-adjusted listing date，`2026-08-03`。 |
| `roll_rule` | `VARCHAR` | 固定 `static`；snapshot 内不 roll/re-list。 |
| `grid_type` | `VARCHAR` | `moneyness`；因此 `moneyness_grid` 非空、`strike_grid` 为空。 |
| `expiry_days` | `JSON` | Candidate days `[30,60,90,180,270,365]`。 |
| `moneyness_grid` | `JSON NULL` | Candidate listing moneyness decimal strings：`0.7...1.3` 共 11 个。 |
| `strike_grid` | `JSON NULL` | Absolute-grid alternative；此 parent 为 null。 |
| `call_put` | `JSON` | `['call','put']`。 |
| `strike_increment` | `DECIMAL(24,8)` | Absolute contract strike 的 increment，`0.50 USD`；不同于 quote tick `0.01`。 |
| `strike_rounding` | `VARCHAR` | `ROUND_HALF_EVEN`。 |
| `exercise_style` | `VARCHAR` | `european`。 |
| `settlement_type` | `VARCHAR` | `cash`。 |
| `contract_multiplier` | `DECIMAL(24,8)` | `100` underlying units/contract。 |
| `liquidity_filter` | `JSON NULL` | Inclusive listing-moneyness band `0.85–1.15` 且 `max_expiry_days=180`。 |
| `quote_model` | `JSON NULL` | Baseline half-spread 与 side-specific deterministic noise contract。 |
| `generator_config_id` | `VARCHAR` | Source generator config id。 |
| `created_run_id` | `VARCHAR` | 创建 row 的 run id。 |

实际 `quote_model`：

```json
{
  "minimum_half_spread": 0.005,
  "relative_half_spread": 0.0025,
  "null_policy": "no-null-for-required-snapshot-fields",
  "bid_ask_noise": {
    "type": "clipped_gaussian_half_spread_multiplier",
    "minimum_multiplier": 0.5,
    "maximum_multiplier": 1.5,
    "standard_deviation": 0.2,
    "stream_namespace": "option-bid-ask-noise-v1"
  }
}
```

`minimum_half_spread=0.005` 是量化前的 baseline；最终 bid/ask 仍按 `0.01 USD` tick canonicalize，
所以实际 half-spread 不一定等于这个数字。

### 6.4 `market.option_contracts`

Primary key: `(snapshot_id, option_id)`。当前 1,232 rows。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `option_id` | `VARCHAR` | Stable contract id，形式为 `<underlying_id>-<template_id>`。 |
| `underlying_id` | `VARCHAR` | Logical FK to `market.underlyings`。 |
| `template_id` | `VARCHAR` | Chain cell id，编码 call/put、candidate expiry days、listing moneyness。 |
| `call_put` | `VARCHAR` | `call` 或 `put`。 |
| `strike` | `DECIMAL(24,8)` | Listing 时从 `listing_spot × strike_moneyness` 得到并按 `0.50` rounding 的 immutable absolute strike。 |
| `expiry` | `DATE` | Listing date 加 candidate calendar days 后按 Following adjustment 得到的 expiry。 |
| `exercise_style` | `VARCHAR` | 全部 `european`。 |
| `settlement_type` | `VARCHAR` | 全部 `cash`。 |
| `contract_multiplier` | `DECIMAL(24,8)` | 全部 `100`。 |
| `chain_id` | `VARCHAR NULL` | Logical FK to `option_chain_specs`；当前全部非空。 |
| `listing_date` | `DATE NULL` | 当前全部 `2026-08-03`。 |
| `listing_spot` | `DECIMAL(24,8) NULL` | Listing-time underlying spot；当前全部非空。 |
| `strike_moneyness` | `DECIMAL(24,8) NULL` | Listing coordinate；保留值为 `0.85,0.90,0.95,1.00,1.05,1.10,1.15`。不是 daily moneyness。 |
| `created_run_id` | `VARCHAR` | 创建 contract 的 run id。 |

Strike、expiry 和 option identity 在 listing 后不随 daily spot 变化。F2A quote mutation 不允许修改
本表；child 可以把必要 contract terms denormalize 到 public option rows，而不复制整个 private master。

## 7. `market` observation tables：全部字段

### 7.1 `market.underlying_daily`

Primary key: `(snapshot_id, date, underlying_id)`。当前 1,430 rows。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `date` | `DATE` | Business observation date。 |
| `underlying_id` | `VARCHAR` | Logical FK to `underlyings`。 |
| `spot_open` | `DECIMAL(24,8)` | Start row 等于 initial spot；后续行等于前一 published close。 |
| `spot_high` | `DECIMAL(24,8)` | Separate synthetic range rule 的 high；满足 `>= open, close`。 |
| `spot_low` | `DECIMAL(24,8)` | Separate synthetic range rule 的 low；满足 `<= open, close` 且正。 |
| `spot_close` | `DECIMAL(24,8)` | P-measure rounded-state GBM endpoint；F2A spot mutation 的唯一 public spot target。 |
| `adjusted_close` | `DECIMAL(24,8)` | 当前全部等于 `spot_close`。 |
| `volume` | `BIGINT` | 独立 deterministic uniform activity rule，实际范围 `750,040–1,249,833`；不是 GBM output。 |
| `dividend` | `DECIMAL(24,8)` | Discrete cash dividend field；当前全部 `0`。不要与 continuous `dividend_yield` 混淆。 |
| `corporate_action` | `VARCHAR` | 当前全部 `none`。 |
| `generated_run_id` | `VARCHAR` | 生成 observation 的 run id。 |

OHLC 不是同一 intraperiod diffusion path/bridge 的联合观测，不能用于 barrier、realized range 或
intraday volatility truth。F2A child 只复制 `spot_close`，不复制 full OHLC/adjusted close/volume/
dividend/corporate action，避免把 spot point mutation 伪装成新的历史 bar。

### 7.2 `market.option_daily`

Primary key: `(snapshot_id, date, option_id)`。当前 60,368 rows。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `date` | `DATE` | Valuation/quote date；只存在于 `date < expiry`。 |
| `underlying_id` | `VARCHAR` | Denormalized underlying id；必须与 contract master 相同。 |
| `option_id` | `VARCHAR` | Logical FK to `option_contracts`。 |
| `call_put` | `VARCHAR` | Denormalized `call/put`。 |
| `strike` | `DECIMAL(24,8)` | Denormalized immutable strike。 |
| `expiry` | `DATE` | Denormalized expiry。 |
| `exercise_style` | `VARCHAR` | `european`。 |
| `settlement_type` | `VARCHAR` | `cash`。 |
| `contract_multiplier` | `DECIMAL(24,8)` | `100`；option cashflow 必须乘 multiplier。 |
| `bid` | `DECIMAL(24,8)` | Executable sell-side quote，非负且 `bid <= mid`。 |
| `ask` | `DECIMAL(24,8)` | Executable buy-side quote，`ask >= mid`。 |
| `mid` | `DECIMAL(24,8)` | Canonical BSM model mid；F2A logical option quote target。 |
| `settlement_price` | `DECIMAL(24,8)` | Parent 中等于 `mid` 的 legacy/current market field；不得进入 F2A child，避免留下未 mutation side channel。 |
| `volume` | `BIGINT` | 独立 deterministic activity，实际 `25–499`；不代表 fill capacity。 |
| `open_interest` | `BIGINT` | 独立 deterministic activity，实际 `500–4,999`；不用于 F2A position constraints。 |
| `generated_run_id` | `VARCHAR` | 生成 quote 的 run id。 |

当前 parent 有 4,302 个 zero bids，这是合法 quote 状态；不能因为 `bid=0` 补值或删除 row。全部
`bid/mid/ask/settlement_price` 落在 `0.01 USD` tick grid，且全部满足
`0 <= bid <= mid <= ask`。

Option 每张合约的 valuation-time execution cashflow 由 public variant 定义：

```text
long one contract  = multiplier * ask + option_fee
short one contract = multiplier * bid - option_fee
```

当前 baseline option fee 为 `0.50 USD/contract/side`。不要把 mid 当 executable side，也不要遗漏
multiplier 或按 net position 只收一次 fee；每条 leg 按 `abs(position)` 收费。

### 7.3 `market.option_pricing_audit`（empty legacy table）

Primary key: `(snapshot_id, date, option_id)`。当前 **0 rows**。Schema 2.4 为兼容 legacy snapshots
而保留它；generator 0.8/config 1.6 不生成任何 IV answers。

| Field | Type | 含义（仅 legacy schema） |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `date` | `DATE` | Valuation date。 |
| `option_id` | `VARCHAR` | Option identity。 |
| `risk_neutral_measure_id` | `VARCHAR` | Q measure id。 |
| `numeraire_id` | `VARCHAR` | Pricing numeraire id。 |
| `rate_path_id` | `VARCHAR` | Rate path id。 |
| `measure_change` | `VARCHAR` | Legacy constrained value `girsanov_drift_only`。 |
| `volatility_mapping` | `VARCHAR` | Legacy constrained value `same_deterministic_diffusion`。 |
| `q_effective_volatility` | `DOUBLE` | Legacy interval-effective Q volatility。 |
| `theoretical_price` | `DOUBLE` | Legacy unrounded QuantLib NPV。 |
| `canonical_mid` | `DECIMAL(24,8)` | Legacy visible canonical mid。 |
| `implied_volatility` | `DOUBLE NULL` | Legacy solved IV；not a current field/result。 |
| `iv_status` | `VARCHAR` | Legacy `CONVERGED/NO_FINITE_IV`。 |
| `iv_error` | `VARCHAR NULL` | Legacy failure detail。 |
| `iv_solver` | `JSON` | Legacy solver settings。 |
| `generated_run_id` | `VARCHAR` | Legacy run lineage。 |

任何 query、mutation、task 或 test 都不得把这张 empty table 当作 hidden answer source，也不得在 child
中创建对应字段。若未来 task 需要 IV，Trusted verifier 必须从 solver-visible canonical price 按
versioned inverse method 重算。

## 8. `market.pricing_metadata`：全部字段与 JSON

Primary key: `(snapshot_id, valuation_timestamp, underlying_id)`。当前 1,430 rows。

| Field | Type | 含义 |
|:---|:---|:---|
| `snapshot_id` | `VARCHAR` | Snapshot identity。 |
| `valuation_timestamp` | `TIMESTAMPTZ` | `date 16:00:00 UTC`；跨 session timezone join 时必须先转 UTC date。 |
| `valuation_date` | `DATE` | Explicit market date；base table 有，当前 parent view 未投影。 |
| `underlying_id` | `VARCHAR` | Logical FK to `underlyings`。 |
| `currency` | `VARCHAR` | 全部 `USD`。 |
| `discount_curve` | `JSON` | Public pricing curve；当前 flat continuous rate 3%。 |
| `risk_free_rate` | `DOUBLE` | Curve scalar shorthand；全部 `0.03`。 |
| `dividend_curve` | `JSON` | Public continuous dividend/carry curve；当前 flat 1%。 |
| `dividend_yield` | `DOUBLE` | Curve scalar shorthand；全部 `0.01`。 |
| `borrow_or_carry_rate` | `DOUBLE` | 当前 `risk_free_rate-dividend_yield`，约 `0.02`。 |
| `calendar` | `VARCHAR` | `WeekendsOnly`。 |
| `day_count` | `VARCHAR` | `Actual365Fixed`。 |
| `physical_dynamics` | `JSON` | P path/DGP provenance；Authoring-only，不进入 F2A child。 |
| `pricing_dynamics` | `JSON` | Q process、measure identities 与 deterministic volatility function；按 public child allowlist 投影。 |
| `pricing_model` | `VARCHAR` | `Black-Scholes-Merton`。 |
| `pricing_engine` | `VARCHAR` | `QuantLib.AnalyticEuropeanEngine`。 |
| `generator_version` | `VARCHAR` | `0.8.0`；Authoring provenance，不进入 F2A child。 |
| `seed` | `UBIGINT` | Authoring seed；private。 |
| `rng` | `VARCHAR` | Authoring RNG identity；private。 |
| `input_precision` | `JSON` | Generator dtype/quote precision/increments；Authoring-only。 |
| `canonicalization` | `JSON` | Decimal places/encoding/rounding/increments；Authoring-only。 |
| `generated_run_id` | `VARCHAR` | 生成 metadata row 的 run id。 |

### 8.1 Curve JSON

```json
{
  "discount_curve": {"type": "flat_continuous", "rate": 0.03},
  "dividend_curve": {"type": "flat_continuous", "yield": 0.01}
}
```

### 8.2 `physical_dynamics` JSON（Authoring-only）

| JSON path | 含义 |
|:---|:---|
| `measure` | `P`。 |
| `process` | `QuantLib.BlackScholesMertonProcess`。 |
| `state_role` | Start row 为 `initial_condition`（22 rows）；其余为 `interval_transition`（1,408 rows）。 |
| `drift` | Time-origin instantaneous drift value。 |
| `volatility` | Time-origin instantaneous volatility value。 |
| `drift_function.type` | `piecewise_linear`。 |
| `drift_function.nodes[].day_offset/value` | 相对 `time_origin` 的 calendar-day node 与 annualized drift。 |
| `drift_function.extrapolation` | `flat`。 |
| `volatility_function.type` | `piecewise_linear`。 |
| `volatility_function.nodes[].day_offset/value` | Calendar-day node 与 annualized instantaneous volatility。 |
| `volatility_function.extrapolation` | `flat`。 |
| `interval_reduction.drift` | Start 为 `point_value_at_initial_condition`；transition 为 `integral_arithmetic_mean`。 |
| `interval_reduction.volatility` | Start 为 `point_value_at_initial_condition`；transition 为 `integrated_variance_root_mean_square`。 |
| `dependence_spec_id` | FK-like reference to `underlying_dependence`。 |
| `driver_id` | 当前 underlying driver。 |
| `driver_order` | 冻结的 22-driver order。 |
| `shock_formulation` | `Lambda*factor+sqrt(D)*idiosyncratic`。 |
| `increment_partition` | Namespaced per-date random stream identity。 |
| `state_precision_contract` | Published `0.01` close is restart state。 |
| `ohlc_model` | `separate_synthetic_range-v1`。 |
| `time_axis` | `calendar_day_offset/Actual365Fixed`。 |
| `time_origin` | `2026-08-03`。 |
| `underlying_minimum_price_increment` | String `0.01`。 |

### 8.3 `pricing_dynamics` JSON

| JSON path | 含义 |
|:---|:---|
| `measure` | `Q`。 |
| `process` | `QuantLib.BlackScholesMertonProcess`。 |
| `risk_neutral_drift` | String contract `risk_free_rate-dividend_yield`。 |
| `q_pricing.risk_neutral_measure_id` | `USD-MONEY-MARKET-Q-v1`。 |
| `q_pricing.numeraire_id` | `USD-MONEY-MARKET-ACCOUNT-v1`。 |
| `q_pricing.rate_path_id` | `USD-FLAT-CONTINUOUS-RATE-v1`。 |
| `q_pricing.measure_change` | `girsanov_drift_only`。 |
| `q_pricing.volatility_mapping` | `same_deterministic_diffusion`。 |
| `volatility_measure_change` | Human-readable explanation of the same baseline mapping。 |
| `volatility_function.type` | `piecewise_linear`。 |
| `volatility_function.nodes[].day_offset/value` | Q diffusion function nodes；当前与对应 P diffusion coefficient 相同。 |
| `volatility_function.extrapolation` | `flat`。 |
| `option_minimum_price_increment` | String `0.01`。 |

`pricing_dynamics` 是模型/curve context，不是 hidden theoretical price 或 IV answer。F2A 判断必须使用
public variant 的 executable candidate formulas；不能只比较 quote 与 BSM repricing result。

Executable variant v4 将 Solver-visible future law 另行冻结为：`P ~ Q`（candidate horizon 上
null sets 等价），每段 exact deterministic time-inhomogeneous GBM transition 对 `(0,+infinity)` 有
positive conditional density；volatility nodes 的 origin 是 `2026-08-03T16:00:00Z`，offset 是 calendar
days，value 单位是 annualized `1/sqrt(year)`，piecewise-linear interpolation/flat extrapolation。每个
valuation/expiry date 均取 `16:00:00Z`；`T1` 同 timestamp 的 option settlement、第一段 liquidation、
state-contingent rebalance、第二段建仓与 cash deposit 顺序是 normative。`dividend_curve` 是
deterministic continuous nonnegative proportional cash-distribution yield；borrow/carry quote 不能代替它。
这些是 v4 public contract，不追溯改写 frozen parent 或 legacy/blocked variants。

### 8.4 Precision JSON

`input_precision`：

```json
{
  "dtype": "float64",
  "market_quote_decimal_places": 8,
  "minimum_price_increments": {"underlying": "0.01", "option": "0.01"}
}
```

`canonicalization`：

```json
{
  "decimal_places": 8,
  "encoding": "UTF-8",
  "rounding": "ROUND_HALF_EVEN",
  "minimum_price_increments": {"underlying": "0.01", "option": "0.01"}
}
```

这两个 JSON 记录 parent authoring checkpoints，不等于 F2A oracle 的完整 operation order。Oracle
dtype/casts/reduction order 必须由 versioned public variant/candidate catalogue 明确声明。

## 9. `solver_visible` parent views

Parent 当前有三个 views；它们是普通 smoke pipeline 的过渡视图，不是 production security boundary。
即使 query 只引用 `solver_visible`，Solver 若能打开同一个 DuckDB 文件，仍可读取 `market` 和
`metadata` schemas。

| View | Columns（按物理顺序） |
|:---|:---|
| `solver_visible.underlying_daily` | `snapshot_id, date, underlying_id, spot_open, spot_high, spot_low, spot_close, adjusted_close, volume, dividend, corporate_action` |
| `solver_visible.option_daily` | `snapshot_id, date, underlying_id, option_id, call_put, strike, expiry, exercise_style, settlement_type, contract_multiplier, bid, ask, mid, settlement_price, volume, open_interest` |
| `solver_visible.pricing_metadata` | `snapshot_id, valuation_timestamp, underlying_id, currency, discount_curve, risk_free_rate, dividend_curve, dividend_yield, borrow_or_carry_rate, calendar, day_count, physical_dynamics, pricing_dynamics, pricing_model, pricing_engine, generator_version, seed, rng, input_precision, canonicalization` |

每个 view field 的语义与对应 base-table dictionary 相同。DuckDB `information_schema` 可能把 view
columns 报告为 nullable；实际 base columns 的 NOT NULL/CHECK constraints 仍见第 5--8 节。

### 9.1 F2A public child 的目标 allowlist

Materializer 不能 `CREATE VIEW ... AS SELECT * FROM parent_view`。它必须新建 task-specific child，并且
只暴露：

```text
solver_visible.underlying_daily(
  snapshot_id, date, underlying_id, spot_close
)

solver_visible.option_daily(
  snapshot_id, date, underlying_id, option_id, call_put, strike, expiry,
  exercise_style, settlement_type, contract_multiplier, bid, ask, mid
)

solver_visible.pricing_metadata(
  snapshot_id, valuation_timestamp, underlying_id, currency,
  discount_curve, risk_free_rate, dividend_curve, dividend_yield,
  borrow_or_carry_rate, calendar, day_count, pricing_dynamics,
  pricing_model, pricing_engine
)
```

必须排除：

- `settlement_price`，否则 option mutation 后可能泄露 parent mid/before value；
- full OHLC、adjusted close、volume、open interest、discrete dividend/corporate action；
- `physical_dynamics`、P dependence、generator version、seed/RNG；
- input precision、authoring canonicalization、generation runs/revisions；
- parent DB、private lineage、requested signature、selector trace、family margins、oracle result。

## 10. Mutation authoring 指引

### 10.1 Stable slice selection

Stable selector：

```text
(parent_snapshot_id, parent_revision, valuation_date, underlying_id)
```

完整 slice 检查必须在每个 expiry 上验证 14 rows、7 distinct strikes、7 calls 和 7 puts，不能只检查
slice 总 row count。可复用 SQL：

```sql
WITH per_expiry AS (
    SELECT
        date,
        underlying_id,
        expiry,
        count(*) AS row_count,
        count(DISTINCT strike) AS strike_count,
        count(*) FILTER (WHERE call_put = 'call') AS call_count,
        count(*) FILTER (WHERE call_put = 'put') AS put_count
    FROM market.option_daily
    WHERE snapshot_id = 'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2'
    GROUP BY date, underlying_id, expiry
),
complete_slices AS (
    SELECT date, underlying_id
    FROM per_expiry
    GROUP BY date, underlying_id
    HAVING count(*) = 4
       AND min(row_count) = 14
       AND max(row_count) = 14
       AND min(strike_count) = 7
       AND max(strike_count) = 7
       AND min(call_count) = 7
       AND max(call_count) = 7
       AND min(put_count) = 7
       AND max(put_count) = 7
)
SELECT date, underlying_id
FROM complete_slices
ORDER BY date, underlying_id;
```

当前结果必须为 484 rows。Stable row order 为：

```text
valuation date
  -> underlying_id
  -> expiry
  -> strike
  -> call_put
  -> option_id
```

### 10.2 Option quote point mutation

Complete-grammar operator: `mutate_option_price_point_v2`。Legacy/v2 review 中的 v1 operator ID 只按
各自 immutable contract 重放。

在一个 `(valuation_date, option_id)` 上记录 parent half-spreads：

```text
bid_offset = parent.mid - parent.bid
ask_offset = parent.ask - parent.mid

child.mid = parent.mid + delta_ticks * 0.01 USD
child.bid = child.mid - bid_offset
child.ask = child.mid + ask_offset
```

要求：

- `delta_ticks` 是 versioned integer grid 上的 signed count；不能存第二份 decimal increment source；
- 只改变一个 logical quote point；三个 physical fields 是 deterministic derivatives；
- `strike/expiry/call_put/spot/curves/other quotes` 全部不变；
- 结果 finite、`0 <= bid <= mid <= ask` 且三者都在 cent grid；
- 不复制 `settlement_price`；不新增 `task_price`；
- 不用 BSM bounds/parity/monotonicity/convexity clip 或 reprice child。

### 10.3 Equal call+put grouped mutation（v4 已实现）

`mutate_call_put_pair_equal_shift_v1` 原子选择同 valuation、underlying、expiry、strike 与 multiplier 的
call/put pair，对两行 quote 施加相同 `delta_ticks`。V4 materializer 已按该 copy-on-write 合同执行，
但仍不会修改本 frozen parent 的任何 row。

```text
logical_mutation_groups       = 1
logical_quote_points_changed  = 2
physical_quote_points_changed = 2
group_semantics = same-strike-same-expiry-call-put-equal-shift
```

两条 leg 都必须保存 option ID、tick unit 与 `bid/mid/ask` before/after，并在同一 transaction 内通过
same-pair、tick、finite/nonnegative 与 side-order gates；任一失败整组 rollback。Equal shift 严格保持
same-pair executable parity surpluses，但不保持 call/put price bounds，所以必须重扫整个 U family。

### 10.4 Underlying spot point mutation

Complete-grammar operator: `mutate_underlying_spot_point_v2`。Legacy/v2 review 中的 v1 operator ID 只按
各自 immutable contract 重放。

- Target 是一个 `(valuation_date, underlying_id)` 的 child `spot_close`；幅度仍为 integer ticks ×
  parent underlying increment `0.01 USD`。
- 所有 option quotes、contract terms、curves、pricing/execution contracts 保持不变。
- Child 不复制 open/high/low/adjusted close，所以 mutation 是 valuation-time task state，不是新的
  P-measure history 或 OHLC bar。
- Spot mutation 的无条件不变量是 option-only `X_after == X_before`。Authoring 先独立扫描并要求
  clean signature `000`，否则 deterministic skip；只有结合该 policy 才能推出 accepted spot child
  的 `X_after=false`。Before/after 不相等时 materialization 才必须失败。

### 10.5 Signature selector 与 guards

目标 canonical order：

```text
cross-sectional, cross-asset, calendar
```

目标 dataset signatures：

```text
000 -> []
100 -> ["cross-sectional"]
010 -> ["cross-asset"]
001 -> ["calendar"]
110 -> ["cross-sectional", "cross-asset"]
101 -> ["cross-sectional", "calendar"]
011 -> ["cross-asset", "calendar"]
111 -> ["cross-sectional", "cross-asset", "calendar"]
```

Authoring search order 必须固定：

```text
requested signature
  -> execution contract/profile
  -> operator
  -> valuation date/underlying/expiry/strike/call_put/option_id
  -> sign order
  -> absolute integer-tick grid
```

每个 candidate child 在内存 public projection 上全量重扫 enabled families。只接受 realized bitmask
精确等于 requested signature，并按 candidate payoff class 同时记录/检查：

```text
setup-boundary distance（identically-zero payoff 用 open s>0；
                         support-certified nonconstant payoff 用 closed s>=0）
terminal finite-vertex slack
terminal actual-boundary slack
terminal one-sided-limit slack
terminal recession-ray slope slack
strict-gain certificate
```

Active family 至少有一个 canonical candidate 的完整向量通过；inactive family 的所有 candidates 都
必须不满足 canonical predicate，并以最近 boundary/certificate evidence 通过 inactive guards。Calendar
各 cell/boundary/limit/ray 检查的目标是排除 initial surplus 的 `g_j>=0`，不能只检查
`W_T2=s_j/D+g_j>=0`。Guard 只用于样本 selection，不改变 exact verifier predicate，也不能把不同量纲
压成一个 `active_guard/inactive_guard` USD 标量。
不存在可行窗口时 deterministic skip；不能随机 retry、扩大 grid、修改 parent 或为了 label 临时换 fee。

Legacy variant v1/catalogue v2/dataset v1 保持 replay；v2/v3 保留为 blocked review records。当前
child materialization 必须路由到 executable variant v4/catalogue v5、mutation/dataset/lineage v4；
`runtime_enabled=true`，calendar family 为
`transaction-cost-aware-two-expiry-call-stock-flip-v1`。Tracked complete-chain fixture 的真实 full-oracle
audit 已实现 `000..111` 全部 signatures，其中 `001` 的 grouped-pair 正向 tick window 为
`[132,290]`。这不授权在缺少 production parent 时 fallback 到 fixture，也不扩大 v4 的有限 catalogue
作用域。

### 10.6 Child gates 与 artifacts

Authoring gates 只检查 numeric/domain、完整性、identity/provenance、visibility、tick 和 requested
signature separation；不能把故意破坏的 clean-market arbitrage relations强制恢复。

```text
snapshots/generated/f2a/children/<child_snapshot_id>/child.duckdb
snapshots/generated/f2a/children/<child_snapshot_id>/child.manifest.json
snapshots/private/f2a/<task_id>/lineage.json
datasets/manifests/tasks/f2a/<task_id>.json
```

Private lineage 至少记录：parent/child IDs/revisions、stable selector、operator/tick、physical
before/after、requested/realized signature、execution/candidate contract IDs、每个 family 的 nearest
candidate/cost-adjusted margin、guards 和 exact violated invariant。若 schema 不接受这些字段，先
version schema；不能向 `additionalProperties: false` object 私自追加。

Trusted verifier 不读取 parent/private lineage/requested signature/authoring result；它只从 final public
child 与 public variant 独立重算。删除 private lineage 不得改变 ORM truth。

## 11. Maintainer 查询模板

现有参数化 queries 位于
[`snapshots/generated/sql_query/`](../../../sql_query/README.md)。其中一些 authoring inspection queries
读取 `market/metadata`；不能直接放进 Solver bundle。

### 11.1 预览一个 LLM-safe 56-row slice

下面的 SQL 在 parent 上只选择目标 child allowlist 中的字段，适合 maintainer 预览。生产 LLM 仍应
查询独立 child，而不是 parent 文件。

```sql
WITH parameters(snapshot_id, market_date, underlying_id) AS (
    VALUES (
        'DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2',
        DATE '2026-08-03',
        'SYNTH-METAL-GOLD'
    )
)
SELECT
    quote.snapshot_id,
    quote.date,
    quote.underlying_id,
    underlying.spot_close,
    quote.option_id,
    quote.call_put,
    quote.strike,
    quote.expiry,
    quote.exercise_style,
    quote.settlement_type,
    quote.contract_multiplier,
    quote.bid,
    quote.mid,
    quote.ask,
    CAST(pricing.valuation_timestamp AT TIME ZONE 'UTC' AS VARCHAR)
        AS valuation_timestamp_utc,
    pricing.currency,
    pricing.discount_curve,
    pricing.risk_free_rate,
    pricing.dividend_curve,
    pricing.dividend_yield,
    pricing.borrow_or_carry_rate,
    pricing.calendar,
    pricing.day_count,
    pricing.pricing_dynamics,
    pricing.pricing_model,
    pricing.pricing_engine
FROM solver_visible.option_daily AS quote
JOIN solver_visible.underlying_daily AS underlying
  ON underlying.snapshot_id = quote.snapshot_id
 AND underlying.date = quote.date
 AND underlying.underlying_id = quote.underlying_id
JOIN solver_visible.pricing_metadata AS pricing
  ON pricing.snapshot_id = quote.snapshot_id
 AND CAST(pricing.valuation_timestamp AT TIME ZONE 'UTC' AS DATE) = quote.date
 AND pricing.underlying_id = quote.underlying_id
JOIN parameters
  ON parameters.snapshot_id = quote.snapshot_id
 AND parameters.market_date = quote.date
 AND parameters.underlying_id = quote.underlying_id
ORDER BY quote.expiry, quote.strike, quote.call_put, quote.option_id;
```

### 11.2 检查 daily/master consistency

```sql
SELECT count(*) AS mismatch_count
FROM market.option_daily AS daily
JOIN market.option_contracts AS contract
  ON contract.snapshot_id = daily.snapshot_id
 AND contract.option_id = daily.option_id
WHERE daily.underlying_id <> contract.underlying_id
   OR daily.call_put <> contract.call_put
   OR daily.strike <> contract.strike
   OR daily.expiry <> contract.expiry
   OR daily.exercise_style <> contract.exercise_style
   OR daily.settlement_type <> contract.settlement_type
   OR daily.contract_multiplier <> contract.contract_multiplier;
```

当前结果为 0。Child materializer 仍应在自己的 allowlisted projection 上做等价检查。

### 11.3 检查 tick 与 quote order

```sql
SELECT
    count(*) FILTER (
        WHERE bid * 100 <> round(bid * 100)
           OR mid * 100 <> round(mid * 100)
           OR ask * 100 <> round(ask * 100)
    ) AS off_tick_rows,
    count(*) FILTER (
        WHERE bid < 0 OR bid > mid OR mid > ask
    ) AS invalid_order_rows
FROM market.option_daily;
```

当前两个结果都为 0。

## 12. 给 LLM 的指引来源

### 12.1 使用边界

F2A v4 reference runtime 已完成 child→submission→verifier，并可用 tracked fixture 重放；production
Solver image、trusted DuckDB adapter 与 sandbox enforcement 尚未完成。正式 F2A Solver 只能获得：

- 一个 frozen public child（不是 parent）；
- public task manifest；
- exact public variant/candidate/execution contract；
- trajectory 和 submission schemas；
- trusted read-only DuckDB query adapter。

不能提供 parent、`market/metadata` schemas、private lineage、mutation/authoring configs、requested
signature、selector traces、stored labels、hidden tests 或 verifier implementation。仅靠 prompt 说“不要
访问”不构成隔离；文件 mount、DuckDB adapter 和 import/API sandbox 必须实际 enforcement。

### 12.2 可抽取的 canonical LLM task instructions

下面内容是当前 v4 task prompt 的可抽取数据库使用部分。构建 task 时必须用实际 child/variant IDs
替换占位符，并绑定 executable v4 contract；不能直接把整个本文档放入 Solver bundle。

```text
You are analyzing one frozen F2A public child snapshot in read-only mode.

Identity and scope
- Use exactly child snapshot <snapshot_id>, revision <revision>, valuation date
  <valuation_date>, and underlying <underlying_id>.
- Query only the supplied solver_visible relations through the trusted adapter.
- Do not write, attach another database, install packages, access the network,
  subprocesses, parent data, private lineage, or hidden verifier material.

Market data
- spot_close is the single valuation-time ex-dividend spot state in USD.
- Option rows are European, cash-settled contracts. Prices are USD per underlying
  unit; multiply every option leg by contract_multiplier.
- bid is the executable sell price, ask is the executable buy price, and mid is
  non-executable unless the public strategy contract explicitly says otherwise.
- Preserve the declared row order: expiry, strike, call_put, option_id.
- Use the public UTC valuation timestamp, curves, calendar, Actual365Fixed clock,
  Q/numeraire/rate-path identities, dtype, cast points, and reduction order.

Execution costs
- A long option leg costs abs(position) * (multiplier * ask + per-contract,
  per-side option fee).
- A short option leg receives abs(position) * (multiplier * bid - that fee).
- Charge the fee to every option leg using abs(position).
- Every underlying trade uses the public proportional cost on absolute traded
  notional; under the baseline, buy at S*(1+0.0005) and sell at S*(1-0.0005).
- The cash account is frictionless only if the public variant says so. Transaction
  costs are economic cashflows, not numerical tolerances.

Arbitrage task
- Enumerate every candidate in every family enabled by public candidate catalogue
  <candidate_catalogue_id>. Do not infer the answer from a suspected mutation.
- A quote/model mismatch is model inconsistency, not automatically executable
  arbitrage. Do not use raw same-strike maturity ordering as a calendar proof.
- The result is catalogue-scoped. false/[] does not prove global market
  no-arbitrage.
- Derive the family-hit vector from exact executable candidate certificates after
  bid/ask, multiplier, fees, underlying costs, funding, carry, settlement, and
  position rules. One hidden authoring mutation group may activate multiple
  families; never infer which group or how many physical points changed.
- Return arbitrage_type in the canonical order:
  cross-sectional, cross-asset, calendar.
- Enforce arbitrage_opportunity == bool(arbitrage_type).
- Return no maximal_spread field for F2A.

Output
- Produce the complete trajectory required by <trajectory_schema_id>.
- In Outcome.orm_answer, return exactly:
  {"arbitrage_opportunity": <bool>, "arbitrage_type": [<canonical ordered types>]}
- Do not include mutation target, private evidence, maximal spread, free-form type
  names, duplicates, or a differently ordered type array.
```

### 12.3 推荐的 LLM query workflow

1. 读取并核对 public task/variant IDs；不要从文件名猜 identity。
2. 查询指定 slice，并确认正好 56 rows、4 expiries、每 expiry 7 strikes × call/put。
3. 查询唯一 `spot_close` 和唯一 pricing context；timestamp 统一转 UTC。
4. 保留 DuckDB DECIMAL quote inputs；只在 public contract 指定的 checkpoint cast 到 `float64`。
5. 按 stable row/candidate order 枚举全部 enabled families，不在发现第一个 hit 后提前停止，因为最终
   `arbitrage_type` 是全部 hit families 的 ordered union。
6. 对每个 leg 使用 directional side、multiplier 和 fee；对每次 underlying trade 应用 proportional
   cost；按声明 curve 统一 cashflow dates。
7. 只根据 public-child certificates 生成 bool/type array，不读取或猜测 mutation intention。
8. 按 submission schema exact serialization；不添加 `maximal_spread`。

## 13. 实现与测试 checklist

### Authoring/materializer

- [x] 精确打开本 parent path，验证 DB metadata 与 manifest 为 v2/r1/FROZEN；无 fallback。
- [x] Parent connection 始终 read-only，parent file 在运行前后不被写入。
- [x] 只选择 484 个 complete slices 中的稳定目标。
- [x] Child 使用字段 allowlist，不使用 `SELECT *`。
- [x] Option mutation 只产生一个 logical quote target；spot mutation 只产生一个 spot target。
- [x] 所有 public prices 保持 cent ticks 与 quote domain；不 reprice/clip arbitrage relations。
- [x] Signature search 稳定、cost-aware、exact bitmask match；不可达时 skip。
- [x] Child 新 identity/revision、DRAFT→gates→FROZEN；private lineage 与 public artifact 分离。
- [x] Public manifest/child IDs 不泄露 operator、target、signature 或 label。

### Oracle/verifier

- [x] Verifier 不读 parent/private lineage/authoring selector，也不导入 Solver implementation。
- [x] Cross-sectional/cross-asset formulas 逐腿计入 executable sides 和 option fees。
- [x] Underlying trades 逐次计入 5 bps cost；不使用 frictionless BSM replication shortcut。
- [x] Calendar 已 version two-expiry segment/cell/tail certificate，并由 executable v4 启用。
- [x] Clean control 与七种 target signatures 都从 final public child 独立复算。
- [x] Exact bool/type equality、canonical order、no duplicates、no maximal spread。

### LLM environment

- [ ] 只挂载 public child/task/variant/schemas，不挂载 parent 或 repo。
- [ ] DuckDB connection 由 trusted read-only adapter 持有，并限制 query surface。
- [ ] Network、dynamic install、subprocess 和 hidden verifier access 实际禁用。
- [ ] Import/runtime capability audit 拒绝预制 option pricing/IV/Greek/surface/arbitrage APIs。
- [ ] Prompt 使用第 12.2 节的抽取版本，并绑定实际 versioned contract IDs。

前三节中只有 production LLM environment 仍未完成；仓库内 reference Solver 直接读取受控的本地
fixture/child，只用于数学与接口重放，不能作为上述四项 sandbox gate 的替代证据。

## 14. 相关文档与查询

- [Generated snapshots 总览](../../../README.md)
- [F2A parent SQL catalog](../../../sql_query/README.md)
- [Authoring package](../../../../../src/synthetic_derivatives/authoring/README.md)
- [DuckDB agent task plan](../../../../../src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md)
- [F2A detailed task plan](../../../../../src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md)
- [Solver environment allowlist](../../../../../environments/solver/README.md)
- [F2A v5.1 inversion/linked-validation 完成记录](../../../../../f2a_v5_bsm_inversion_linked_diffusion_validation_rework_plan.md)
