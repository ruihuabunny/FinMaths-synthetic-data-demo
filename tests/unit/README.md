# Unit tests

本目录验证确定性 authoring、Solver 数值内核、task mutation 和 curriculum 的内部不变量。
Unit tests 可以直接导入具体实现类、调用非公开 helper，并检查 private DuckDB tables；
它们不代表 Solver 可访问的数据边界。

从仓库根目录运行：

```bash
.venv/bin/pytest -q tests/unit
```

运行单个模块或测试：

```bash
.venv/bin/pytest -q tests/unit/test_option_chain_builder.py
.venv/bin/pytest -q \
  tests/unit/test_underlying_simulator.py::test_option_pricing_does_not_consume_underlying_correlation
```

## 文件与覆盖范围

| 文件 | 主要覆盖 |
|:---|:---|
| [`test_authoring_config.py`](test_authoring_config.py) | Deterministic piecewise-linear drift/volatility 的插值、精确区间缩约、flat extrapolation、schema version 和 positive-volatility validation。 |
| [`test_underlying_simulator.py`](test_underlying_simulator.py) | $\Lambda/D/R$ 规范派生、factor/idiosyncratic shock、published-close rounded restart state、private dependence persistence、schema migration、append invariance、snapshot immutability，以及 option generator 不消费 underlying correlation。 |
| [`test_joint_dependence.py`](test_joint_dependence.py) | Config 1.7 的 P/Q measure-qualified identities、drift-only covariance mapping、invalid driver/loading rejection、safe solver projection、common-Q gate、marginal BSM price/Greeks invariance 与 frozen byte immutability。 |
| [`test_solver_database_export.py`](test_solver_database_export.py) | SHA-256 rank 无放回采样、stable sample/task IDs、显式 selector canonicalization，以及 nested JSON recursive leakage rejection。 |
| [`test_bsm_greeks_contract.py`](test_bsm_greeks_contract.py) | $\mathbb Q$/numeraire、BSM 输入域、Greek holding-fixed/scaling、binary64-to-decimal checkpoint、variant/schema identities 与无公开中间量合同。 |
| [`test_bsm_solver.py`](test_bsm_solver.py) | [`analytic_and_implied_greeks_iv`](../../src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/README.md) 的 stdlib-only import boundary、call/put bounds、put-call parity、Delta relation、Gamma/Vega positivity、短长 maturity、低高 volatility 与 canonical row order。 |
| [`test_bsm_implied_volatility.py`](test_bsm_implied_volatility.py) | 同一 Solver 包的 Decimal-first visible midpoint、贴现 price domain、固定 `[1e-6,5.0]` bracket、恰好 80 次 bisection、四种 canonical status、无 fallback 与 task-only config boundary。 |
| [`test_option_chain_builder.py`](test_option_chain_builder.py) | Expiry × grid × call/put 展开、liquidity filtering、quote-noise replay、moneyness/absolute-strike 互斥、listing strike 冻结、stable contract ID、append/`sync-config` invariance 和 22-underlying public config 结构。 |
| [`test_task_space.py`](test_task_space.py) | 七维 task coordinates、F enum/schema 一致性、显式旧六维迁移、registry compatibility 和 documented axes。 |
| [`test_model_family_registry.py`](test_model_family_registry.py) | `tdgbm_bsm` stochastic identity、`M=0` 派生、closed schema、duplicate/unknown family 与动态 import-path 拒绝。 |
| [`test_executable_capability_registry.py`](test_executable_capability_registry.py) | Exact five-field capability key、status/evidence gate、static/query interface 分离和 MC/non-BSM fail-closed。 |
| [`test_task_v3_identity.py`](test_task_v3_identity.py) | Semantic TaskSpec v3 的四类正交身份、closed serialization、legacy adapter 与 family/`M` 一致性。 |
| [`test_authoring_backend_registry.py`](test_authoring_backend_registry.py) | `TDGBMBSMAuthoringBackend` 显式 dispatch、duplicate/unknown rejection 和写 artifact 前失败。 |
| [`test_mutation.py`](test_mutation.py) | Deterministic single-axis mutation、snapshot lineage、incompatible child rejection 和 method identity。 |
| [`test_family_mutation_guards.py`](test_family_mutation_guards.py) | Family/`M` immutability、capability-backed semantic changes，以及 kind/method/interface/output/snapshot 完整 lineage。 |
| [`test_curriculum.py`](test_curriculum.py) | 20/60/20 stage mass、mastery-adaptive sampling，以及 diagnostics 不改变 binary reward。 |
| [`test_family_curriculum.py`](test_family_curriculum.py) | Capability gate 先于 family-local stage mapping、portable-only pool 和不变的 20/60/20/mastery semantics。 |
| [`test_hy3_chat_runner.py`](test_hy3_chat_runner.py) | Hy3 Chat Completions nested function-tool shape、普通文本拒绝、受限 solver replay、capability denial、tool-error repair round 与 trusted exact verification。 |

