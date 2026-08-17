# Authoring package

本目录实现 trusted authoring boundary：读取版本化 generator config，通过 pinned
QuantLib 生成确定性市场数据，并用事务方式写入可增量编辑、最终可冻结的 DuckDB
snapshot。Solver 不应直接导入本包，也不能访问其中的 private generation provenance。

完整项目设计见 [项目 README](../../../README.md) 和
[Authoring Pipeline](../../../docs/authoring_pipeline.md)。

## 模块职责

| 模块 | 职责 |
|:---|:---|
| [`backends.py`](backends.py) | 将已实现的 `tdgbm_bsm` family 显式解析为现有 underlying/option generators；未知 family 在创建 artifact 前失败。 |
| [`config.py`](config.py) | 解析 generator config；校验 deterministic physical functions、underlying dependence、option-chain grid 和稳定 ID；规范派生 $D$ 与 $R$。 |
| [`generator_common.py`](generator_common.py) | 两个 generator 共享的 pinned QuantLib 检查、calendar/day-count、日期转换、8 位 decimal canonicalization，以及 namespaced deterministic RNG。 |
| [`underlying_daily_generator.py`](underlying_daily_generator.py) | 生成 underlying master、P/Q measure-qualified dependence、`underlying_daily` 和 `pricing_metadata`；只有 P spec 可生成 path shock。 |
| [`option_daily_generator.py`](option_daily_generator.py) | 生成 private option-chain spec、冻结的 option contracts 和 Q-measure `option_daily`；不提供 underlying path/dependence API，也不生成 IV answers。 |
| [`pipeline.py`](pipeline.py) | 通过 backend registry 编排两个 generator、DuckDB transaction、incremental MERGE、snapshot compatibility、quality gates、revision 和 manifest。 |
| [`schema.py`](schema.py) | DuckDB DDL、additive migration、solver-visible views、table column order 和 business-key MERGE。 |
| [`cli.py`](cli.py) | `create-smoke`、`append-dates`、`sync-config`、`sync-range`、`summary` 和 `freeze` 命令入口。 |
| [`__init__.py`](__init__.py) | 导出 `AuthoringPipeline` 与显式 authoring backend registry 类型。 |

原来的单体 `generator.py` 已拆除。Product-specific generator 只共享基础设施，不互相
调用，避免 option quote 生成路径意外读取 P-measure correlation contract。

## Model-family dispatch

`AuthoringPipeline` 的兼容默认值是当前唯一实现的 `tdgbm_bsm`。构造 pipeline 时会先通过
不可变 backend registry 解析 family、校验 generator config 中的 BSM process/engine
identity，再创建数据库目录或 generator；未知 family、重复 backend 或不匹配的 config
因此在 artifact 写入前 fail closed。

这个 adapter 不改变现有 generator 文件、随机流、行生成顺序或 canonicalization。
Family 的完整 P/Q、numeraire、day-count、state、transition、dtype 与 RNG identity 由
[`model-family config`](../../../configs/model_families/tdgbm_bsm_v1.json) 声明；backend 只负责
把该 identity 显式绑定到已经存在的实现。新增第二个 family 必须提供自己的完整 state 与
数值闭环，不能把新参数塞进当前 BSM backend。

## 生成顺序

一次 `sync_range` 在同一个 DuckDB transaction 内执行：

```text
validate DRAFT snapshot and immutable config
  -> UnderlyingDailyGenerator
       -> underlying master + measure-qualified dependence specs
       -> realized P-measure underlying paths
       -> per-underlying/date pricing metadata
  -> OptionDailyGenerator
       -> private option-chain spec + frozen contracts
       -> Q option quotes from realized spot + frozen contract + pricing inputs
  -> MERGE by stable business keys
  -> cross-table quality gates
  -> revision + manifest
  -> commit
```

run 开始后的任一步失败都会 rollback 市场数据批次，并在
`metadata.generation_runs` 留下 `FAILED` 记录；immutable/frozen preflight failure
不会改写数据库。重复运行相同范围应返回 `NOOP`，不增加 revision。

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

$S_t>0$ 表示 USD/underlying-unit 的 synthetic ex-dividend spot；它不是 total-return index。
`physical_drift` 是 $\mathbb P$ 下年化瞬时期望价格收益率（year$^{-1}$），
`physical_volatility` 是年化瞬时收益标准差（year$^{-1/2}$），时间轴为 calendar time，
day-count 为 Actual/365 Fixed。当前 `adjusted_close = close`、`dividend = 0`、
`corporate_action = none` 是独立 observation rules，不是 GBM 自动生成的 cash distributions。

在 $\mathcal F_{t_i}$ 已知当前全体 published closes、确定性 functions、日期与冻结 P spec。
下一日期 namespace 的 factor/idiosyncratic normal shocks 与过去 disjoint intervals 独立，
同一日期按 $\Lambda/D/R$ 联合。当前 deterministic-time model 的 Markov state 因而是 published
close vector；stochastic volatility/rates/regimes 必须先扩展 persisted state 才能接入。

