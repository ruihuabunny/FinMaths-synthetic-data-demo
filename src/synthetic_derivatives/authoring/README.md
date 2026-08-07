# Authoring package

本目录实现 trusted authoring boundary：读取版本化 generator config，通过 pinned
QuantLib 生成确定性市场数据，并用事务方式写入可增量编辑、最终可冻结的 DuckDB
snapshot。Solver 不应直接导入本包，也不能访问其中的 private generation provenance。

完整项目设计见 [项目 README](../../../README.md) 和
[Authoring Pipeline](../../../docs/authoring_pipeline.md)。F2A 的完整交易、candidate 和 ORM 合同见
[`f2a_arbitrage_finding_agent_task_plan.md`](../mutation/f2a_arbitrage_finding_agent_task_plan.md)。

## 当前状态

| 范围 | 状态 | Authoring 约束 |
|:---|:---|:---|
| Ordinary snapshot pipeline | 已实现 | 支持 DRAFT create/sync/append/NOOP、quality gates、revision、manifest 与 freeze。 |
| Active development v3 | `DRAFT / r1`，当前 pipeline 下只读 | 使用 legacy config v1；authoring-time IV solving 已退休，不能在同一 identity 下继续写入或重建。 |
| Successor ordinary authoring | config schema `1.6.0`、generator `0.8.0`、snapshot v4 | 新 materialization 必须写新文件，不得 append 到 legacy v3。 |
| F2A clean parent | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1 / FROZEN` | 已由现有 pipeline 物化并冻结；只允许 read-only selection，不能 append、sync 或原地 mutation。 |
| F2A child authoring | 未实现 | `f2a_child_materializer.py`、point-mutation runtime、signature selector、private lineage 和 child artifacts 尚不存在。 |
| F2A contracts | 部分骨架已落地 | 当前 variant 仍是 catalogue v2 且 `calendar_family = null`；private dataset config 仍是 positive/negative balance。Catalogue v3/calendar certificate 与八种 signature policy 尚未 versioned。 |

默认 active development database 是
`snapshots/generated/quantlib_bsm_metals_option_chain_smoke_v1_20260807.duckdb`。若它缺失，应报告，
不能 silent fallback 到 public snapshot。它不是 F2A parent，也不能用于要求 frozen parent 的 child
materialization。

## 模块职责

| 模块 | 职责 |
|:---|:---|
| [`config.py`](config.py) | 解析 generator config；校验 deterministic physical functions、underlying dependence、option-chain grid 和稳定 ID；规范派生 $D$ 与 $R$。 |
| [`generator_common.py`](generator_common.py) | 两个 generator 共享的 pinned QuantLib 检查、calendar/day-count、日期转换、配置化 minimum-price-increment 量化，以及 namespaced deterministic RNG。 |
| [`underlying_daily_generator.py`](underlying_daily_generator.py) | 生成 underlying master、private P-measure dependence、`underlying_daily` 和 `pricing_metadata`；这是唯一消费 $\Lambda/D/R$ 的 generator。 |
| [`option_daily_generator.py`](option_daily_generator.py) | 生成 private option-chain spec、冻结的 option contracts 和 Q-measure `option_daily`；到 canonical quote 为止，不求解 IV，也不提供 underlying path/dependence API。 |
| [`pipeline.py`](pipeline.py) | 编排两个 generator、DuckDB transaction、incremental MERGE、snapshot compatibility、quality gates、revision 和 manifest。 |
| [`schema.py`](schema.py) | DuckDB DDL、additive migration、solver-visible views、table column order 和 business-key MERGE。 |
| [`cli.py`](cli.py) | `create-smoke`、`append-dates`、`sync-config`、`sync-range`、`summary` 和 `freeze` 命令入口。 |
| [`__init__.py`](__init__.py) | 对外只导出 `AuthoringPipeline`。 |

原来的单体 `generator.py` 已拆除。Product-specific generator 只共享基础设施，不互相
调用，避免 option quote 生成路径意外读取 P-measure correlation contract。

F2A runtime 落地后会新增独立的 `f2a_child_materializer.py`。它不属于现有
`AuthoringPipeline` 的普通 quote-generation 路径：它只读 frozen parent，按 allowlist 构建新 child、
执行 authoring-side signature selection/quality gates、写 private lineage 并 freeze 新 identity；
不会重新运行 QuantLib repricing。该文件当前尚未创建，不得从本表推断 F2A child 已可物化。

## 生成顺序

一次 `sync_range` 在同一个 DuckDB transaction 内执行：

```text
validate DRAFT snapshot and immutable config
  -> UnderlyingDailyGenerator
       -> underlying master + private dependence spec
       -> realized P-measure underlying paths
       -> per-underlying/date pricing metadata
  -> OptionDailyGenerator
       -> private option-chain spec + frozen contracts
       -> Q option quotes from realized spot + frozen contract + pricing inputs
  -> MERGE by stable business keys
  -> cross-table quality gates
  -> commit
  -> publish manifest atomically
