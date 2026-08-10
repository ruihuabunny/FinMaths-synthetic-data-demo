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
| F2A production parent contract | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1 / FROZEN` | 生产运行须显式提供 DB/manifest；Git 不跟踪该大文件，缺失时失败。只允许 read-only selection，不能 append、sync 或原地 mutation。 |
| F2A child authoring | v4 reference runtime 已实现 | `f2a_child_materializer.py` 完成纯 mutation 应用、独立 authoring X/U/T rescan、candidate evidence、冻结前 trusted-oracle 对照与原子 JSON fixture child。 |
| F2A contracts | v1 legacy；v2/v3 historical blocked；v4 executable | V4 启用 stock-flip calendar family，完整链 42 candidates；tracked CI fixture 已完成全部八种真实 tick signature audit。 |
| F2A v5.1 parent contract | `DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1 / r1 / FROZEN` | 生产/pilot 必须显式提供 distinct 126-day、三节点 parent；不能使用 v4 parent 或 legacy v3 fallback。 |
| F2A v5.1 task authoring | Pilot runtime 已实现 | `f2a_v5.py` 物化 8-underlying public/private packages，运行 Stage-1、逐 row IV inversion、linked validation、localisation、model-signal 与独立 v4 audit；release 仍受 2,000+ cohort gate 阻挡。 |

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
| [`f2a_child_materializer.py`](f2a_child_materializer.py) | V4 frozen-parent selection、tick reachability、copy-on-write child、candidate evidence 与 freeze。 |
| [`f2a_v5.py`](f2a_v5.py) | V5.1 8-underlying sampling、public/private subset authoring、mutation selection、estimator/signal/execution audits 与 publication-mode gate。 |
| [`f2a_calibration.py`](f2a_calibration.py) | Private task-seed FP/FN、Wilson bounds、8×8 confusion conservation、bootstrap maximum statistic 与 release-report validation。 |
| [`__init__.py`](__init__.py) | 对外只导出 `AuthoringPipeline`。 |

原来的单体 `generator.py` 已拆除。Product-specific generator 只共享基础设施，不互相
调用，避免 option quote 生成路径意外读取 P-measure correlation contract。

F2A v4 的独立 [`f2a_child_materializer.py`](f2a_child_materializer.py) 不属于现有
`AuthoringPipeline` 的普通 quote-generation 路径：它只读 frozen parent，按 allowlist 构建新 child、
执行 authoring-side signature selection/quality gates、写 private lineage 并 freeze 新 identity；
不会重新运行 QuantLib repricing。Reference smoke 使用明确的 tracked JSON fixture；生产 DuckDB adapter
仍必须显式选择 frozen v2 parent，缺失时不能 fallback。

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

F2A child 使用另一条可执行的 copy-on-write 流程：

```text
open exact F2A v2 parent read-only
  -> select one complete 4-expiry x 7-strike x call/put slice
  -> build an in-memory solver-visible projection by field allowlist
  -> enumerate clean control or one atomic mutation group on integer ticks
     (single quote / equal call+put pair / spot)
  -> recompute the full authoring-side family bitmask and active/inactive guards
  -> accept only exact requested-signature match; otherwise deterministic skip
  -> write a new DRAFT child + private lineage
  -> run child domain/identity/visibility gates
  -> freeze child under a new snapshot identity/revision
```

Mutation 层只产生 immutable spec/identity/lineage inputs，不打开 DuckDB。Trusted verifier 也不参与
上面的 selection：它随后只从最终 public child 和 public variant 使用独立实现重算 ORM truth。
Authoring-side selector 与 verifier-owned oracle 不共享实现。

V5.1 使用另一条 full-trajectory 流程，不复用 v4 的 56-row single-slice child shape：

```text
qualify exact 126-day / 22-underlying / three-node v5 parent read-only
  -> deterministically sample 8 unique underlyings
  -> export task-specific public DuckDB (node locations, never node values)
  -> run independent reference Solver and trusted V0/V1/V2/V3 verifier
  -> evaluate row-wise market IV and linked Stage-1 diffusion counterfactual
  -> apply at most one requested mutation group per selected slice
  -> rescan public model signals and independent v4 executable audit
  -> write public package plus separate private lineage/audits