Golden package 的 trusted-adapter runtime、reference replay、QuantLib verifier、release views、
negative submissions 以及 BSM-specific nine-field dataset export 属于跨边界行为，统一在
`tests/packaging_analytic_and_implied_greeks_iv/` 覆盖，不在 unit suite 重复构造完整 package。
`test_hy3_chat_runner.py` 只用 fake API response 验证本地 orchestration；它不会发真实网络请求。

## Authoring unit-test 边界

### Underlying 与 option 必须分离

[`UnderlyingDailyGenerator`](../../src/synthetic_derivatives/authoring/underlying_daily_generator.py)
是唯一允许消费 P spec、$\Lambda/D/R$ 和 P-measure shock streams 的 generator；它会
持久化 Q spec，但 Q spec 不参与 historical path transition。
[`OptionDailyGenerator`](../../src/synthetic_derivatives/authoring/option_daily_generator.py)
只能从 realized spot、frozen contract 和 pricing inputs 生成报价，不应暴露
`underlying_close_shock` 或 `underlying_daily_row`。

相关测试必须区分两种陈述：

1. 改变 P-measure correlation 可以改变生成的 joint underlying paths；
2. 固定 realized spot 与 option pricing inputs 后，在 identity 与合法 non-diagonal P/Q
   dependence 之间切换不得改变 vanilla option surface 或 analytic Greeks。

### Incremental invariance

固定 config/seed 时，one-shot 与 incremental append 必须产生完全相同的业务行。测试比较
规范排序后的 logical rows，而不是 DuckDB 文件字节；数据库文件包含 run UUID、timestamp
和物理布局，不能用文件 hash 代表市场结果相等。这里的 restart state 是前一期量化后的
published close；测试锁定的是 rounded-state Markov law，不是隐藏 continuous state。

### Snapshot immutability

以下修改必须被拒绝或要求新的 `snapshot_id`：

- 已生成 path 的 driver order、factor loading 或 regime；
- 已声明 Q mapping 的 source spec、mapping ID、共同 Q/numeraire/rate-path identity；
- 已挂牌 option chain 的 grid、roll/listing rule、strike increment；
- stable contract ID 下的 listing spot、absolute strike、expiry 或合约约定；
- 已存在 underlying path 的历史中间缺口。

Legacy config 的明确 additive 行为应单独测试，不能无意套用到 immutable
`underlying_simulation` 或 option-chain contract。

## Pytest fixtures 与临时数据

共享 fixtures 定义在 [`tests/conftest.py`](../conftest.py)：

- `repository_root`
- `smoke_config_path`
- task-space、mutation、curriculum config paths
- base task manifest path

需要写配置或 DuckDB 的测试应使用 `tmp_path`。Pytest 会管理这些临时目录；不要把 unit
test 生成的数据库写入 `snapshots/public`。`pytest` 的 pass/fail 日志本身不会自动写进
DuckDB。

## 新增测试的约定

- 测试名描述业务不变量，而不是实现步骤。
- 固定 seed/日期/排序；不要依赖测试执行顺序或全局 RNG state。
- 数学构造尽量用小矩阵和可手算参数，并明确使用 `pytest.approx` 还是 exact equality。
- DuckDB 查询必须显式限定 `snapshot_id`，多行比较必须固定 `ORDER BY`。
- Failure test 同时验证 transaction rollback 或 snapshot revision 未变化。
- 若修改 solver-visible contract，还必须在 `tests/public` 增加相应 public schema test。

## 完成检查

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/pytest -q tests/unit
git diff --check
```