```

任一步失败都会 rollback 市场数据批次，并在 `metadata.generation_runs` 留下 `FAILED`
记录。Revision 在 market transaction 内更新；相邻 manifest 只在 commit 成功后原子替换。
重复运行相同范围应返回 `NOOP`，不增加 revision。

F2A child 使用另一条计划中的 copy-on-write 流程：

```text
open exact F2A v2 parent read-only
  -> select one complete 4-expiry x 7-strike x call/put slice
  -> build an in-memory solver-visible projection by field allowlist
  -> enumerate clean control or one logical point mutation on integer ticks
  -> recompute the full authoring-side family bitmask and active/inactive guards
  -> accept only exact requested-signature match; otherwise deterministic skip
  -> write a new DRAFT child + private lineage
  -> run child domain/identity/visibility gates
  -> freeze child under a new snapshot identity/revision
```

Mutation 层只产生 immutable spec/identity/lineage inputs，不打开 DuckDB。Trusted verifier 也不参与
上面的 selection：它随后只从最终 public child 和 public variant 使用独立实现重算 ORM truth。
Authoring-side selector 与 verifier-owned oracle 不共享实现。

## Snapshot identity 与生命周期

| 用途 | Config | Snapshot | 生命周期 |
|:---|:---|:---|:---|
| Legacy active development | `quantlib_bsm_metals_option_chain_smoke_v1.json` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3 / r1` | `DRAFT`，当前只读；不能重建旧 identity。 |
| New ordinary authoring | `quantlib_bsm_metals_option_chain_smoke_v2.json` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v4` | 在新文件中从 DRAFT 开始，可按普通 pipeline sync/freeze。 |
| F2A clean parent | `quantlib_bsm_metals_f2a_parent_v2.json` | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1` | `FROZEN`，immutable mutation source。 |
| F2A child | 由 parent、public variant、mutation spec 与 private authoring contract 联合确定 | 每个 child 一个不泄露 label 的新 identity | 计划中：DRAFT materialization 通过 gates 后 freeze；尚无已物化 child。 |

改变经济参数、minimum price increment、随机 law、quote law、generator version 或 numerical
convention 都必须使用新 generator/snapshot identity。F2A child 的 point mutation 也绝不能写回 parent；
parent/child/task IDs 不编码 operator、target、requested/realized signature 或 positive/negative status。

## 数学与权限边界

### Underlying simulation

`UnderlyingDailyGenerator` 在物理测度 $\mathbb P$ 下生成 path。Config `1.2.0+` 以
factor-loading matrix $\Lambda$ 为 source of truth，并确定性派生

$$
D=\operatorname{diag}(1-\lVert\lambda_i\rVert^2),
\qquad
R=\Lambda\Lambda^\top+D,
$$

随后使用

$$
Z_t=\Lambda\eta_t+D^{1/2}\varepsilon_t
$$

耦合 underlying close shocks。`driver_order` 必须恰好排列 underlying IDs，不能包含
option IDs、Greeks 或其他 derivative contracts。

`start_date` materialize 的是 $S(t_0)=S_0$ initial condition，不从虚构前一日抽 shock。
之后每个 actual-calendar interval 对 piecewise-linear $\mu_P(t)$ 精确积分取平均，并对
$\sigma_P^2(t)$ 精确积分取 RMS；这两个 flat-equivalent coefficients 先给出连续 GBM endpoint，
再按 `underlying_minimum_price_increment` 做 `ROUND_HALF_EVEN`。量化后的 close 是下一步 restart
state，因此 authored path 是明确的 rounded-state Markov chain。

### Option generation

