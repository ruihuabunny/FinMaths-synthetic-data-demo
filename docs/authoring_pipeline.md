# DuckDB + QuantLib Authoring Pipeline

> 实现状态（2026-08-12）：underlying simulator 第一阶段与 static
> `OptionChainBuilder` 与 liquidity-filtered quote profile 已经实现。Generator config
> `1.2.0` 可以用 $\Lambda/D/R$ 相关结构生成物理测度 $\mathbb P$ 下的多个
> underlying path；config `1.3.0` 生成 expiry × listing-moneyness × call/put 完整网格，
> 并冻结挂牌 strike；config `1.4.0` 从 candidate grid 只挂牌近月、近价合约，并以
> deterministic quote noise 扰动 BSM bid/ask；config `1.5.0` 使用 sampled-and-frozen
> piecewise-linear physical functions与显式 drift-only Girsanov Q mapping；config `1.6.0`
> 将 IV inversion 移出 authoring，config `1.7.0` 增加独立 P/Q underlying-driver
> dependence identities。Authoring schema `2.5.0` 保存 source/mapping/common-Q context。
> Option/derivative pricing 不读取 underlying 相关矩阵，原有 v1/v1.1/v1.2 pipeline
> 保持兼容。独立 exporter 与 BSM Greeks packaging pipeline 已能从 frozen P/Q parent
> 生成一条 accepted 的 8-underlying D4 golden task；Phase F batch runner 与九字段 dataset
> exporter 已实现。旧 solver interface 在 Git-ignored `runs/` 中留有本地 100-task 历史
> run；当前最小 prompt/interface 尚未重建、审核或 promotion 该批次。

## 目标与范围

第一版 authoring pipeline 将 framework 的 snapshot 合同落实到一个 DuckDB 文件中，并支持三类增量操作：

1. 在已有 5 天数据后追加第 6 天，只生成并插入第 6 天的数据；
2. 增加 underlying，在当前日期区间内只为新 underlying 生成路径、metadata 和 options；
3. 增加 option template，在当前日期区间内只生成新 option contracts 和 quotes。

Smoke test 中有 5 个 underlying 定义和 5 个 option templates。每个 template 会实例化到每个 underlying，因此数据库包含 25 个 option contracts，而不是总共 5 个合约。

Mutation + curriculum 本身不直接写 authoring DuckDB：`task_space` 只登记 snapshot
id/revision 和七维坐标（当前 task 固定 `F0`；旧六维输入仅由显式 adapter 迁移），
`mutation` 只产生 child task/lineage，`curriculum` 只计算
采样权重。联合 simulator 会扩展 authoring pipeline；若 mutation 改变市场状态、共享
利率路径、边际模型或联合依赖，仍须通过新的 authoring config/snapshot id 生成，再把
新 revision 注册到 child task。

## Underlying simulator 第一阶段（历史基线）

