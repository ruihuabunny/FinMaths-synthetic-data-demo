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