```

Market-IV `invalid_bracket` rows 保留明确 status、没有 IV/market `d1/d2`，并从 model-signal candidates
排除；它们不会仅因 inversion failure 让 series 或 pilot authoring 失败。Release mode 还要求七种
nonzero requested signatures 和一份独立、gate-passing 的 2,000+ task-seed cohort report；当前 parent
的 `011` 在冻结 tick grid 下不可达，所以当前配置仍是 pilot。

## Snapshot identity 与生命周期

| 用途 | Config | Snapshot | 生命周期 |
|:---|:---|:---|:---|
| Legacy active development | `quantlib_bsm_metals_option_chain_smoke_v1.json` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3 / r1` | `DRAFT`，当前只读；不能重建旧 identity。 |
| New ordinary authoring | `quantlib_bsm_metals_option_chain_smoke_v2.json` | `DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v4` | 在新文件中从 DRAFT 开始，可按普通 pipeline sync/freeze。 |
| F2A clean parent | `quantlib_bsm_metals_f2a_parent_v2.json` | `DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2 / r1` | `FROZEN`，immutable mutation source。 |
| F2A child | 由 parent、public variant、mutation spec 与 private authoring contract 联合确定 | 每个 child 一个不泄露 label 的新 identity | V4 reference runtime：DRAFT in-memory projection 经独立 verifier exact-match 后原子写为 FROZEN；CI 产物写临时目录。 |
| F2A v5.1 parent | `quantlib_bsm_metals_f2a_v5_parent_v1.json` | `DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1 / r1` | 126-day distinct frozen source；path 由运行方显式提供。 |
| F2A v5.1 task package | `f2a_dataset_v5.json` + variant schema `5.1.0` | `DERIVATIVES-F2A-V5-CHILD-*` / output `model-reconstruction-xut-full-trajectory-v2` | Public/private artifacts 分离；默认 `PILOT_REQUIRES_COHORT_CALIBRATION`。 |

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
| [`bsm_arbitrage_finding_f2a_v1.json`](../../../configs/variants/bsm_arbitrage_finding_f2a_v1.json) / [`v2`](../../../configs/variants/bsm_arbitrage_finding_f2a_v2.json) / [`v3`](../../../configs/variants/bsm_arbitrage_finding_f2a_v3.json) / [`v4`](../../../configs/variants/bsm_arbitrage_finding_f2a_v4.json) | V1 是 legacy，v2/v3 是 blocked history，v4 是 executable calendar-enabled public contract。 |
| [`f2a_complete_v3.json`](../../../configs/mutations/f2a_complete_v3.json) / [`v4`](../../../configs/mutations/f2a_complete_v4.json) | V3 是 historical grammar；v4 的纯 runtime 实现 single quote、equal call+put group 与 spot，并公开 logical/physical counts。 |
| [`f2a_dataset_v3.json`](../../../authoring/configs/f2a_dataset_v3.json) / [`v4`](../../../authoring/configs/f2a_dataset_v4.json) | V3 保留 blocked audit target；v4 记录真实 reachability-proved 单-parent audit scope 与 `publication_task_count=8`。 |
| [`quantlib_bsm_metals_f2a_v5_parent_v1.json`](../../../configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json) | V5 full-trajectory 的 distinct 126-day、三节点、cent-tick parent DGP；不得用 v4/v3 替代。 |
| [`bsm_model_reconstruction_xut_signal_f2a_v5.json`](../../../configs/variants/bsm_model_reconstruction_xut_signal_f2a_v5.json) | Active schema `5.1.0`；分开冻结 physical fitting、80-step BSM inversion、linked validation、model signal 与 independent v4 audit。 |
| [`f2a_dataset_v5.json`](../../../authoring/configs/f2a_dataset_v5.json) | 8-underlying pilot sampling、六个当前可达 signatures、private FP/FN cohort gates 和 `submission-v5.1` artifact paths。 |

旧 files 不迁移，blocked v2/v3 也不能直接发布。V4 已用新 variant/catalogue identity 落地 evaluator、
finite-date admissibility proof tests、真实 reachability 和 independent verifier；不得把这些能力回写或
重标到历史 catalogue。V5.1 与 v4 共存：其 X/U/T 是 linked-counterfactual model signal，不能改写
v4 的 executable-arbitrage truth，也不能把 v4 oracle result当作 v5 scored answer。

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

F2A v4 不能直接复制 parent 中的同名 views，因为 parent pricing metadata 仍含 P dynamics、seed/RNG
和 authoring canonicalization。V4 child 只允许以下最小投影：

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

V5.1 使用不同的 task-specific public schema：

```text
solver_visible.underlying_daily
solver_visible.f2a_option_quotes
solver_visible.option_contracts
solver_visible.pricing_inputs
solver_visible.physical_node_locations
solver_visible.f2a_contracts
```

