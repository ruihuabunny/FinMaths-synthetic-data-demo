# DuckDB + QuantLib Authoring Pipeline

## 目标与范围

第一版 authoring pipeline 将 framework 的三表 snapshot 合同落实到一个 DuckDB 文件中，并支持三类增量操作：

1. 在已有 5 天数据后追加第 6 天，只生成并插入第 6 天的数据；
2. 增加 underlying，在当前日期区间内只为新 underlying 生成路径、metadata 和 options；
3. 增加 option template，在当前日期区间内只生成新 option contracts 和 quotes。

Smoke test 中有 5 个 underlying 定义和 5 个 option templates。每个 template 会实例化到每个 underlying，因此数据库包含 25 个 option contracts，而不是总共 5 个合约。

Mutation + curriculum 扩展不改变这条 pipeline：`task_space` 只登记 snapshot id/hash 和六维坐标，`mutation` 只产生 child task/lineage，`curriculum` 只计算采样权重。三个模块都不写 authoring DuckDB；若 mutation 需要新的市场状态，仍须通过新的 authoring config/snapshot id 生成，再把新 hash 注册到 child task。

## 文件

| 文件 | 用途 |
|:---|:---|
| `configs/generators/quantlib_bsm_smoke_v1.json` | 固定 seed、模型、underlyings、option templates 与 quote rules。 |
| `snapshots/public/quantlib_bsm_smoke_v1.duckdb` | 可增量编辑的 `DRAFT` smoke snapshot。 |
| `snapshots/public/quantlib_bsm_smoke_v1.manifest.json` | 当前 logical revision、行数和 content hash。 |
| `scripts/edit_snapshot.py` | 仓库本地 `.venv` 使用的编辑入口。 |
| `src/synthetic_derivatives/authoring/` | Config、QuantLib generator、DuckDB schema、transactional pipeline 与 CLI。 |
| `tests/public/test_authoring_smoke.py` | 初始规模、幂等、追加日期、增加品种和 freeze 测试。 |

## DuckDB schemas

数据库分为三个 SQL schemas。

### `market`

| 表 | 业务主键 | 说明 |
|:---|:---|:---|
| `market.underlyings` | `(snapshot_id, underlying_id)` | Underlying master 和 P/Q 参数入口。 |
| `market.option_contracts` | `(snapshot_id, option_id)` | Option 合约静态字段；`option_id` 由 underlying 与 template 稳定派生。 |
| `market.underlying_daily` | `(snapshot_id, date, underlying_id)` | Framework 要求的 underlying daily panel。 |
| `market.option_daily` | `(snapshot_id, date, option_id)` | Framework 要求的 option daily quotes。 |
| `market.pricing_metadata` | `(snapshot_id, valuation_timestamp, underlying_id)` | 每个 valuation slice 的 P/Q dynamics、curves、engine、seed、RNG 和 canonicalization。 |

三张 daily/metadata 表包含 framework 指定的全部字段。内部额外保存 `row_sha256` 和 `generated_run_id`，用于幂等 merge、lineage 和审计。

### `metadata`

| 表 | 说明 |
|:---|:---|
| `metadata.schema_migrations` | DuckDB schema version 与 DDL hash。 |
| `metadata.snapshots` | Snapshot 状态、当前 revision、依赖版本与 logical content hash。 |
| `metadata.generation_runs` | 每次增量操作的范围、状态、逐表 inserted/updated/unchanged 统计和失败信息。 |
| `metadata.snapshot_revisions` | 每次产生逻辑变更后的行数、config hash 与 content hash。 |

### `solver_visible`

只暴露 framework 合同字段：

- `solver_visible.underlying_daily`
- `solver_visible.option_daily`
- `solver_visible.pricing_metadata`

这些 view 不暴露 authoring lineage 字段。生产部署时还应把 solver 和 authoring database 放到不同权限环境，按 task variant 导出冻结切片。

## QuantLib 生成方法

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

随机流不是一个依赖循环顺序的全局 stream，而是使用以下 tuple 派生 32-bit seed：

```text
(snapshot_id, base_seed, purpose, entity_id, market_date)
```

派生后由 `QuantLib.MersenneTwisterUniformRng` 和 `QuantLib.BoxMullerMersenneTwisterGaussianRng` 产生 draw。这样新增 underlying/option 不会改变已有实体的随机数或历史路径。

### Option quotes

每个 valuation date 使用：

- `QuantLib.BlackScholesMertonProcess`
- flat continuous risk-free/dividend curves
- deterministic quadratic log-forward-moneyness smile
- `QuantLib.AnalyticEuropeanEngine`
- European call/put payoff

QuantLib NPV 先按 `ROUND_HALF_EVEN` 量化到 8 位小数，再生成 bid/ask/mid/settlement price。Latent volatility 不被当成下游 IV 答案；后续任务必须从 Solver 可见的已量化 mid 重新反解。

## 增量写入语义

每次运行使用一个 DuckDB transaction：

```text
validate DRAFT/config
  -> create generation run
  -> load incremental rows into temporary staging tables
  -> MERGE by stable business primary key
  -> skip rows with identical row_sha256
  -> run cross-table quality gates
  -> record revision/content hash
  -> commit
```

任一生成、constraint 或 quality-gate 错误会 rollback 整个数据批次，并记录 `FAILED` run。内容 hash 基于五张 market 表按业务主键排序后的 logical row hashes，不依赖 DuckDB 二进制文件布局或运行时间戳。

已有 entity definition 和历史路径是不可变的：

- 配置可以追加 underlyings 或 option templates；
- 配置不能删除或原地修改已存在的定义；
- 不能回填 underlying path 中间的日期缺口，因为这会使后续路径失效；
- 需要修正历史或模型参数时，应使用新的 `snapshot_id`。

## 命令

所有命令都必须使用仓库内 `.venv`。

### 创建或幂等同步 smoke snapshot

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  create-smoke
```

再次运行返回 `NOOP`，revision 和 logical content hash 均不变。

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

## Smoke-test 验收

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
- frozen snapshot 拒绝增量写入。

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