本节以
[`financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md`](financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
为设计基准。目标是把当前“逐 underlying 独立生成”的 smoke pipeline 扩展为同币种
underlying 生成器，同时保持确定性重放、事务写入、snapshot 不可变性与现有公开测试。

### 设计边界与不变量

- 本阶段只改变 `underlying_daily` 的 close path，且明确属于物理测度 $\mathbb P$。
- correlation matrix 的 driver 是按 `driver_order` 排列的 underlying spot processes，
  不是 option contract、Greek 或其他 derivative risk units。
- `option_daily_row()` 不接收 dependence spec，也不生成 correlated derivative shocks。
  Option quote 仍由当日可见 spot、声明的风险中性参数和 QuantLib engine 计算；固定 spot
  与定价参数时，切换 underlying correlation 不得改变该 option quote。
- Config `1.7.0` 已为 vanilla margins 声明共同 $\mathbb Q$/numeraire/rate-path identity、
  独立 P/Q dependence IDs 与 drift-only same-Brownian-covariance mapping；更复杂合法边际
  模型仍需新的显式 measure mapping，不能沿用这一 baseline。
- money-market numeraire 继续记为 $B_t$；factor-loading matrix 只记为 $\Lambda_t$。
- v1/v1.1 配置不声明 `underlying_simulation`，继续使用原来的逐 entity 独立 close
  stream，已有固定 seed 结果不改变。

### 配置合同

在不原地改变 v1/v1.1 smoke 配置语义的前提下，generator config `1.2.0` 新增一个
顶层 `underlying_simulation` 对象：

```json
{
  "schema_version": "1.2.0",
  "underlying_simulation": {
    "dependence_spec_id": "SYNTH-P-SPOT-FACTOR-v1",
    "measure": "P",
    "driver_order": ["SYNTH-U01", "SYNTH-U02"],
    "formulation": "factor_loading",
    "factor_loading_matrix": [[0.8], [0.5]],
    "idiosyncratic_diagonal": "derive_from_row_norms",
    "matrix_dtype": "float64",
    "factorization_method": "factor_loading_direct",
    "factorization_order": "declared_driver_order",
    "time_grid": "business_daily",
    "regime_id": "constant"
  }
}
```

完整可运行示例见
[`quantlib_bsm_correlated_underlyings.template.json`](../authoring/templates/quantlib_bsm_correlated_underlyings.template.json)。
当前实现合同如下：

1. `measure` 当前只能为 `P`；`driver_order` 必须恰好包含配置中的所有
   `underlying_id`，不能重复、缺失或加入 option id。
2. `factor_loading_matrix` 必须为 finite、非 ragged 的 $n\times k$ matrix，且每行
   squared norm 不超过 1。
3. 以 $\Lambda$ 为 source of truth，并按

   $$
   D=\operatorname{diag}\!\left(1-\lVert\lambda_1\rVert^2,\ldots,
   1-\lVert\lambda_n\rVert^2\right),\qquad
   R=\Lambda\Lambda^\top+D
   $$

   确定性派生并冻结 $D$ 与 $R$；config 不接受另行输入的 raw
   `correlation_matrix`。第一阶段只支持 business-daily constant regime。
4. identity baseline 可使用全零 loading rows，得到 $D=I,R=I$；不使用普通
   Cholesky 作为默认构造路径。
5. `matrix_dtype`、factorization method/order、time grid 与 regime 都是合同字段；
   当前分别固定为 `float64`、`factor_loading_direct`、
   `declared_driver_order`、`business_daily` 与非空 constant regime id。

### DuckDB schema 与持久化

本节记录 config `1.2.0` 当时将 authoring schema 从 `2.0.0` additive migration 到
`2.1.0` 的里程碑；当前实现已经是 schema `2.5.0` / config `1.7.0`，同时保存独立的
P/Q dependence identities、共同定价测度与 measure mapping：

| 对象 | 业务主键 | 当前内容 |
|:---|:---|:---|
| `market.underlying_dependence` | `(snapshot_id, dependence_spec_id)` | `measure=P`、`driver_order`、$\Lambda$、$D$、$R$、dtype、构造顺序、time grid、regime 和 authoring lineage。 |
| `market.pricing_metadata.physical_dynamics` | 现有主键 | 每个 underlying/date 保存 `dependence_spec_id`、`driver_id`、driver order 与 shock/RNG namespace。 |
| `metadata.snapshot_revisions` / manifest | 现有主键 | 新增 `underlying_dependence_count`。 |

在历史 `1.2.0` 阶段，`market.underlying_dependence` 只是 private P-measure authoring
DGP，且没有创建 `solver_visible.underlying_dependence` view。当前 `1.7.0` 只在完整 P/Q
pair、共同 Q/numeraire/rate-path identity 与映射均可验证时投影经过裁剪的 safe view；seed、
run provenance 和 private function nodes 仍不向 Solver 暴露。Option quote table 本身不增加
correlation 字段。

矩阵以规范 JSON/list 或等价的确定形态持久化；必须固定 float dtype、row-major 顺序、
driver order、数字 canonicalization 与 serialization order，确保相同 config 重放时
byte-identical。

### 生成顺序与随机流分区

当前 pipeline 的事务内顺序为：

```text
validate DRAFT/config and immutable underlying dependence
  -> derive and stage Lambda, D and R in canonical driver order
  -> generate common-factor and idiosyncratic shocks
  -> evolve all P-measure underlying paths on one time grid
  -> run the unchanged option quote pipeline from each realized spot
  -> MERGE all staging tables by stable business keys
  -> run cross-table and dependence quality gates
  -> record revision/manifest
  -> commit
```

联合 shock 使用

$$
Z_t=\Lambda\eta_t+D^{1/2}\varepsilon_t,
$$

其中 common factor shock $\eta_t$ 与 idiosyncratic shock $\varepsilon_t$ 相互独立。
随机流不再只按 entity 切分，改为以下稳定 namespaces：

```text
(snapshot_id, base_seed, "P", dependence_spec_id, "factor", factor_id, date)
(snapshot_id, base_seed, "P", dependence_spec_id, "idiosyncratic", underlying_id, date)
```

这样追加日期不会重画历史。第一阶段把整个 `underlying_simulation` 合同视为 snapshot
不可变定义：修改 driver order、loading、factor dimension、regime 或 underlying 集合
必须创建新 snapshot。v1/v1.1 legacy profile 仍保留原有的 additive underlying 规则。

### no-arbitrage 与质量门控

历史 `1.2.0` 阶段的 $R$ 只描述 P-measure underlying shocks 的联合分布，不是
derivative pricing 的 no-arbitrage 条件，也不用于构造 derivative quote correlation。
当前 `1.7.0` 已为 vanilla margins 声明共同 Q、money-market numeraire、rate-path context、
独立 Q-dependence identity 和 drift-only same-Brownian-covariance mapping；这些声明仍不把
相关性注入单资产 vanilla price。更复杂边际或联合 payoff 必须另行声明合法的 Q dynamics
与 measure mapping。authoring gate 负责：

- 校验 measure、driver ids/order、matrix shape/dtype 和 factorization contract；
- 校验 $\Lambda$ 有限且 row norm 不超过 1，并重放
  $D=\operatorname{diag}(1-\lVert\lambda_i\rVert^2)$ 与
  $R=\Lambda\Lambda^\top+D$；PSD 由 factor construction 内生保证；
- 校验一个 snapshot 最多只有一个且不可变的 underlying dependence spec；已有路径
  不得补挂、删除或修改该 spec；
- 校验切换 $R$ 后，在相同固定 spot 和 pricing inputs 下 option quote 完全相同；
- 保留原有 cross-table、missing quote、path-gap、rollback 和 freeze gates。

### 分阶段实施

| 阶段 | 状态 | 代码范围 | 交付与退出条件 |
|:---|:---|:---|:---|
| U0. 契约冻结 | 完成 | `config.py`、文档 | 固定 `measure=P`、underlying-only driver order、$\Lambda/D/R$ canonicalization 与不可变规则。 |
| U1. Config + persistence | 完成 | `config.py`、`schema.py`、模板 | config `1.2.0`、schema `2.1.0`、private dependence table、revision/manifest count 和旧配置兼容。 |
| U2. 联合 underlying path | 完成 | `underlying_daily_generator.py`、`pipeline.py` | factor/idiosyncratic namespaces、相关 close shock、one-shot/append replay 与 metadata lineage。 |
| U3. Underlying simulator 扩展 | 计划中 | config/generator/tests | time-varying $\Lambda_t$、regime transitions、richer P dynamics；仍不把 option ids 放入 correlation matrix。 |
| Q1. Pricing context/model | 已完成（vanilla baseline） | config/schema/gates | `1.7.0` 已冻结共同 $\mathbb Q$/numeraire/rate path、独立 P/Q dependence identities 与 drift-only mapping；联合 payoff 与 richer Q dynamics 仍是后续范围。 |

### 测试与最终验收

除保留现有 smoke tests 外，新增以下测试层：

- config unit tests：duplicate/missing underlying driver、matrix shape、row norm、固定
  P/time-grid/factorization contract 和 legacy compatibility；
- algebra tests：固定 $\Lambda$ 下 $D$、$R$ 的规范重放与 factor/idiosyncratic 合成；
- deterministic generator tests：相同 seed byte-identical，不同 namespace 独立，追加
  日期不改历史；
- integration tests：private dependence row 和 physical lineage 完整，transaction failure
  rollback，禁止同 snapshot 修改 dependence；
- derivative-boundary test：固定 spot 与 pricing inputs，仅改变 loading/correlation，
  `option_daily_row()` 输出不变；
- visibility/freeze tests：dependence DGP 不进入 solver view，冻结后拒绝所有修改。

第一阶段完成定义是：新 underlying profile 可由固定 config/seed 完整重放；旧 smoke
profile 无回归；$\Lambda/D/R$ 可规范重放；相关结构只改变 underlying close shocks；
option engine 不读取该结构；snapshot 冻结和不可变规则继续成立。

## OptionChainBuilder 第一阶段

Config `1.3.0/1.4.0` 落实 README 的阶段 1，只改变 option contract construction 和
spread microstructure；config `1.5.0` 在不改变 frozen chain identity 的前提下替换旧的
latent-smile volatility 输入，并引入明确的 single-asset common-$\mathbb Q$ contract。

### Config `1.3.0`

`1.3.0` 继承 `1.2.0` 的 immutable `underlying_simulation`，并以顶层
`option_chain` 替代逐条 `option_templates`。下面展示 listing-moneyness 模式；也可以删除
`moneyness_grid` 并声明互斥的 absolute `strike_grid`：

```json
{
  "schema_version": "1.3.0",
  "option_chain": {
    "chain_id": "STATIC-GRID-v1",
    "expiry_days": [30, 90, 180],
    "moneyness_grid": [0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2],
    "call_put": ["call", "put"],
    "listing_rule": "snapshot_start",
    "roll_rule": "static",
    "strike_increment": 0.5,
    "strike_rounding": "ROUND_HALF_EVEN",
    "exercise_style": "european",
    "settlement_type": "cash",
    "contract_multiplier": 100
  }
}
```

当前合同固定如下：

1. expiry 与所选 grid 都必须非空、正值且严格递增；`moneyness_grid` / `strike_grid`
   必须恰好出现一个；call/put 必须各出现一次。
2. 第一版只支持 `snapshot_start` listing 与 `static` roll。动态 weekly/monthly/quarterly
   series 需要后续 exchange profile，不允许在本版隐式发生。
3. listing date 是 `start_date` 按 `WeekendsOnly/Following` 调整后的首个 business date；
   listing spot 使用配置中明确的 `initial_spot`。
4. Moneyness 模式的 listing strike 按
   `initial_spot × moneyness / strike_increment` 执行 `ROUND_HALF_EVEN` 后还原成绝对
   strike；absolute-strike 模式直接按相同 increment/rounding 规范化。若两个 grid
   inputs 舍入到同一 strike，整个 config 被拒绝。
5. template/option ID 只由 stable underlying id、chain id、expiry、moneyness 与 call/put
   派生，不依赖生成循环位置。listing 后绝对 strike 永不随 daily spot 重算。

### Schema、增量与边界

Option-chain provenance 首次由 schema `2.2.0` 引入；当前 schema `2.5.0` 支持 mutable
`2.0.0`--`2.4.0` migration，冻结库保持只读且不原地迁移：

| 对象 | 作用 |
|:---|:---|
| `market.option_chain_specs` | 私有保存 chain candidate grid、listing/roll、strike rounding、合约约定、liquidity/quote model 与 lineage；无 solver view。 |
| `market.option_contracts` | 新增 nullable `chain_id, listing_date, listing_spot, strike_moneyness`；legacy 已有行不改写。 |
| `market.option_pricing_audit` | Legacy `1.5.0` 私有保存 Q identity/mapping、effective volatility、未舍入理论价、canonical mid、derived IV/状态和 solver contract；current `1.6+` 不写；无 solver view。 |
| `metadata.snapshot_revisions` / manifest | 保存 `option_chain_spec_count` 与 `option_pricing_audit_count`。 |

一个 `1.3.0` snapshot 将 chain spec、underlying 集合和全部已挂牌 contracts 视为不可变
整体。one-shot、append 与 `sync-config` 每次重建相同 expected contract rows，再与数据库
逐字段比较；修改 grid、increment、initial spot、expiry、roll 或 stable ID 必须使用新
`snapshot_id`。Quality gates 另验证 chain contract 必须能关联私有 spec、listing 字段完整，
以及每条 option quote 的静态合约字段必须与 contract master 一致。

Correlation boundary 保持不变：`OptionChainBuilder` 的 Cartesian grid 不进入
`underlying_simulation.driver_order`；当前 1,232 个 option contracts 也不会扩张
underlying 的 22×22 $R$。Option rows 仍只读取 realized spot、contract 和现有 pricing
inputs。

### Config `1.4.0` liquidity 与 quote noise

`1.4.0` 继承 static listing/roll 语义，并把 expiry/moneyness arrays 明确定义为 candidate
grid。`liquidity_filter` 在 contract materialization 前执行：

```json
{
  "liquidity_filter": {
    "type": "listing_moneyness_band_and_max_expiry",
    "max_expiry_days": 180,
    "min_moneyness": 0.85,
    "max_moneyness": 1.15,
    "boundary": "inclusive"
  }
}
```

筛选依据 listing-time inputs，后续 spot 移动不会让合约隐式出现或消失。第一版只接受
moneyness grid；absolute-strike grid 的跨 underlying liquidity normalization 留待后续
exchange profile。

`quote_model.bid_ask_noise` 使用按 `(snapshot, seed, namespace, side, option_id, date)`
分区的 Gaussian draw，分别乘在 bid/ask baseline half-spread 上并 clip 到声明边界。
QuantLib analytic BSM NPV 始终保存为 `mid` 和 `settlement_price`，噪声不进入 volatility、
discounting 或 underlying simulation。完整 filter 与 quote model 均写入 private
`market.option_chain_specs`，同一 snapshot 内不可改变。

## 文件

| 文件 | 用途 |
|:---|:---|
| `configs/generators/quantlib_bsm_smoke_v1.json` | 固定 seed、模型、underlyings、option templates 与 quote rules。 |
| `authoring/templates/quantlib_bsm_correlated_underlyings.template.json` | 可运行的 config `1.2.0` correlated-underlying 示例。 |
| `configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json` | 冻结 config `1.5.0` 的 22-underlying 历史 profile；保留 private authoring IV audit 仅用于迁移审计。 |
| `configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json` | 当前 writable config `1.6.0` baseline；保留 Q pricing inputs，但不在 authoring 层生成 IV answers。 |
| `configs/task_packages/bsm_market_implied_greeks_v1.json` | D4 golden task 的七维坐标、确定性 selection、精确 method/schema/capability 合同，以及用于 stable identity 的 solver-interface 版本。 |
| `snapshots/public/quantlib_bsm_smoke_v1.duckdb` | 已冻结的 metals public snapshot；文件路径为向后兼容保留名。 |
| `snapshots/public/quantlib_bsm_smoke_v1.manifest.json` | 当前 logical revision、版本标识和行数。 |
| `scripts/edit_snapshot.py` | 仓库本地 `.venv` 使用的编辑入口。 |
| `scripts/sample_physical_dynamics.py` | 从声明分布可重放地抽样 physical drift/volatility nodes，并将 realized nodes 冻结到 config。 |
| `scripts/package_bsm_greeks_task.py` | 从现有 frozen `1.7` parent，或从 `1.6` baseline 临时构造 private P/Q parent，构建并验证 accepted/released package。 |
| `src/synthetic_derivatives/authoring/generator_common.py` | 两个 generator 共享的 pinned QuantLib、calendar/day-count、decimal canonicalization 与 deterministic RNG。 |
| `src/synthetic_derivatives/authoring/underlying_daily_generator.py` | Underlying master/dependence、P path 与 pricing metadata；唯一消费 $\Lambda/D/R$ 的 generator。 |
| `src/synthetic_derivatives/authoring/option_daily_generator.py` | Option chain spec、frozen contracts 与 daily quotes；只接收 realized spot，不提供 underlying dependence API。 |
| `src/synthetic_derivatives/authoring/pipeline.py` | 显式编排两个 generator、DuckDB transaction、incremental MERGE 与 quality gates。 |
| `src/synthetic_derivatives/export/solver_database.py` | 从 frozen parent 确定性写出独立 public-only child、stable IDs、logical checksum 与 read-only handoff。 |
| `src/synthetic_derivatives/export/contracts.py` | 固定 public table/column/type/order、export identity、subset manifest 与 recursive leakage denylist。 |
| `tests/public/test_authoring_smoke.py` | 初始规模、幂等、追加日期、增加品种和 freeze 测试。 |
| `tests/unit/test_underlying_simulator.py` | $\Lambda/D/R$、联合 shock、持久化、append invariance 与 derivative boundary 测试。 |
| `tests/unit/test_option_chain_builder.py` | chain config、完整网格、listing strike、不可变性及 append/sync tests。 |
| `tests/integration/test_solver_database_replay.py` | 22→8 public child、parent immutability、replay checksum、nested leakage 与只读边界。 |
| `tests/packaging_analytic_and_implied_greeks_iv/` | D4 package 的数据库、runtime、trajectory、verifier、leakage、release-view 与 replay 端到端合同。 |

## DuckDB schemas（当前实现）

数据库分为三个 SQL schemas。

### `market`

| 表 | 业务主键 | 说明 |
|:---|:---|:---|
| `market.underlyings` | `(snapshot_id, underlying_id)` | Underlying master 和 P/Q 参数入口。 |
| `market.underlying_dependence` | `(snapshot_id, dependence_spec_id)` | P/Q measure-qualified factor-loading 合同；完整 P/Q pair 通过 safe solver view 投影，不含 seed/run provenance。 |
| `market.option_chain_specs` | `(snapshot_id, chain_id)` | 私有 option-chain listing/grid/roll/rounding 合同；不进入 solver views。 |
| `market.option_contracts` | `(snapshot_id, option_id)` | Option 合约静态字段及 listing provenance；绝对 strike 挂牌后冻结。 |
| `market.underlying_daily` | `(snapshot_id, date, underlying_id)` | Framework 要求的 underlying daily panel。 |
| `market.option_daily` | `(snapshot_id, date, option_id)` | Framework 要求的 option daily quotes。 |
| `market.pricing_metadata` | `(snapshot_id, valuation_timestamp, underlying_id)` | 每个 valuation slice 的 P/Q dynamics、curves、engine、seed、RNG 和 canonicalization。 |
| `market.option_pricing_audit` | `(snapshot_id, valuation_date, option_id)` | 仅 legacy `1.5.0` 写入的 private IV migration audit；current `1.6+` materialization 不写该表，task truth 从 solver-visible canonical quotes 重算。 |

daily/metadata 表包含 framework 指定的全部字段。内部表额外保存 run id，用于 lineage
和审计；幂等写入直接依赖业务主键。

### `metadata`

| 表 | 说明 |
|:---|:---|
| `metadata.schema_versions` | DuckDB schema version。 |
| `metadata.snapshots` | Snapshot 状态、当前 revision 与依赖版本。 |
| `metadata.generation_runs` | 每次增量操作的范围、状态、逐表 inserted/unchanged 统计和失败信息。 |
| `metadata.snapshot_revisions` | 每次产生逻辑变更后的行数。 |

### `solver_visible`

只暴露 framework 合同字段：

- `solver_visible.underlying_daily`
- `solver_visible.option_daily`
- `solver_visible.pricing_metadata`
- `solver_visible.underlying_dependence`（仅完整 P/Q pair）

这些 authoring-DB views 只用于投影源，不构成最终文件权限边界。生产 Solver 必须接收由
`synthetic_derivatives.export` 新建的独立 child 文件，不能打开包含 `market` tables 的 parent。

### 独立 public child

Public schema `solver-market-public-duckdb-v1.0.0` 固定六张 base tables：

- `metadata.public_task`
- `solver_visible.underlying_state`
- `solver_visible.option_contracts`
- `solver_visible.option_chain_quotes`
- `solver_visible.pricing_context`
- `solver_visible.underlying_dependence`

Exporter 只接受单一 `FROZEN` parent；以 `READ_ONLY` attach 读取选中的 valuation date、
underlyings 和 option IDs，写完后 detach。Sampling seed 只参与 SHA-256 stable identity，不写入
child。P/Q dependence 同步截取 $\Lambda$ rows、$D$ entries 与 $R$ principal submatrix，因而
不改变 factor law，也不进行 PSD repair。

Child 不含 `market` schema、generation run IDs、seed/RNG、authoring IV audit、private function
node heights、oracle/answer 或 parent path。Pre-export payload 与完成后的每个 public value 都会
递归扫描；JSON strings 会先解析再检查 nested keys。Logical checksum 哈希固定类型合同和全部
canonical rows，不读取 DuckDB file bytes，因此不受物理布局/mtime 影响。最终只通过
`open_solver_database()` 的 read-only connection 交付；完整合同见
[`src/synthetic_derivatives/export/README.md`](../src/synthetic_derivatives/export/README.md)。

### D4 BSM Greeks golden package

Task-specific packaging 在上述 generic public child 之后再做一次最小投影。当前 accepted
`bsm_market_implied_greeks_v1` 从 valuation-date listing inputs 确定性选出 8 个
underlyings；每个 underlying 保留两个最近 live expiries、每个 expiry 五个最接近 forward
ATM 的 strikes，以及完整 call/put pairs。最终 Agent DuckDB 恰有 160 rows 和三张 base
relations：

- `metadata.public_task`；
- `solver_visible.greeks_task_inputs`；
- `solver_visible.greeks_task_contract`。

三关系 DB 不保存 P/Q loading matrices；但其 public provenance 固定 parent/public-child
logical checksums、joint-market contract、P/Q dependence IDs 和 drift-only covariance mapping
policy。相关性只约束共同市场身份，不进入单资产 vanilla BSM 边际 price/Greeks。

`public/prompt.md` 只是最小路由，不再复制 method、data dictionary、submission schema 或
runtime permissions。它只指向四个 public source：contract query、ordered-input query、
`public/submission.schema.json` 与 `public/runtime_contract.json`。BSM 的 $\mathbb Q$/numeraire、
定价 law、visible-mid IV schedule、Greek units、binary64 checkpoints、rounding 和 row order
全部由三关系 DB 中的 canonical method contract 唯一定义；输出 schema 与 runtime contract
分别只拥有结构和能力语义。

Agent 通过各一次的 trusted contract/input query adapter 读取数据，不得到 raw DuckDB handle。
Reference solver 在 effective runtime allowlist 下两次重放；trusted verifier 只从 public
bid/ask 和冻结 method config 用 QuantLib 独立重建 80-step IV 与五个 unit Greeks。Source
package 自动导出 authoring、train/dev、evaluation views；私有 artifact manifest 覆盖所有源
制品，evaluation view 只含 root manifest 与 `public/`。构建入口和当前边界见
[`task_packages/README.md`](../task_packages/README.md)。

## QuantLib 生成方法（当前实现）

### Underlying path

每个 underlying 使用 `QuantLib.BlackScholesMertonProcess.evolve` 生成 P-measure
time-inhomogeneous GBM close：

\[
\frac{dS_t}{S_t}=\mu(t)dt+\sigma(t)dW_t.
\]

$S_t>0$ 是 USD/underlying-unit 的 synthetic ex-dividend spot，不是 total-return index。
$\mu$ 是 $\mathbb P$ 下年化瞬时期望价格收益率（year$^{-1}$），$\sigma$ 是年化瞬时收益
标准差（year$^{-1/2}$）；时间轴为 calendar time，day-count 为 Actual/365 Fixed。
在 $\mathcal F_{t_i}$ 已知当前 published close vector、确定性 functions、日期与冻结 P spec；
未来 date namespace 的 factor/idiosyncratic normal shocks 与过去 disjoint intervals 独立，
同一日期按冻结 $\Lambda/D/R$ 联合。当前模型没有额外 latent state。

`physical_drift` 和 `physical_volatility` 既可以是向后兼容的 scalar，也可以是以
`start_date` 为原点的 `piecewise_linear` deterministic function。每个 close
interval 对 \(\mu(t)\) 精确积分并取算术平均，对 \(\sigma^2(t)\) 精确积分并取
root-mean-square；得到的 interval-equivalent 参数交给 QuantLib 的 exact GBM
proposal transition。Proposal 再按 underlying minimum price increment 做
`ROUND_HALF_EVEN`，published close 作为下一 interval 的 restart state；因此实际
materialized law 是 rounded-state Markov chain，而不是保留未舍入 latent close 的
continuous-state GBM。物理 drift/volatility function 与风险中性定价参数分开保存，完整函数
和当日有效参数写入 `pricing_metadata.physical_dynamics`。

`start_date` 行只 materialize $S(t_0)=S_0$，OHLC 均为 `initial_spot`，不抽取从虚构前一日
到 $t_0$ 的 transition。后续行才从 preceding materialized state 按真实 calendar interval
演化。Checked-in configs 的 `initial_spot` 都已与 underlying tick 对齐；parser 目前不显式
拒绝非对齐自定义值，而 generator 会先量化该值，所以这种输入不能主张原始值就是 initial
condition。当前 metals config 由 [`sample_physical_dynamics.py`](../scripts/sample_physical_dynamics.py)
按 underlying ID 分区随机流，分别抽取互不相同的 per-underlying seed、7-node offset grid、
drift phi/std、log-vol phi/std 和有界均值回归 Gaussian/lognormal nodes。Global seed、
per-underlying seeds、每个参数的 hard bounds 和 realized values 全部冻结；路径生成只消费
realized nodes。

OHLC 另有明确但简化的 observation contract：`open = previous published close`，即 no-gap；
`high/low` 由 `separate_synthetic_range-v1` heuristic stream 构造，不来自同一 intraperiod
path、Brownian bridge 或 exact range distribution。Volume 使用独立 uniform stream。
`adjusted_close = close`、`dividend = 0`、`corporate_action = none` 也是显式规则。这些字段不
支持 barrier、realized-range、overnight-gap、total-return 或真实流动性推断。

随机流不是一个依赖循环顺序的全局 stream。v1/v1.1 使用以下 tuple 派生 32-bit seed：

```text
(snapshot_id, base_seed, purpose, entity_id, market_date)
```

config `1.2.0` 则按上文的 factor 与 idiosyncratic namespaces 派生 close shocks。两者均由
`QuantLib.MersenneTwisterUniformRng` 和
`QuantLib.BoxMullerMersenneTwisterGaussianRng` 产生 draw。相关结构只替换 close
transition 的 $Z_t$；range、volume 与 option activity 仍使用各自的稳定 streams。

### Option quotes

`1.3.0+` 先由 `OptionChainBuilder` 生成 frozen contract grid；`1.4.0+` 在 materialization
前应用 liquidity filter。Checked-in historical public config `1.5.0` 每个 valuation date 对已挂牌合约使用：

- `QuantLib.BlackScholesMertonProcess`
- flat continuous risk-free/dividend curves
- money-market-numeraire Q drift $r-q$
- drift-only Girsanov mapping 下的 $\sigma_Q(t)=\sigma_P(t)$
- valuation-to-expiry integrated-variance RMS volatility
- `QuantLib.AnalyticEuropeanEngine`
- European call/put payoff

QuantLib NPV 先按 `ROUND_HALF_EVEN` 量化到 8 位小数，作为
`mid = settlement_price`；side-specific deterministic noise 只改变 bid/ask half-spread。
随后调用 `QuantLib.VanillaOption.impliedVolatility` 对同一个 canonical mid 反解；输入 Q
volatility、raw NPV、derived IV 或 `NO_FINITE_IV` 状态写入 private
`market.option_pricing_audit`。Pricing volatility 不被当成下游 IV 答案。
Current `1.6.0+` 仍用同一声明的 Q-pricing law 生成 canonical quotes，但不再执行或持久化
authoring IV inversion；IV/Greeks truth 由 task/verifier 从 solver-visible price 重算。

## 增量写入语义（当前实现）

每次运行使用一个 DuckDB transaction：

```text
validate DRAFT/config
  -> create generation run
  -> load incremental rows into temporary staging tables
  -> MERGE by stable business primary key
  -> run cross-table quality gates
  -> record revision
  -> commit
```

任一生成、constraint 或 quality-gate 错误会 rollback 整个数据批次，并记录 `FAILED` run。Snapshot 通过 `snapshot_id` 和递增 revision 标识，不再计算额外的内容摘要。

已有 entity definition 和历史路径是不可变的：

- v1/v1.1 配置可以追加 underlyings 或 option templates；
- 已存在的定义按业务主键保留，配置中的删除或原地修改不会重写它们；
- 不能回填 underlying path 中间的日期缺口，因为这会使后续路径失效；
- 需要修正历史或模型参数时，应使用新的 `snapshot_id`。
- config `1.2.0` 的 `underlying_simulation` 与 underlying 集合整体不可变；修改 loading、
  driver order 或增删 underlying 时必须使用新的 `snapshot_id`。
- config `1.3.0` 的 option chain spec 和已挂牌 contracts 整体不可变；append/sync 只生成
  缺失 daily rows，不能改变 listing strike 或 contract identity。
- config `1.4.0` 的 liquidity filter 与 quote model 同属 immutable chain provenance；
  修改边界、spread 或 noise namespace 必须使用新的 `snapshot_id`。
- config `1.5.0` 的 sampled physical nodes、sampling provenance、Q identity/mapping 与 IV
  solver contract 都属于 snapshot identity；修改任一项必须使用新的 `snapshot_id`。
- config `1.6.0` 移除 authoring IV answer materialization；不能在同一 snapshot identity 下
  回填 legacy audit rows。
- config `1.7.0` 的共同 Q/numeraire/rate-path context、P/Q dependence identities 与
  measure mapping 都属于 snapshot identity；任何变化都必须产生新 snapshot。

## 命令

所有命令都必须使用仓库内 `.venv`。

### 创建或幂等同步 scratch smoke snapshot

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/quantlib-bsm-smoke-v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  create-smoke
```

再次运行返回 `NOOP`，revision 保持不变。

### 追加交易日

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/quantlib-bsm-smoke-v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  append-dates --days 1
```

### 增加 underlying 或 option

在 config 的 `underlyings` 或 `option_templates` 数组末尾添加新定义，然后执行：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/quantlib-bsm-smoke-v1.duckdb \
  --config path/to/additive-config.json \
  sync-config
```

`sync-config` 使用数据库当前的最小/最大日期，为新增实体回填已有时间范围。

### 查看与冻结

```bash
.venv/bin/python scripts/edit_snapshot.py --database path/to/snapshot.duckdb \
  --config path/to/config.json summary

.venv/bin/python scripts/edit_snapshot.py --database path/to/snapshot.duckdb \
  --config path/to/config.json freeze
```

`DRAFT` 可以增量编辑；`FROZEN` 会拒绝任何后续写入。若要扩展已发布 snapshot，复制配置并使用新的 `snapshot_id` 创建新数据库。

### 重放 22 品种 legacy audit profile

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v2.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  create-smoke
```

期望规模为 22 个 underlyings、1,232 个 option contracts、65 个 business dates、1,430
条 underlying daily、60,368 条 expiry 前 option daily、1,430 条 pricing metadata 与
60,368 条 private option pricing audit。
Candidate grid 是 6 expiries × 11 moneyness × call/put，filter 实际保留 4 × 7 × 2。
该 profile 验证规模、liquidity selection、确定性和 BSM pricing sanity，不代表真实金属
交易所 calendar/carry/settlement profile。不同 valuation/expiry 区间因 integrated variance
不同而使用不同 effective volatility；所有 strike 仍来自同一个 coherent deterministic-
time-varying-diffusion BSM marginal model。

上述 `1.5.0` profile 是迁移审计基线，不应作为新 task 的答案来源。当前 writable
`1.6.0` baseline 使用同样的市场规模，但 `market.option_pricing_audit` 行数为 0：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v4.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  create-smoke
```

### 构建并验证 accepted D4 golden package

构建入口会从 `1.6.0` baseline 在临时目录生成独立的 frozen `1.7.0` P/Q parent；parent
不会写入 task package，且完成后由临时目录回收：

```bash
.venv/bin/python scripts/package_bsm_greeks_task.py \
  --base-parent-config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  --config configs/task_packages/bsm_market_implied_greeks_v1.json \
  --output-root /tmp/bsm-task-packages \
  --build-status ACCEPTED
```

当前 checked-in golden task 是
`bsm-mig-v1-bde472c5cb0ca8a660314c9e`：8 个 underlyings、2 个 live expiries、每 expiry
5 个 strikes、call/put 成对，共 160 行；agent database 仅含 3 个允许关系。它仍处于
`ACCEPTED`。Phase F 已提供 seed-parameterized batch runner 和 verified nine-field dataset
exporter。旧 interface 有 Git-ignored 本地 100-task 历史 run；当前 interface 尚未执行完整
100-task rebuild、split audit 或 `RELEASED` promotion。

采用最小 prompt 的新构建必须先计算 `solver_interface_digest`。该 canonical digest 绑定
solver-interface contract version、rendered prompt、method contract、submission schema 和
effective runtime contract，并进入 stable task-ID 公式；trusted adapter/input surface 的语义
变化通过升级 interface version 纳入 identity。任何一个接口变化都必须生成新 task directory。
当前 checked-in `ACCEPTED` package 已在新 identity 完成构建、replay、verifier、leakage、
release-view 和源码隔离验收后完成显式 promotion。

## Smoke-test 验收（当前实现）

```bash
make test
```

测试覆盖：

- 5 underlyings × 5 option templates × 5 business dates；
- 重跑完全幂等；
- 第 6 天只插入一个日切片；
- 新 underlying 只回填该实体；
- 新 option template 只回填新合约族；
- solver-visible views 覆盖 framework 字段；
- frozen snapshot 拒绝增量写入；
- config `1.2.0` 的 $\Lambda/D/R$ 可重放，相关 underlying 路径满足 append invariance；
- 固定 spot 与 pricing inputs 时，underlying correlation 不影响 option quote；
- config `1.3.0` 展开 3 expiries × 7 strikes × paired call/put，listing strike 跨日固定；
- chain one-shot/append/`sync-config` invariant，修改 chain spec 或 listed contract 被拒绝；
- config `1.4.0` candidate grid 只 materialize 期限/moneyness filter 内的合约；
- quote noise 可重放、bid/ask 独立且不改变 BSM mid；
- physical node sampling 固定 seed replay，start date 保持精确 initial condition；
- legacy `1.5.0` integrated-Q-variance/canonical-mid IV audit 与 solver visibility boundary；
- current `1.6.0` authoring 不写 IV answers，`1.7.0` parent 完整冻结共同 Q、P/Q
  dependence identities 与 mapping；
- legacy 22 品种 profile 的 1,232 contracts、60,368 quotes 与等量 private audits，以及
  current baseline 的零 authoring-IV rows；
- accepted D4 package 的 3-relation/160-row public DB、四源最小 prompt routing、
  `solver_interface_digest`、80-step bisection + analytic Greeks、independent QuantLib verifier、
  runtime limits、leakage scans、release-view byte identity 与 two-replay determinism。

## 版本与参考

- Python `3.12`
- DuckDB `1.5.5`
- QuantLib Python binding `1.39`
- pytest `8.4.1`

DuckDB 的 `MERGE INTO`、transactions 与 constraints 设计分别参考官方文档：

- <https://duckdb.org/docs/current/sql/statements/merge_into>
- <https://duckdb.org/docs/current/sql/statements/transactions>
- <https://duckdb.org/docs/stable/sql/constraints>

QuantLib European analytic engine 参考：

- <https://quantlib-python-docs.readthedocs.io/en/latest/pricing_engines/options.html>