`OptionDailyGenerator` 只接收 pipeline 已经 materialize 的 `spot_close`。它不会读取
`underlying_simulation`，也不会给 option contracts 另外生成 correlated shocks。
Underlying correlation 只能通过 realized spot 间接影响 quote。

Config `1.3.0` 的 `option_chain` 必须在 `moneyness_grid` 和 `strike_grid` 中恰选一个。
当前只支持：

- `listing_rule = snapshot_start`
- `roll_rule = static`
- paired call/put
- positive, strictly increasing expiry/grid
- `ROUND_HALF_EVEN` strike-increment rounding

Moneyness 只在 listing date 用 listing spot 转换一次。生成后的 absolute strike、expiry 和
contract ID 写入 contract master，后续 valuation date、append 或 `sync-config` 都不得重算。

Config `1.4.0` 在 moneyness candidate grid 上增加 immutable `liquidity_filter`：只挂牌
不超过 `max_expiry_days` 且 listing moneyness 位于 inclusive band 内的合约。筛选只发生
一次，不会随 daily spot 漂移。它还要求 `quote_model.bid_ask_noise`；BSM NPV 仍是
`mid = settlement_price`，独立的 deterministic Gaussian multiplier 只扰动 bid/ask
half-spread，并在配置边界内截断。

Config `1.5.0` 删除 `base_implied_volatility` 和 legacy smile，改为 snapshot-wide
`q_pricing` contract。当前 baseline 明确选择 drift-only Girsanov mapping：Q drift 为
$r-q$，deterministic diffusion 保持 $\sigma_Q(t)=\sigma_P(t)$。每个 valuation/expiry
区间使用 integrated-variance RMS 作为 QuantLib analytic BSM 的 exact constant equivalent。
Config `1.6.0` 保留该定价模型，但从 authoring contract 删除 IV solver。
Option generator 只使用已经按 underlying increment 量化的 spot。NPV、mid、bid、ask 和 settlement
price 再按 `option_minimum_price_increment` 量化。Authoring 到此为止：不调用 IV solver，
不把 derived IV、solver status 或 canonical answer 写入 DuckDB。IV method contract 与
canonical answer 分别属于 task/variant 和 trusted verifier/private oracle 边界。

### No-arbitrage

Correlation matrix 不是 no-arbitrage 条件。Single-asset option consistency 来自所选合法
pricing model、风险中性测度和 discounting convention；pipeline quality gates 只负责发现
实现错误和 cross-table 不一致。Config `1.5.0+` 已冻结 single-asset vanilla margins 的共同
$\mathbb Q$/numeraire/rate-path identity；multi-asset Q-dependence 与 joint payoff pricing
仍是独立后续阶段。

F2A child 是刻意改变公开 executable quote/spot 的 task state，不能套用 clean-parent 的
BSM bounds、put-call parity、strike monotonicity/convexity 或 calendar relation 作为 materialization
gate，否则会把待检测信号修复掉。F2A child domain gate 只检查声明的字段 allowlist、有限性、
非负性、`bid <= mid <= ask`、tick alignment、完整 chain、identity/provenance 与 visibility boundary。

Authoring guard 也不是套利定义或数值 tolerance。它只从远离边界的 candidate 中选择样本；最终
`arbitrage_opportunity/arbitrage_type` 必须由 Trusted verifier 从 public child 全量重扫 finite
catalogue。一次 point mutation 可能激活多个 family，不能从 mutation intention 复制 type。F2A
`false/[]` 只表示 frozen catalogue 内没有 positive exact certificate，不证明全市场 no-arbitrage；
quote 偏离 BSM 只直接说明 model inconsistency，也不能自动写成 executable arbitrage。

Public execution cost 必须进入 authoring-side candidate cashflow：option 按 directional bid/ask，
每条腿收取 `0.50 USD/contract/side`；underlying 每次成交按绝对 traded notional 收取单边 `5 bps`；
cash account 无交易费。费用是经济输入，不是 guard/tolerance，也不能为了请求的 signature 临时修改。

## Config 与 schema 版本