Config `1.7.0` 把这一合同扩展为严格有序的 P/Q specs。两者使用独立
`dependence_spec_id`；Q spec 记录 source P spec、mapping ID、共同 Q/numeraire/rate-path
IDs，并在当前 `girsanov_drift_only_same_brownian_covariance` baseline 下显式重复且严格
匹配 P 的 `driver_order/Λ/D/R/dtype/factor order/time grid/regime`。这来自等价测度下
drift shift 不改变 Brownian quadratic covariation，不是可推广到 stochastic-vol/hybrid
模型的默认规则。Historical path generation 始终只消费 P spec。

`start_date` materialize 的是 $S(t_0)=S_0$ initial condition，不从虚构前一日抽 shock。
当前 checked-in configs 的 `initial_spot` 都与 underlying tick 对齐，所以 canonicalization
不改变 $S_0$。Parser 目前只校验正值，未单独拒绝非 tick-aligned 自定义 `initial_spot`；这种
输入会先被 `ROUND_HALF_EVEN` 后才写入 initial row，不能声称 materialized state 等于原始值。
之后每个 actual-calendar interval 对 piecewise-linear $\mu_P(t)$ 精确积分取平均，并对
$\sigma_P^2(t)$ 精确积分取 RMS；这两个 flat-equivalent coefficients 使 QuantLib GBM
从当前 published state 到未量化 proposal 的 transition 与 deterministic
time-inhomogeneous GBM 在 observation endpoint 上同分布。Proposal 随后按配置的
underlying minimum price increment 做 `ROUND_HALF_EVEN`，量化后的 published close
就是下一期 restart state；所以 materialized path 的准确合同是 rounded-state Markov chain，
不是保留隐藏未舍入状态的 continuous-state GBM。这个量化 checkpoint 是 one-shot/append
一致性的一部分，改变它必须产生新的 snapshot identity。

当前 OHLC 采用显式 no-gap convention：`open = previous published close`。`high/low` 来自
独立 `separate_synthetic_range-v1` heuristic shock，只保证必要的价格顺序与正值，不是同一
intraperiod diffusion path 或 bridge/range law。Volume 来自单独的 deterministic uniform
stream；这些字段都不是 spot GBM 自身的输出。

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
Config `1.6.0+` 只生成 canonical market quotes，不在 authoring market-data contract 中
求解或保存 IV answers；IV inversion 由独立的 task contract、stdlib solver 和 trusted
QuantLib verifier 从公开 bid/ask midpoint 重新执行。

### No-arbitrage

Correlation matrix 不是 no-arbitrage 条件。Single-asset option consistency 来自所选合法
pricing model、风险中性测度和 discounting convention；pipeline quality gates 只负责发现
实现错误和 cross-table 不一致。Config `1.7.0` 已冻结 single-asset vanilla margins 的共同
$\mathbb Q$/numeraire/rate-path identity 与 Q-measure underlying-driver dependence；basket、
spread 等 joint payoff pricing 仍是独立后续阶段。

## Config 与 schema 版本

| Generator config | 主要能力 |
|:---|:---|
| `1.0.0` | Scalar physical parameters 与 legacy option templates。 |
| `1.1.0` | 增加 deterministic piecewise-linear drift/volatility functions。 |
| `1.2.0` | 增加 immutable P-measure `underlying_simulation`。 |
| `1.3.0` | 增加 static `option_chain`，替代逐条 `option_templates`。 |
| `1.4.0` | 增加 candidate-grid liquidity filter 与 deterministic bid/ask spread noise。 |
| `1.5.0` | 增加 per-underlying sampled-and-frozen physical functions（含 seeds/hard bounds）、显式 Q mapping 与 canonical-mid QuantLib IV audit。 |
| `1.6.0` | 从 authoring contract 移除 IV solver/answers，并分离 underlying/option minimum increments。 |
| `1.7.0` | 增加独立 P/Q dependence identities、drift-only covariance mapping 与共同 Q context。 |

当前 authoring schema 为 `2.5.0`，支持 mutable `2.0.0`--`2.4.0` migration；冻结库只读
打开，绝不原地迁移。
`market.option_chain_specs` 同时保存 liquidity filter 和完整 quote model。Private
authoring tables 包括：

- `market.underlying_dependence`
- `market.option_chain_specs`
- `market.option_pricing_audit`（只为 legacy `1.5.0` frozen snapshot 读取保留；当前
  `1.6.0+` materialization 不写入）
- 带 run lineage 的 market/master tables
- `metadata.snapshots`、`generation_runs`、`snapshot_revisions`

Solver 只应读取：

- `solver_visible.underlying_daily`
- `solver_visible.option_daily`
- `solver_visible.pricing_metadata`
- `solver_visible.underlying_dependence`（仅完整 P/Q pair；不含 seed/run/calibration provenance）