`physical_node_locations` 公开 drift/diffusion offsets 与 frozen role/index，不公开 node values；
`f2a_contracts` 分开保存 physical fitting、BSM inversion、linked validation 和 model-signal contracts。
Public manifest 使用 `f2a-public-duckdb-v5.1.0`，output contract 为
`model-reconstruction-xut-full-trajectory-v2`。任何 seed、clean quote、node value、mutation lineage、
private truth 或 FP/FN flag 都必须留在 private package。

## F2A v4 child authoring 合同（已实现）

### Parent selection 与 logical mutations

稳定 slice key 为
`(parent_snapshot_id, parent_revision, valuation_date, underlying_id)`。第一版只接受完整的
`4 expiries x 7 strikes x call/put = 56` 行 live chain；缺任一 expiry、strike 或 call/put mate 就
deterministic skip，不补 quote、不 fallback、不重新定价 parent。

V3 historical grammar 首次声明、V4 executable runtime 实现三个 mutation operators，加一个 clean
control：

- `mutate_option_price_point_v2`：只改变一个 `(valuation_date, option_id)` 的 logical quote point。
  Materializer 保留 parent half-spread，用 integer `delta_ticks` 唯一派生新的 `mid/bid/ask`；strike、
  expiry、call/put、spot、curves、pricing volatility contract 和其他 quotes 不变。
- `mutate_call_put_pair_equal_shift_v1`：原子选择 same valuation/underlying/expiry/strike/multiplier 的
  call+put pair，对两个 quote 施加同一 tick shift。它是一个 logical mutation group，但
  `logical_quote_points_changed = physical_quote_points_changed = 2`；lineage 必须保存两条 leg 的
  option ID 与 before/after。任一 leg 失败时整组 rollback。Equal shift 保持 same-pair parity，
  不保持 cross-asset bounds，故仍须 full rescan。
- `mutate_underlying_spot_point_v2`：只改变一个 `(valuation_date, underlying_id)` 的 `spot_close`；
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

F2A 不对 clean parent quotes 做第二次 tick projection。Single quote 的 logical/physical quote count
为 `1/1`；grouped pair 是一个 logical group、两个 quote points；每个 quote 的三项标准 market fields
只是确定性派生。Child 不新增 `task_price`，也不保留未 mutation 的
`settlement_price` 作为 before/theoretical-value 旁路。

### Type-signature selector

Complete-grammar authoring contract 不再做简单 50/50 positive/negative balance；以下是待 audit 的
target feasibility set：

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
absolute integer-tick grid。每个 grid point 都由 candidate-specific exact evaluator 先检查 terminal
certificate，再计算各自 open/closed setup-boundary distance 与 terminal guard vector；不使用统一
affine `spread - TC` 或 family `max(spread)`。所有可缩放 position vector 先 gcd-normalize。只接受
realized signature exact match 且 active/inactive guard 全部通过的第一个 tick；不存在窗口时
deterministic skip，不随机 retry、不扩大 grid、不修改 parent 或 fee profile。

Spot mutation 的精确不变量是 `X_after == X_before`。本 policy 在 mutation 前独立扫描并要求 clean
signature `000`，否则 deterministic skip；所以 accepted spot child 才进一步满足 `X_after=false`。
Before/after 不相等才是 routing/implementation failure。Requested signature、selector trace 和 guard
evidence 只写 private lineage，不能进入 public task/child identity，也不能成为 stored truth。

V4 的 requested signature 顺序固定为 `000,100,010,001,110,101,011,111`。Tracked CI fixture 通过
真实 option/spot mutation 和 full authoring oracle 已证明全部八种 signature 可达；smoke
`publication_task_count=8`。`001` 由 early ATM equal call+put 正向 shift 实现，其 exact integer tick
窗口为 `[132,290]`。Audit 记录位于
[`f2a_v4.json`](../../../datasets/manifests/splits/f2a_v4.json)，不使用抽象 affine trigger。

当前只有一个 parent snapshot。按 `parent_snapshot_id` grouping 时只允许一个 `audit` split；在至少
三个独立 parent worlds（或经版本化证明无 latent-world leakage 的更细 grouping）存在前，不能生成或
声称可用的 train/validation/test split。

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

V4 的非交互入口是 [`scripts/materialize_f2a.py`](../../../scripts/materialize_f2a.py)：

```bash
.venv/bin/python scripts/materialize_f2a.py build-fixture
.venv/bin/python scripts/materialize_f2a.py audit
.venv/bin/python scripts/materialize_f2a.py smoke --signature 001
```