| Generator config | 主要能力 |
|:---|:---|
| `1.0.0` | Scalar physical parameters 与 legacy option templates。 |
| `1.1.0` | 增加 deterministic piecewise-linear drift/volatility functions。 |
| `1.2.0` | 增加 immutable P-measure `underlying_simulation`。 |
| `1.3.0` | 增加 static `option_chain`，替代逐条 `option_templates`。 |
| `1.4.0` | 增加 candidate-grid liquidity filter 与 deterministic bid/ask spread noise。 |
| `1.5.0` | 增加 per-underlying sampled-and-frozen physical functions（含 seeds/hard bounds）、显式 Q mapping；legacy snapshot 还包含 authoring IV audit。 |
| `1.6.0` | 保留 Q quote-generation model，删除 authoring IV solver；IV method/answer 移到 task/verifier 边界。 |

F2A 不增加新的顶层 config 分类。各 source of truth 保持分离：

| Config | Authoring 使用方式 |
|:---|:---|
| [`quantlib_bsm_metals_f2a_parent_v2.json`](../../../configs/generators/quantlib_bsm_metals_f2a_parent_v2.json) | Frozen parent DGP、`0.01 USD` underlying/option increments、rounding 与 generator/snapshot identity。 |
| [`bsm_arbitrage_finding_f2a_v1.json`](../../../configs/variants/bsm_arbitrage_finding_f2a_v1.json) | Public Q/numeraire/rate path、execution cost、candidate/output contract；不能包含 private target 或 stored truth。 |
| [`f2a_point_v1.json`](../../../configs/mutations/f2a_point_v1.json) | Logical operator IDs、integer tick grid 与稳定枚举顺序；不重复声明 decimal increment。 |
| [`f2a_dataset_v1.json`](../../../authoring/configs/f2a_dataset_v1.json) | Private parent selectors、signature distribution、guards、feasibility/split policy；不进入 Solver bundle。 |

当前后两份 public/private skeleton 还没有对齐目标 catalogue v3：variant 仍禁用 calendar，dataset
config 仍是 positive/negative balance。更新时必须 version contract/schema/identity，不能在 runtime
中用隐式默认值补齐，也不能在现有 catalogue identity 下静默启用 calendar。

当前 authoring schema 为 `2.4.0`，支持从 `2.0.0/2.1.0/2.2.0/2.3.0` additive migration。
所有 generator configs 还必须显式声明可由 DuckDB `DECIMAL(24,8)` 表示的正数
`underlying_minimum_price_increment` 和 `option_minimum_price_increment`。改变任一 increment 会改变
underlying path law 或 option quote law，必须使用新的 generator/snapshot identity。
`market.option_chain_specs` 同时保存 liquidity filter 和完整 quote model。Private
authoring tables 包括：

- `market.underlying_dependence`
- `market.option_chain_specs`
- `market.option_pricing_audit`（schema 2.4 legacy；新生成不再写入）
- 带 run lineage 的 market/master tables
- `metadata.snapshots`、`generation_runs`、`snapshot_revisions`

Solver 只应读取：

- `solver_visible.underlying_daily`
- `solver_visible.option_daily`
- `solver_visible.pricing_metadata`

注意：当前 `solver_visible.pricing_metadata` 仍是 smoke 阶段的过渡合同，public/private
metadata split 尚未完成。

F2A 不能直接复制 parent 中的同名 views，因为 parent pricing metadata 仍含 P dynamics、seed/RNG
和 authoring canonicalization。计划中的 child 只允许以下最小投影：

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

`settlement_price`、完整 OHLC、volume/open interest、physical dynamics、generator/seed/RNG、input
precision、authoring canonicalization、metadata/audit tables、before/after、reference answer 和 private
lineage 都不得进入 child。Parent view 将来新增字段也不能通过 `SELECT *` 自动进入 child。

## F2A child authoring 合同（计划中）

### Parent selection 与 logical mutations

稳定 slice key 为
`(parent_snapshot_id, parent_revision, valuation_date, underlying_id)`。第一版只接受完整的
`4 expiries x 7 strikes x call/put = 56` 行 live chain；缺任一 expiry、strike 或 call/put mate 就
deterministic skip，不补 quote、不 fallback、不重新定价 parent。

允许两个 logical point operators：

- `mutate_option_price_point_v1`：只改变一个 `(valuation_date, option_id)` 的 logical quote point。
  Materializer 保留 parent half-spread，用 integer `delta_ticks` 唯一派生新的 `mid/bid/ask`；strike、
  expiry、call/put、spot、curves、pricing volatility contract 和其他 quotes 不变。
