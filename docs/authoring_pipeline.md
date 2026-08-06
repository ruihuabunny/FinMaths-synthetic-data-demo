# DuckDB + QuantLib Authoring Pipeline

> 实现状态（2026-08-06）：underlying simulator 第一阶段与 static
> `OptionChainBuilder` 已经实现。Generator config
> `1.2.0` 可以用 $\Lambda/D/R$ 相关结构生成物理测度 $\mathbb P$ 下的多个
> underlying path；config `1.3.0` 生成 expiry × listing-moneyness × call/put 完整网格，
> 并冻结挂牌 strike；authoring schema `2.2.0` 保存两类私有生成合同。
> Option/derivative pricing 不读取 underlying 相关矩阵，原有 v1/v1.1/v1.2 pipeline
> 保持兼容。

## 目标与范围

第一版 authoring pipeline 将 framework 的 snapshot 合同落实到一个 DuckDB 文件中，并支持三类增量操作：

1. 在已有 5 天数据后追加第 6 天，只生成并插入第 6 天的数据；
2. 增加 underlying，在当前日期区间内只为新 underlying 生成路径、metadata 和 options；
3. 增加 option template，在当前日期区间内只生成新 option contracts 和 quotes。

Smoke test 中有 5 个 underlying 定义和 5 个 option templates。每个 template 会实例化到每个 underlying，因此数据库包含 25 个 option contracts，而不是总共 5 个合约。

Mutation + curriculum 本身不直接写 authoring DuckDB：`task_space` 只登记 snapshot
id/revision 和六维坐标，`mutation` 只产生 child task/lineage，`curriculum` 只计算
采样权重。联合 simulator 会扩展 authoring pipeline；若 mutation 改变市场状态、共享
利率路径、边际模型或联合依赖，仍须通过新的 authoring config/snapshot id 生成，再把
新 revision 注册到 child task。

## Underlying simulator 第一阶段