Blocked catalogue v3 仍不是运行授权；新任务必须路由到 executable v4，且 child 只能在 independent
verifier exact-match 后冻结。

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

V5.1 parent 必须从 distinct config 物化到新文件；下面是 pilot 路径：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/f2a-v5-parent.duckdb \
  --config configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json \
  create-smoke
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/f2a-v5-parent.duckdb \
  --config configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json \
  freeze
.venv/bin/python scripts/materialize_f2a_agent_tasks.py \
  --parent-db /tmp/f2a-v5-parent.duckdb \
  --sampling-seed 20260808 --mutation-seed 20260808 \
  --target-signatures 001,010,100,101,110,111 \
  --publication-mode pilot \
  --output-dir /tmp/f2a-v5-tasks
```

`--qualify-only` 可在写 task package 前运行 parent qualification。默认 pilot 不请求当前 frozen grid
不可达的 `011`；release mode 强制请求全部七种 nonzero signatures 并要求 private cohort report，
所以不能仅把 `--publication-mode` 改成 `release` 来绕过 gate。

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
  验证 F2A v1/v2/v3 historical contracts；v4 coverage 另见 `test_f2a_repo_v4.py`、
  `test_f2a_calendar_v4.py` 与 integration `test_f2a_v4_runtime.py`。
- [`test_f2a_v5_stage1.py`](../../../tests/unit/test_f2a_v5_stage1.py)、
  [`test_f2a_v5_stage2_inversion.py`](../../../tests/unit/test_f2a_v5_stage2_inversion.py) 与
  [`test_f2a_v5_stage2_signal.py`](../../../tests/unit/test_f2a_v5_stage2_signal.py)：三节点 estimator、
  fixed bisection、linked validation、derived `d1/d2`、invalid-row exclusion 与 model-signal economics。
- [`test_f2a_v5_contracts.py`](../../../tests/unit/test_f2a_v5_contracts.py)：active v5.1 terminology、
  schema/config identities、public leakage、semantic exactness 与 cohort release gates。

修改 generator 时至少应保证：

1. 固定 config/seed 的 solver-visible rows 不变，除非变更本身明确版本化；
2. one-shot 与 append 结果一致；
3. option generator 不获得 underlying dependence/path-transition API；
4. `git diff --check` 和全量 tests 通过。
5. 旧 v3/带 authoring-IV solver 的 config 只能读取；任何新 materialization 使用 config
   `1.6.0` 与新的 config/generator/snapshot identity。

F2A v4 runtime tests 已覆盖：

1. 精确选择 F2A v2 parent path/DB/manifest identity；缺失时失败且不 fallback；
2. parent 始终 read-only，child 用新 identity 从 DRAFT 经过 gates 后 freeze，并可由完整输入重放；
3. single option operator 只改变一个 quote point；grouped call+put 原子改变两个 physical quote
   points 并逐 leg 唯一派生 `mid/bid/ask`；spot operator 只改变 child `spot_close`；
4. public child 严格匹配 allowlist，parent 新增字段不会自动进入，private lineage/label 不泄漏；
5. 每条 option/underlying trade 按 public execution contract 正确收费，guard 不改变 exact predicate；
6. stable tick search 对 `000` 与 audit 已证明 reachable 的 signatures exact-match realized bitmask，
   不可达时 deterministic skip；只有 audit 证明七种 positives 全部可达后才要求全覆盖；
7. calendar tests 逐现金流覆盖两个 segment primitives、`T1` rebalance、`T2` liquidation、cell
   boundaries 与 tail slopes，拒绝 raw maturity ordering、有限 spot-grid sampling 和 frictionless
   BSM replication；
8. authoring selector 与 verifier 不共享实现，篡改 private lineage 不改变 public-child truth。

F2A v5.1 tests 与本地 pilot 另行覆盖：8-underlying stable sampling、126-day/three-node parent
qualification、public node-location/private node-value split、Stage-1 covariance、逐 row 80-step IV
inversion、linked Stage-2 residual、`option_series_results` schema、V0/V1/V2/V3 exact comparison，以及
单 task-seed FP/FN 诊断。它们尚未提供 gate-passing 的 2,000+ cohort，也不构成 production sandbox
验收。

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
  global search 不属于当前 v4 executable contract；不能通过扩大 authoring selector 的职责偷偷接入。
- V5.1 market IV 是 required derived output，但不是 shared diffusion estimator；不得用 row-wise
  market-IV repricing 替换 linked counterfactual、localisation residual 或 X/U/T model signal。