- `mutate_underlying_spot_point_v1`：只改变一个 `(valuation_date, underlying_id)` 的 `spot_close`；
  option quotes、curves 与 execution/pricing contracts 不变。Child 不复制其余 OHLC，所以这不是新
  P-measure path，也不声称构造了新的 daily bar。

Option mutation 使用：

```text
bid_offset = parent.mid - parent.bid
ask_offset = parent.ask - parent.mid

child.mid = parent.mid + delta_ticks * parent_option_increment
child.bid = child.mid - bid_offset
child.ask = child.mid + ask_offset
```

F2A 不对 clean parent quotes 做第二次 tick projection。一个 quote mutation 虽派生三个标准 market
fields，`realized chi_F` 仍为一个 logical point。Child 不新增 `task_price`，也不保留未 mutation 的
`settlement_price` 作为 before/theoretical-value 旁路。

### Type-signature selector

目标 authoring contract 不再做简单 50/50 positive/negative balance，而是覆盖：

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

Selector 顺序固定为 requested signature、execution profile、operator、slice/option target、sign、
absolute integer-tick grid。每个 grid point 都在内存 solver-visible projection 上重算全部 enabled
families；只接受 realized bitmask 精确等于 requested signature、active families 至少有一个
canonical candidate margin 达到 positive guard，且 inactive families 全部不构成套利并远离触发边界
的第一个 tick。不存在窗口时跳过 target，不随机 retry、不扩大 grid、不修改 parent 或 fee profile。

Spot-only mutation 不能直接改变 option-only cross-sectional inequalities；若 authoring-side scan 得到
cross-sectional bit，应视为 routing/implementation failure。Requested signature、selector trace 和
guard evidence 只写 private lineage，不能进入 public task/child identity，也不能成为 stored truth。

### Artifact 与 freeze 边界

```text
snapshots/generated/f2a/parents/<parent_snapshot_id>/parent.duckdb
snapshots/generated/f2a/children/<child_snapshot_id>/child.duckdb
snapshots/generated/f2a/children/<child_snapshot_id>/child.manifest.json
snapshots/private/f2a/<task_id>/lineage.json
datasets/manifests/tasks/f2a/<task_id>.json
```

Private lineage 至少记录 parent/child identities、stable selector、operator/tick、before/after、
requested/realized signature、execution/candidate contract IDs、每个 family 的 cost-adjusted margin 与
guard evidence。若现有 `additionalProperties: false` schema 没有字段，必须先 version schema；不能
私自附加字段。删除或篡改 private lineage 不得改变 Trusted verifier 从 public child 得到的 truth。

目前不存在 `scripts/materialize_f2a.py` 或 F2A child runtime，所以没有可执行的 child materialization
命令。在 public catalogue v3、calendar cashflow proof tests、signature selector contract 和独立 oracle
落位前，禁止手工复制/编辑 parent 来模拟 child。

## 使用方式

从仓库根目录运行，始终使用项目 `.venv`：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v4.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  create-smoke
```

追加日期：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v4.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  append-dates --days 1
```

查看或冻结：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v4.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  summary

