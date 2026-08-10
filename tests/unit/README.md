# Unit tests

本目录验证确定性 authoring、task mutation 和 curriculum 的内部不变量。Unit tests 可以
直接导入具体实现类、调用非公开 helper，并检查 private DuckDB tables；它们不代表 Solver
可访问的数据边界。

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
| [`test_underlying_simulator.py`](test_underlying_simulator.py) | $\Lambda/D/R$ 规范派生、factor/idiosyncratic shock、private dependence persistence、schema migration、append invariance、snapshot immutability，以及 option generator 不消费 underlying correlation。 |
| [`test_option_chain_builder.py`](test_option_chain_builder.py) | Expiry × grid × call/put 展开、liquidity filtering、quote-noise replay、moneyness/absolute-strike 互斥、listing strike 冻结、stable contract ID、append/`sync-config` invariance 和 22-underlying public config 结构。 |
| [`test_q_pricing.py`](test_q_pricing.py) | Config 1.5/1.6 边界、legacy authoring-IV config 只读、P/Q metadata、integrated-variance BSM quote、tick quantization 和不生成 IV answers。 |
| [`test_f2a_repo_contracts.py`](test_f2a_repo_contracts.py) | F2A legacy/v2/v3 identity mapping、complete-grammar point counts、distribution/single-parent split、`g_j` target、lineage `000/111` 与 operator/signature conditionals、public no-label-leakage。 |
| [`test_f2a_math_contracts.py`](test_f2a_math_contracts.py) | Candidate-specific open/closed boundary、`g_j`/`W_T2` regression、`A_S/B_S` partition cashflow identities、single-expiry piecewise-affine cells/knots/tails、gcd normalization、multiplier-safe scanner 与 operator response algebra。 |
| [`test_f2a_calendar_v4.py`](test_f2a_calendar_v4.py) | 42-candidate calendar enumeration、`beta/Delta_0/Delta_1`、unsimplified/piecewise ledgers、boundary/ray/open-set certificate、fee/cost accounting 与 raw-order/model-mismatch/F<1/multiplier/nonfinite negative controls。 |
| [`test_f2a_repo_v4.py`](test_f2a_repo_v4.py) | V4 executable identities、non-null calendar config、runtime files、tracked complete-chain fixture/manifest、reachability artifact 与 jsonschema/referencing dependency lock。 |
| [`../integration/test_f2a_v4_runtime.py`](../integration/test_f2a_v4_runtime.py) | 真实 quote mutation 的 `000..111` tick windows、exact `001`/mixed signatures、atomic child、candidate-specific lineage rejection、independent Solver/verifier smoke。 |
| [`test_f2a_v5_stage1.py`](test_f2a_v5_stage1.py) | 三节点 P-measure estimator 的 hat basis、精确 drift/variance interval integrals、weekend/node crossing、Schur covariance、RSE 与 horizon support。 |
| [`test_f2a_v5_stage2_inversion.py`](test_f2a_v5_stage2_inversion.py) | Call/put 80-step BSM inversion、discounted bounds、`invalid_bracket`、market/linked `d1/d2` 派生、half-even checkpoint 及 Solver/verifier exact wrapper equality。 |
| [`test_f2a_v5_stage2_signal.py`](test_f2a_v5_stage2_signal.py) | Linked-price gradient、full covariance uncertainty、post-cost X/U/T activation，以及 market-IV repricing 不得替代 linked counterfactual。 |
| [`test_f2a_v5_contracts.py`](test_f2a_v5_contracts.py) | V5.1 config/schema/output identity、invalid-IV row exclusion、public node-value leakage、task-seed cohort gate、semantic-layer perturbation 和 Solver/verifier independence。 |
| [`test_task_space.py`](test_task_space.py) | 六维 task coordinates、registry compatibility 和 documented axes。 |
| [`test_mutation.py`](test_mutation.py) | Deterministic single-axis mutation、snapshot lineage、incompatible child rejection 和 method identity。 |
| [`test_curriculum.py`](test_curriculum.py) | 20/60/20 stage mass、mastery-adaptive sampling，以及 diagnostics 不改变 binary reward。 |

## Authoring unit-test 边界

### Underlying 与 option 必须分离

[`UnderlyingDailyGenerator`](../../src/synthetic_derivatives/authoring/underlying_daily_generator.py)
是唯一允许消费 `underlying_simulation`、$\Lambda/D/R$ 和 P-measure shock streams 的
generator。
[`OptionDailyGenerator`](../../src/synthetic_derivatives/authoring/option_daily_generator.py)
只能从 realized spot、frozen contract 和 pricing inputs 生成报价，不应暴露
`underlying_close_shock` 或 `underlying_daily_row`。

相关测试必须区分两种陈述：

1. 改变 P-measure correlation 可以改变生成的 joint underlying paths；
2. 固定 realized spot 与 option pricing inputs 后，改变 P-measure correlation 不得改变
   option quote。

### Incremental invariance

固定 config/seed 时，one-shot 与 incremental append 必须产生完全相同的业务行。测试比较
规范排序后的 logical rows，而不是 DuckDB 文件字节；数据库文件包含 run UUID、timestamp
和物理布局，不能用文件 hash 代表市场结果相等。

### Snapshot immutability

以下修改必须被拒绝或要求新的 `snapshot_id`：

- 已生成 path 的 driver order、factor loading 或 regime；
- 已挂牌 option chain 的 grid、roll/listing rule、strike increment；
- stable contract ID 下的 listing spot、absolute strike、expiry 或合约约定；
- 已存在 underlying path 的历史中间缺口。

Legacy config 的明确 additive 行为应单独测试，不能无意套用到 immutable
`underlying_simulation` 或 option-chain contract。

### Config 1.5 / 1.6 与 IV 边界

Config `1.5.0` 只为读取已有 snapshot identity 保留。若其中存在
`q_pricing.implied_volatility_solver`，当前 pipeline 必须拒绝 create/append/freeze，避免在同一
identity 下混合有、无 IV audit 的两种 output contract。Config `1.6.0` 保留共同 Q pricing
context 和 deterministic diffusion mapping，但生成结果只到 canonical option quote；测试不得
期待 `market.option_pricing_audit` 新增行。

Canonical IV method/answer 属于 task variant 与 trusted verifier。测试 authoring 时只能断言
公开 pricing inputs、quote law、absence of answer leakage 和 legacy table row count，不得把 hidden
pricing volatility 重新包装成 expected IV。

V5.1 是独立 task contract：每条 visible midpoint 使用 bracket `[1e-6,5.0]` 和恰好 80 次 binary64
bisection。`invalid_bracket` row 不得返回 IV 或 market `d1/d2`，并从 model-signal candidate 中排除；
它本身不让 series/authoring 失败。Linked counterfactual 仍由 Stage-1 diffusion 的 exact integrated
variance 构造，market-IV repricing 只验证 inverse result，不能成为零 residual 的 counterfactual。

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
- F2A v1/v2/v3 repo tests 只保护 historical identities；v4 tests 使用 tracked complete-chain fixture 和
  真实 public quote/spot mutation，禁止用抽象 affine trigger 或 finite spot grid 代替 reachability/
  pathwise certificate。当前 artifact 是单-parent `audit` scope，不得描述成正式 training split。
- V5.1 tests 使用 `schemas/submission-v5.1.schema.json` 与 top-level `option_series_results`；旧
  `submission-v5.schema.json` 只做 historical pilot replay。单 task-seed FP/FN diagnostic 不能冒充
  2,000+ independent-seed cohort release report。

## 完成检查

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/pytest -q tests/unit
git diff --check
```