本节以
[`financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md`](financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
为设计基准。目标是把当前“逐 underlying 独立生成”的 smoke pipeline 扩展为同币种
underlying 生成器，同时保持确定性重放、事务写入、snapshot 不可变性与现有公开测试。

### 设计边界与不变量

- 本阶段只改变 `underlying_daily` 的 close path，且明确属于物理测度 $\mathbb P$。
- correlation matrix 的 driver 是按 `driver_order` 排列的 underlying spot processes，
  不是 option contract、Greek 或其他 derivative risk units。
- `option_daily_row()` 不接收 dependence spec，也不生成 correlated derivative shocks。
  Option quote 仍由当日可见 spot、原有风险中性参数和 QuantLib engine 计算；固定 spot
  与定价参数时，切换 underlying correlation 不得改变该 option quote。
- 本阶段仍保留每个 underlying 的现有 risk-free/dividend 和 pricing 配置；共同
  $\mathbb Q$、numeraire、共享利率路径及更复杂合法边际模型属于后续独立改造，不与
  本次 underlying path correlation 混装。
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

authoring schema 已升级到 `2.1.0`，并从 `2.0.0` 执行 additive migration：

| 对象 | 业务主键 | 当前内容 |
|:---|:---|:---|
| `market.underlying_dependence` | `(snapshot_id, dependence_spec_id)` | `measure=P`、`driver_order`、$\Lambda$、$D$、$R$、dtype、构造顺序、time grid、regime 和 authoring lineage。 |
| `market.pricing_metadata.physical_dynamics` | 现有主键 | 每个 underlying/date 保存 `dependence_spec_id`、`driver_id`、driver order 与 shock/RNG namespace。 |
| `metadata.snapshot_revisions` / manifest | 现有主键 | 新增 `underlying_dependence_count`。 |

`market.underlying_dependence` 是 private authoring DGP，不创建
`solver_visible.underlying_dependence` view，避免把 latent dependence 参数自动暴露给
Solver。Option tables 和 pricing schema 在本阶段没有增加 correlation 字段。

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

本阶段的 $R$ 只描述历史 underlying shocks 的联合分布，不是 derivative pricing 的
no-arbitrage 条件，也不用于构造 derivative quote correlation。Derivative no-arbitrage
仍应来源于后续选定的合法 pricing model、测度与 numeraire；本次改造不扩大现有 option
engine 的 no-arbitrage 声明。authoring gate 当前负责：

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
| Q1. Pricing context/model | 独立后续 | pricing/model registry | 共同 $\mathbb Q$/numeraire/rate path 和合法 coherent model；若多资产 payoff 需要相关性，相关对象仍是其 Q-measure underlying drivers，而不是 derivative contracts。 |

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

本阶段落实 README 的阶段 1，只改变 option contract construction，不改变现有
deterministic-smile BSM 报价公式，也不提前引入 common-$\mathbb Q$ pricing context。

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

Authoring schema `2.2.0` 从 `2.0.0/2.1.0` additive migration：

| 对象 | 作用 |
|:---|:---|
| `market.option_chain_specs` | 私有保存 chain grid type/value、listing/roll、strike rounding、合约约定与 lineage；无 solver view。 |
| `market.option_contracts` | 新增 nullable `chain_id, listing_date, listing_spot, strike_moneyness`；legacy 已有行不改写。 |
| `metadata.snapshot_revisions` / manifest | 新增 `option_chain_spec_count`。 |

一个 `1.3.0` snapshot 将 chain spec、underlying 集合和全部已挂牌 contracts 视为不可变
整体。one-shot、append 与 `sync-config` 每次重建相同 expected contract rows，再与数据库
逐字段比较；修改 grid、increment、initial spot、expiry、roll 或 stable ID 必须使用新
`snapshot_id`。Quality gates 另验证 chain contract 必须能关联私有 spec、listing 字段完整，
以及每条 option quote 的静态合约字段必须与 contract master 一致。

Correlation boundary 保持不变：`OptionChainBuilder` 的 Cartesian grid 不进入
`underlying_simulation.driver_order`，840 个 option contracts 也不会扩张 underlying 的
20×20 $R$。Option rows 仍只读取 realization spot、contract 和现有 pricing inputs。

## 文件

| 文件 | 用途 |
|:---|:---|
| `configs/generators/quantlib_bsm_smoke_v1.json` | 固定 seed、模型、underlyings、option templates 与 quote rules。 |
| `authoring/templates/quantlib_bsm_correlated_underlyings.template.json` | 可运行的 config `1.2.0` correlated-underlying 示例。 |
| `configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json` | config `1.3.0` 的 20-underlying、840-contract、10-day 规模测试。 |
| `snapshots/public/quantlib_bsm_smoke_v1.duckdb` | 可增量编辑的 `DRAFT` smoke snapshot。 |
| `snapshots/public/quantlib_bsm_smoke_v1.manifest.json` | 当前 logical revision、版本标识和行数。 |
| `scripts/edit_snapshot.py` | 仓库本地 `.venv` 使用的编辑入口。 |
| `src/synthetic_derivatives/authoring/generator_common.py` | 两个 generator 共享的 pinned QuantLib、calendar/day-count、decimal canonicalization 与 deterministic RNG。 |
| `src/synthetic_derivatives/authoring/underlying_daily_generator.py` | Underlying master/dependence、P path 与 pricing metadata；唯一消费 $\Lambda/D/R$ 的 generator。 |
| `src/synthetic_derivatives/authoring/option_daily_generator.py` | Option chain spec、frozen contracts 与 daily quotes；只接收 realized spot，不提供 underlying dependence API。 |
| `src/synthetic_derivatives/authoring/pipeline.py` | 显式编排两个 generator、DuckDB transaction、incremental MERGE 与 quality gates。 |
| `tests/public/test_authoring_smoke.py` | 初始规模、幂等、追加日期、增加品种和 freeze 测试。 |
| `tests/unit/test_underlying_simulator.py` | $\Lambda/D/R$、联合 shock、持久化、append invariance 与 derivative boundary 测试。 |
| `tests/unit/test_option_chain_builder.py` | chain config、完整网格、listing strike、不可变性及 append/sync tests。 |

## DuckDB schemas（当前实现）

数据库分为三个 SQL schemas。

### `market`

| 表 | 业务主键 | 说明 |
|:---|:---|:---|
| `market.underlyings` | `(snapshot_id, underlying_id)` | Underlying master 和 P/Q 参数入口。 |
| `market.underlying_dependence` | `(snapshot_id, dependence_spec_id)` | 私有的 P-measure underlying factor-loading 合同；不进入 solver views。 |
| `market.option_chain_specs` | `(snapshot_id, chain_id)` | 私有 option-chain listing/grid/roll/rounding 合同；不进入 solver views。 |
| `market.option_contracts` | `(snapshot_id, option_id)` | Option 合约静态字段及 listing provenance；绝对 strike 挂牌后冻结。 |
| `market.underlying_daily` | `(snapshot_id, date, underlying_id)` | Framework 要求的 underlying daily panel。 |
| `market.option_daily` | `(snapshot_id, date, option_id)` | Framework 要求的 option daily quotes。 |
| `market.pricing_metadata` | `(snapshot_id, valuation_timestamp, underlying_id)` | 每个 valuation slice 的 P/Q dynamics、curves、engine、seed、RNG 和 canonicalization。 |

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

这些 view 不暴露 authoring lineage 字段。生产部署时还应把 solver 和 authoring database 放到不同权限环境，按 task variant 导出冻结切片。

## QuantLib 生成方法（当前实现）

### Underlying path

每个 underlying 使用 `QuantLib.BlackScholesMertonProcess.evolve` 生成 P-measure
time-inhomogeneous GBM close：

\[
\frac{dS_t}{S_t}=\mu(t)dt+\sigma(t)dW_t.
\]

`physical_drift` 和 `physical_volatility` 既可以是向后兼容的 scalar，也可以是以
`start_date` 为原点的 `piecewise_linear` deterministic function。每个 close
interval 对 \(\mu(t)\) 精确积分并取算术平均，对 \(\sigma^2(t)\) 精确积分并取
root-mean-square；得到的 interval-equivalent 参数交给 QuantLib 的 exact GBM
transition。物理 drift/volatility function 与风险中性定价参数分开保存，完整函数
和当日有效参数写入 `pricing_metadata.physical_dynamics`。

随机流不是一个依赖循环顺序的全局 stream。v1/v1.1 使用以下 tuple 派生 32-bit seed：

```text
(snapshot_id, base_seed, purpose, entity_id, market_date)
```

config `1.2.0` 则按上文的 factor 与 idiosyncratic namespaces 派生 close shocks。两者均由
`QuantLib.MersenneTwisterUniformRng` 和
`QuantLib.BoxMullerMersenneTwisterGaussianRng` 产生 draw。相关结构只替换 close
transition 的 $Z_t$；range、volume 与 option activity 仍使用各自的稳定 streams。

### Option quotes

`1.3.0` 先由 `OptionChainBuilder` 生成 frozen contract grid；每个 valuation date 对这些
合约继续使用原有：

- `QuantLib.BlackScholesMertonProcess`
- flat continuous risk-free/dividend curves
- deterministic quadratic log-forward-moneyness smile
- `QuantLib.AnalyticEuropeanEngine`
- European call/put payoff

QuantLib NPV 先按 `ROUND_HALF_EVEN` 量化到 8 位小数，再生成 bid/ask/mid/settlement price。Latent volatility 不被当成下游 IV 答案；后续任务必须从 Solver 可见的已量化 mid 重新反解。

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

## 命令

所有命令都必须使用仓库内 `.venv`。

### 创建或幂等同步 smoke snapshot

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  create-smoke
```

再次运行返回 `NOOP`，revision 保持不变。

### 追加交易日

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  append-dates --days 1
```

### 增加 underlying 或 option

在 config 的 `underlyings` 或 `option_templates` 数组末尾添加新定义，然后执行：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
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

### 运行 20 品种 option-chain smoke

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-option-chain-smoke.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  create-smoke
```

期望规模为 20 个 underlyings、840 个 option contracts、10 个 business dates、200 条
underlying daily、8,400 条 option daily 与 200 条 pricing metadata。这个 smoke 只验证
规模、完整 chain、确定性和基础 pricing sanity，不代表真实金属交易所 calendar/carry/
settlement profile。验收配置将现有 smile 的 skew/curvature/term-slope 设为 0，因此每个
underlying 的完整 grid 使用同一个 constant-vol BSM marginal model；报价实现本身未改。

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
- 固定 spot 与 pricing inputs 时，underlying correlation 不影响 option quote。
- config `1.3.0` 展开 3 expiries × 7 strikes × paired call/put，listing strike 跨日固定；
- chain one-shot/append/`sync-config` invariant，修改 chain spec 或 listed contract 被拒绝；
- 20 品种 smoke 的 840 contracts 与 8,400 quotes 完整生成。

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
