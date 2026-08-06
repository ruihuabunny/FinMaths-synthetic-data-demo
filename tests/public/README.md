# Public tests

本目录验证仓库对使用者承诺的 authoring 行为和公开 snapshot contract：可运行模板、
事务增量语义、solver-visible schema、checked-in snapshot/manifest，以及公开 SQL 查询。

这里的“public”不等于每个断言都只能查询 `solver_visible`。Authoring smoke tests 可以检查
`market`/`metadata` tables 来确认写入质量；真正面向 Solver 的字段边界由
`test_snapshot_schema.py` 单独锁定。

从仓库根目录运行：

```bash
.venv/bin/pytest -q tests/public
```

## 文件与覆盖范围

| 文件 | 主要覆盖 |
|:---|:---|
| [`test_authoring_smoke.py`](test_authoring_smoke.py) | 5-underlying baseline、初次生成、NOOP rerun、append 单日、legacy additive underlying/option template、freeze 和 failed-run audit。 |
| [`test_authoring_template.py`](test_authoring_template.py) | Checked-in QuantLib template 可运行、deterministic time functions 写入 metadata、one-shot/append equality 和 existing-definition behavior。 |
| [`test_snapshot_schema.py`](test_snapshot_schema.py) | `solver_visible` 三个 views 覆盖 framework 字段，以及 checked-in DuckDB 与 manifest 的 status/revision/counts 一致。 |
| [`test_sql_queries.py`](test_sql_queries.py) | `snapshots/public/sql_query` 文件集合、只读约束、DuckDB 可执行性和固定结果行数。 |

## Public contract

### Solver-visible views

当前公开 snapshot contract 是：

- `solver_visible.underlying_daily`
- `solver_visible.option_daily`
- `solver_visible.pricing_metadata`

Private authoring provenance，例如 `created_run_id`、`generated_run_id`、
`market.underlying_dependence` 和 `market.option_chain_specs`，不得因为 schema 重构意外进入
solver-visible views。

注意：`solver_visible.pricing_metadata` 目前仍是 smoke 阶段的过渡结构，尚未完成
public/private metadata split。若主动改变它，必须同步 framework 文档、schema tests 和
task contracts。

### Incremental behavior

Public pipeline 必须满足：

- 首次创建返回 `COMPLETED` 并写入 revision 1；
- 相同 config/range 重跑返回 `NOOP`，revision 不变；
- append 只新增请求日期的 daily/metadata rows，不重写历史；
- legacy additive config 只回填新增 entity/contract family；
- `FROZEN` snapshot 拒绝后续写入，并记录失败 run；
- manifest counts 与 DuckDB logical counts 一致。

### Checked-in snapshot

[`snapshots/public/quantlib_bsm_smoke_v1.duckdb`](../../snapshots/public/quantlib_bsm_smoke_v1.duckdb)
在 public tests 中只读打开。测试不得原地 migrate、append、freeze 或覆盖该文件；需要写入
的场景一律使用 `tmp_path` 创建独立 DuckDB。

Checked-in snapshot 的 manifest 位于
[`quantlib_bsm_smoke_v1.manifest.json`](../../snapshots/public/quantlib_bsm_smoke_v1.manifest.json)。
更新 snapshot 时必须同步 status、revision、日期范围和各表 row counts。
当前逻辑 snapshot 是 config `1.4.0` 的 22-metal、65-business-day liquid option-chain
profile；legacy 5-underlying config 只服务快速 incremental regression tests。

## Public SQL queries

[`snapshots/public/sql_query`](../../snapshots/public/sql_query) 下的 SQL 必须：

- 只读，不包含 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE` 或 `DROP`；
- 可直接在 checked-in DuckDB 上执行；
- 使用明确参数 CTE 和稳定排序；
- 若新增或删除文件，同步更新 `test_sql_queries.py` 的文件集合和 expected row counts。

SQL 中的 `generation_audit.sql` 是 authoring audit 查询，不表示对应字段对 Solver 可见。

## Pytest fixtures 与测试数据库

共享 fixtures 位于 [`tests/conftest.py`](../conftest.py)。Public generation tests 使用
`tmp_path` 保存临时 configs、DuckDB 和 manifests，测试结束后由 pytest 管理。它们不是
长期测试报告；`pytest` pass/fail 结果也不会写入 DuckDB。

如果需要保留一次手工 smoke snapshot，应显式选择仓库外路径，例如：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/public-authoring-smoke.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  create-smoke
```

## 新增 public test 的约定

- 优先通过 `AuthoringPipeline` 或 CLI 支持的配置入口测试，不依赖未公开 helper。
- 对多行 public data 使用稳定 `ORDER BY` 后执行 exact comparison。
- 新字段先判断属于 solver-visible market fact 还是 private authoring provenance。
- 新增 schema/table 时补充 visibility test，明确“应出现”和“不应出现”的对象。
- 不修改 checked-in snapshot，除非任务明确要求重新发布 snapshot 与 manifest。
- 大规模压力测试保留可运行 config；常规 public suite 应控制运行时间和临时文件大小。

## 完成检查

```bash
.venv/bin/pytest -q tests/public
.venv/bin/pytest -q
git diff --check
```
