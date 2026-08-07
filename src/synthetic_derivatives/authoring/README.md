# Authoring package

本目录实现 trusted authoring boundary：读取版本化 generator config，通过 pinned
QuantLib 生成确定性市场数据，并用事务方式写入可增量编辑、最终可冻结的 DuckDB
snapshot。Solver 不应直接导入本包，也不能访问其中的 private generation provenance。

完整项目设计见 [项目 README](../../../README.md) 和
[Authoring Pipeline](../../../docs/authoring_pipeline.md)。

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

修改 generator 时至少应保证：

1. 固定 config/seed 的 solver-visible rows 不变，除非变更本身明确版本化；
2. one-shot 与 append 结果一致；
3. option generator 不获得 underlying dependence/path-transition API；
4. `git diff --check` 和全量 tests 通过。
5. 旧 v3/带 authoring-IV solver 的 config 只能读取；任何新 materialization 使用 config
   `1.6.0` 与新的 config/generator/snapshot identity。

## 扩展规则

- 新增 latent path state 前，先设计可持久化的完整 Markov restart state。
- 新增动态 option listing/roll 前，先版本化 exchange calendar、series identity 和 roll
  state；不要根据 daily spot 隐式重建历史合约。
- 新增 Q-measure multi-asset dependence 时，driver 仍是 underlying/model drivers，不能是
  derivative contracts。
- 修改已挂牌合约、历史 path 或 immutable dependence 时，必须使用新的 `snapshot_id`，
  不能在原 snapshot 内覆盖。
