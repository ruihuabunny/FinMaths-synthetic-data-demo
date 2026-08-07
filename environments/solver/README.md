# Solver Environment Allowlist

本文件是 Solver image、import audit 和 runtime capability gate 的权威 allowlist 合同。
Task variant 可以进一步收紧权限，但不得扩大这里的允许范围。

当前状态：allowlist 设计已冻结；`requirements.lock` 和 runtime enforcement 尚未按本文件全部实现。
在 dependency lock、trusted DuckDB adapter、import/API audit 和 sandbox tests 全部落位前，不得把
Solver environment 标记为 production-ready。

## 当前仓库状态

| 部件 | 当前状态 | 含义 |
|:---|:---:|:---|
| 本 README | 目标合同 | 描述最终允许的 distributions、imports、tools 与 APIs。 |
| [`requirements.lock`](requirements.lock) | 部分实现 | 目前只锁定 `duckdb==1.5.5`，尚未包含下列 NumPy/Pandas 及其依赖。 |
| Trusted DuckDB adapter | 未实现 | 现在没有可供不受信 Solver 使用的 `query_public_child_v1`。 |
| Import/API audit | 未实现 | README 中的 allow/deny 规则尚未由 runtime gate 强制执行。 |
| Sandbox acceptance tests | 未实现 | 不能仅凭依赖文件推断网络、filesystem 或 extension 已隔离。 |

因此本目录当前不能单独构建可运行的 F2A Solver。仓库根目录的 `.venv` 是 authoring/test
环境，包含 QuantLib，不能复用为 Solver image，也不能作为权限隔离通过的证据。

## F2A target distributions

Python 固定为 `3.12.x`。F2A Solver image 计划只安装以下第三方 distributions，包括显式锁定的
Pandas 必需依赖：

```text
duckdb==1.5.5
numpy==2.5.1
pandas==3.0.4
python-dateutil==2.9.0.post0
six==1.17.0
```

不安装 optional Pandas I/O、plotting、SQL、Excel、Parquet 或 acceleration dependencies。
尤其不安装 PyArrow、SciPy、Numba、NumExpr、SQLAlchemy、fsspec 或 filesystem/cloud clients。

版本依据：