.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v4.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  freeze
```

若只需要小型 incremental regression，可把 database/config 换成 `/tmp/authoring-smoke.duckdb`
和 `configs/generators/quantlib_bsm_smoke_v1.json`。不要把 database 指向 checked-in public DB
或 active legacy v3 development DB。

当前 public metals profile 的历史合同是
[`quantlib_bsm_metals_option_chain_smoke_v1.json`](../../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)，
生成 22 个 underlying、65 个交易日、1,232 个流动性固定合约和 60,368 条有效期内
option quotes。候选网格是 6 expiries × 11 moneyness × call/put，filter 只保留
30/60/90/180 天与 0.85–1.15 moneyness，因此每个 underlying 实际挂牌 56 个合约。
Current authoring successor 使用
[`quantlib_bsm_metals_option_chain_smoke_v2.json`](../../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json)
和新 v4 snapshot identity，不会生成 IV audit rows。

F2A parent 使用
[`quantlib_bsm_metals_f2a_parent_v2.json`](../../../configs/generators/quantlib_bsm_metals_f2a_parent_v2.json)，
路径、identity、所有表/字段、关联键、mutation 与 LLM 指引见
[`F2A parent DuckDB 数据字典`](../../../snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md)。
它已经 `FROZEN / r1`；只能
通过 read-only DuckDB connection 或 generated SQL 检查，不能对它运行 `append-dates`、`sync-config`、
`sync-range` 或任何 mutation/edit 命令。若 parent 文件缺失，应失败并报告，不能改连 active v3 或
public v3 后继续声称执行 F2A authoring。

## 测试

```bash
.venv/bin/pytest -q
```

重点测试：

- [`test_underlying_simulator.py`](../../../tests/unit/test_underlying_simulator.py)：
  $\Lambda/D/R$、stream partition、append invariance、private persistence 和 derivative
  boundary。
- [`test_option_chain_builder.py`](../../../tests/unit/test_option_chain_builder.py)：
  grid expansion、liquidity filtering、quote-noise replay、listing strike、stable contract
  identity、append/`sync-config` 和 chain immutability。
- [`test_authoring_smoke.py`](../../../tests/public/test_authoring_smoke.py)：
  transaction、NOOP、append、legacy additive config 与 freeze。
- [`test_q_pricing.py`](../../../tests/unit/test_q_pricing.py)：
  common-Q identity、exact integrated-variance pricing、price increments、legacy config
  read-only guard，以及 config 1.6 不生成 IV answers。
- [`test_f2a_repo_contracts.py`](../../../tests/unit/test_f2a_repo_contracts.py)：
  当前只验证 F2A config/schema/path/execution-cost/allowlist 的声明式骨架；不证明 child materializer、
  signature selector、calendar oracle、Solver/verifier 或 dataset 已实现。

修改 generator 时至少应保证：

1. 固定 config/seed 的 solver-visible rows 不变，除非变更本身明确版本化；
2. one-shot 与 append 结果一致；
3. option generator 不获得 underlying dependence/path-transition API；
4. `git diff --check` 和全量 tests 通过。
5. 旧 v3/带 authoring-IV solver 的 config 只能读取；任何新 materialization 使用 config
   `1.6.0` 与新的 config/generator/snapshot identity。

F2A runtime 实现还必须增加独立测试，至少覆盖：

1. 精确选择 F2A v2 parent path/DB/manifest identity；缺失时失败且不 fallback；
2. parent 始终 read-only，child 用新 identity 从 DRAFT 经过 gates 后 freeze，并可由完整输入重放；
3. option operator 只改变一个 logical quote point 并唯一派生 `mid/bid/ask`，spot operator 只改变
   child `spot_close`；
4. public child 严格匹配 allowlist，parent 新增字段不会自动进入，private lineage/label 不泄漏；
5. 每条 option/underlying trade 按 public execution contract 正确收费，guard 不改变 exact predicate；
6. stable tick search 对 `000` 与七种 positive signatures exact-match realized bitmask，不可达时
   deterministic skip；
7. calendar tests 逐现金流覆盖两个 segment primitives、`T1` rebalance、`T2` liquidation、cell
   boundaries 与 tail slopes，拒绝 raw maturity ordering、有限 spot-grid sampling 和 frictionless
   BSM replication；
8. authoring selector 与 verifier 不共享实现，篡改 private lineage 不改变 public-child truth。

## 扩展规则

- 新增 latent path state 前，先设计可持久化的完整 Markov restart state。
- 新增动态 option listing/roll 前，先版本化 exchange calendar、series identity 和 roll
  state；不要根据 daily spot 隐式重建历史合约。
- 新增 Q-measure multi-asset dependence 时，driver 仍是 underlying/model drivers，不能是
  derivative contracts。
- 修改已挂牌合约、历史 path 或 immutable dependence 时，必须使用新的 `snapshot_id`，
  不能在原 snapshot 内覆盖。
- F2A child materialization 只属于 `authoring/`；`mutation/` 不获得 DuckDB write、QuantLib 或
  oracle 权限，Trusted verifier 不获得 parent/private-lineage 权限。
- F2A numeric/domain gates 不得 clip、reprice 或修复待检测的 BSM bounds、parity、strike/calendar
  invariants；若合同要求新的 candidate formula 或 calendar rule，先更新 candidate/variant identity。
- 多点 mutation、`maximal_spread`、多个 pricing-model sources 或超出 frozen two-expiry catalogue 的
  global search 不属于 F2A v1；不能通过扩大 authoring selector 的职责偷偷接入。