注意：当前 `solver_visible.pricing_metadata` 仍是 smoke 阶段的过渡合同，public/private
metadata split 尚未在 authoring 文件内部完成。最终 Solver 不应打开该文件；
[`synthetic_derivatives.export`](../export/README.md) 会把 frozen parent 投影为独立、经过
recursive leakage scan 的 public-only child，并只以 read-only mode 交付。

## Agent task packaging handoff

`synthetic_derivatives.packaging_analytic_and_implied_greeks_iv` 消费单一 `FROZEN`、config `1.7.0` P/Q parent，不回写或
原地迁移它。当前 golden selector 先生成 generic 8-underlying public child，再将任务输入
物化为只含 `metadata.public_task`、`solver_visible.underlying_market_inputs` 和
`solver_visible.option_quote_inputs` 的独立 D4 DuckDB。P/Q joint-market identities 保留在
public provenance 中，factor matrices 与 seed/private lineage 不进入 Agent-visible rows。

Packaging 随后冻结 prompt、effective runtime、submission schema、hidden QuantLib verifier、
stdlib reference solver 和 observable trajectory，并导出严格 allowlisted 的 authoring、
train/dev、evaluation views。Evaluation view 物理上只含 manifest、prompt、runtime contract
与 submission schema；raw DB 由 trusted host 持有。Authoring private artifact manifest 校验
全部源制品 hash。当前仓库维护一个 100-task combined v2 portable delivery，以及 static v2
和 DuckDB-query v3 两个各 24-task 的 6×4 metric suites；它们均为冻结制品，不由 authoring
pipeline 原地更新。新 metric-suite materialization 先通过 `tdgbm_bsm`
executable-capability preflight，但 accepted artifact 的 replay/verification 仍只依赖其冻结
manifest/runtime/verifier。详见
[`task_packages/README.md`](../../../task_packages/README.md)。

## 使用方式

从仓库根目录运行，始终使用项目 `.venv`：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/authoring-smoke.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  create-smoke
```

追加日期：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/authoring-smoke.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  append-dates --days 1
```

查看或冻结：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/authoring-smoke.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  summary

.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/authoring-smoke.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  freeze
```

Checked-in historical public metals profile 使用
[`quantlib_bsm_metals_option_chain_smoke_v1.json`](../../../configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)，
生成 22 个 underlying、65 个交易日、1,232 个流动性固定合约和 60,368 条有效期内
option quotes。候选网格是 6 expiries × 11 moneyness × call/put，filter 只保留
30/60/90/180 天与 0.85–1.15 moneyness，因此每个 underlying 实际挂牌 56 个合约。

## 测试

```bash
.venv/bin/pytest -q
```

重点测试：

- [`test_underlying_simulator.py`](../../../tests/unit/test_underlying_simulator.py)：
  $\Lambda/D/R$、stream partition、append invariance、private persistence 和 derivative
  boundary。
- [`test_joint_dependence.py`](../../../tests/unit/test_joint_dependence.py)：
  P/Q mapping、拒绝面、safe projection、common-Q gate、边际 price/Greeks invariance 和
  frozen byte immutability。
- [`test_option_chain_builder.py`](../../../tests/unit/test_option_chain_builder.py)：
  grid expansion、liquidity filtering、quote-noise replay、listing strike、stable contract
  identity、append/`sync-config` 和 chain immutability。
- [`test_authoring_backend_registry.py`](../../../tests/unit/test_authoring_backend_registry.py)：
  explicit dispatch、duplicate/unknown family rejection 和写入前失败。
- [`test_tdgbm_bsm_backend_equivalence.py`](../../../tests/integration/test_tdgbm_bsm_backend_equivalence.py)：
  backend path 与原 direct-constructor path 的 solver-visible rows、authoring rows 和 logical
  checksum 等价。
- [`test_authoring_smoke.py`](../../../tests/public/test_authoring_smoke.py)：
  transaction、NOOP、append、legacy additive config 与 freeze。

修改 generator 时至少应保证：

1. 固定 config/seed 的 solver-visible rows 不变，除非变更本身明确版本化；
2. one-shot 与 append 结果一致；
3. option generator 不获得 underlying dependence/path-transition API；
4. `git diff --check` 和全量 tests 通过。

## 扩展规则

- 新增 latent path state 前，先设计可持久化的完整 Markov restart state。
- 新增动态 option listing/roll 前，先版本化 exchange calendar、series identity 和 roll
  state；不要根据 daily spot 隐式重建历史合约。
- 扩展当前 Q-measure dependence 到 stochastic-vol/hybrid 模型时，必须显式版本化测度映射
  和完整 driver blocks，不能沿用 drift-only covariance mapping，也不能加入 derivatives。
- 修改已挂牌合约、历史 path 或 immutable dependence 时，必须使用新的 `snapshot_id`，
  不能在原 snapshot 内覆盖。