- [DuckDB Python result conversion](https://duckdb.org/docs/current/clients/python/conversion)
- [NumPy releases](https://numpy.org/news/)
- [Pandas installation dependencies](https://pandas.pydata.org/pandas-docs/stable/getting_started/install.html)

## Direct-import allowlist

Solver-authored 计算代码只能直接 import：

```text
numpy
pandas
math
decimal
datetime
json
dataclasses
typing
itertools
```

`duckdb` 只能由 trusted `query_public_child_v1` adapter import。Solver-authored计算代码不得取得
raw `DuckDBPyConnection`、创建新 connection、注册 UDF 或直接调用 DuckDB module-level API。
Import audit 检查的是 Solver 源码中的 direct imports；允许包的内部传递 imports 不计作 Solver
主动扩权。

这里的列表是源码 capability contract，不代表当前 lock 已经安装对应 distribution。
在 NumPy/Pandas 被锁定并通过 import/API audit 之前，依赖它们的 Solver 代码仍不可发布。

## Data and tool allowlist

F2A Solver 只允许调用：

```text
query_public_child_v1
submit_trajectory_v1
```

Query adapter 只暴露当前 task 的 frozen public child，并且只允许读取：

```text
solver_visible.underlying_daily
solver_visible.option_daily
solver_visible.pricing_metadata
```

每题应对每个所需 view 至多做一次批量查询，随后把结果转换为 Pandas DataFrame 或 NumPy arrays，
在内存中完成稳定排序、分组、期限匹配和 candidate enumeration。禁止按 candidate 或 option leg
重复往返 DuckDB。

## Allowed numerical and tabular operations

NumPy 允许：

- `float64` arrays、显式 shape/dtype 转换和有限性检查；
- stable sorting/indexing、boolean masks、concatenation 和 deterministic reductions；
- 基础算术及 `exp`、`log`、`sqrt` 等通用数值原语。

Pandas 允许：

- `DataFrame`、`Series`；
- `merge`、`concat`、`groupby`、`sort_values` 和显式 column selection；
- 向 NumPy `float64` arrays 的确定性转换。

F2A 的 canonical operation order、row/pair order 和 dtype 仍由 variant contract 决定。允许
NumPy/Pandas 不授权替换冻结算法：不得用 `isclose`、`allclose` 或 tolerance 改写 candidate-specific
predicate：

```text
(s_j > 0 and g_j >= 0 P-a.s.)
or (s_j == 0 and g_j >= 0 P-a.s. and P(g_j > 0) > 0)
```

Parity 的恒零 future payoff 使用 open boundary `s_j > 0`；support-certified nonconstant nonnegative
payoff 使用 closed boundary `s_j >= 0`。Calendar 必须另外通过完整 pathwise certificate。不得使用
未声明的并行 reduction、随机顺序或不同 linear-algebra method。Legacy variant v1/catalogue v2 的
统一 strict-positive 字符串只用于重放；blocked variant v2/catalogue v3 不可作为可运行 Solver task，
calendar 启用还需要新的 immutable identity。

## Denied APIs and capabilities

即使 package 本身在 allowlist 中，以下能力仍禁止：

- NumPy/Pandas 文件、pickle、memory-map、SQL 和网络 I/O，包括 `numpy.load/save/loadtxt/fromfile/memmap`
  与 `pandas.read_*`、`DataFrame.to_csv/to_parquet/to_pickle/to_sql/to_excel/to_json`；
- `numpy.random`、未声明的 `numpy.linalg`、`numpy.isclose/allclose`；
- `eval`、`exec`、动态 import、subprocess、socket、网络、动态安装和任意 filesystem traversal；
- 任何预制 option pricing、IV、Greek、volatility smile/surface 或 arbitrage-scanning API。

明确禁止安装或 import `QuantLib`、`py_vollib`、`vollib`、`py_lets_be_rational`、`mibian`、
`rateslib`、`financepy`、`pyfeng`、`optionprice`、`opstrat`、`pysabr`、`tf_quant_finance`、
`numpy_financial` 及功能等价包。Package 名黑名单只是诊断；真正的边界由 allowlist-only image、
capability gate 和隔离 sandbox 共同提供。

## Trusted DuckDB adapter

`query_public_child_v1` 必须：

- 只持有当前 child 的 read-only connection；
- 禁用 external access、extension autoload/autoinstall、community/unsigned extensions 和 persistent secrets；
- 初始化后锁定 DuckDB configuration；
- 只挂载 public child，不挂载 parent、private lineage、authoring tables 或 hidden verifier；
- 限制 statement count、执行时间、memory、threads、rows 和 output bytes；
- 拒绝写操作以及 `ATTACH/COPY/INSTALL/LOAD/EXPORT/IMPORT/CREATE SECRET` 等扩权语句。

DuckDB 自身的安全设置属于 defense in depth，不能替代只读 mount、无网络容器和 OS-level sandbox。
参考 [Securing DuckDB](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)。

## Enforcement acceptance

实现完成至少需要以下 tests：

- dependency lock 中只有上述 distributions 及其冻结 transitive dependencies；
- 允许的 direct imports 成功，任一未列 import 失败；
- banned finance packages、NumPy/Pandas I/O 和 raw DuckDB connection 均不可用；
- DuckDB external access、extension loading、write/attach/copy 和 configuration re-enable 均失败；
- 每题 query count 不超过 contract，DataFrame/arrays 不含 private columns；
- fixed public child 在 SQL-to-memory 路径上可 deterministic replay；
- NumPy/Pandas 实现和 trusted verifier 产生相同 canonical F2A ORM answer。

## 变更规则

- 新增 distribution、direct import、tool 或 API 都是权限扩大，必须同时更新 lock、runtime
  gate、正向测试和至少一个拒绝该能力的负向测试。
- 版本升级必须重新冻结完整 transitive dependency set；不能只改本文中的版本号。
- Solver-visible schema 改动必须同步 query adapter 的列 allowlist 和数据泄漏测试。
- Authoring/verifier 依赖不得复制进本目录；尤其不能为了方便安装 QuantLib 或现成
  option/arbitrage package。
- Task variant 可以减少权限，但任何 variant 都不能覆盖本文件扩大权限。
