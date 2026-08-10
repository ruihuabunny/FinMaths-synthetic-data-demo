# Solver Environment Allowlist

本文件是 Solver image、import audit 和 runtime capability gate 的权威 allowlist 合同。
Task variant 可以进一步收紧权限，但不得扩大这里的允许范围。

当前状态：v4/v5.1 reference Solver 已在仓库源码中实现并通过独立 verifier tests，但它运行在含
QuantLib 的 repository development environment，且会直接打开 public DuckDB。这里的 production
allowlist image、trusted query adapter、import/API audit 和 sandbox tests 尚未落地，因此不能把
“reference Solver 可运行”写成“Solver environment production-ready”。

## 当前仓库状态

| 部件 | 当前状态 | 含义 |
|:---|:---:|:---|
| 本 README | 目标合同 | 描述最终允许的 distributions、imports、tools 与 APIs。 |
| [`requirements.lock`](requirements.lock) | 最小 lock 已存在 | 当前只锁定 `duckdb==1.5.5`；reference F2A 数值代码使用标准库，没有 NumPy/Pandas runtime dependency。 |
| Reference Solver | 已实现，非隔离运行 | [`src/synthetic_derivatives/solver/`](../../src/synthetic_derivatives/solver/) 可求解 v4 ORM 与 v5.1 full trajectory；直接文件访问只服务开发/测试。 |
| Trusted DuckDB adapter | 未实现 | 现在没有可供不受信 Solver 使用的 `query_public_child_v1`。 |
| Import/API audit | 未实现 | README 中的 allow/deny 规则尚未由 runtime gate 强制执行。 |
| Sandbox acceptance tests | 未实现 | 不能仅凭依赖文件推断网络、filesystem 或 extension 已隔离。 |

因此本目录当前不能单独构建 production F2A Solver image。仓库根目录的 `.venv` 是
authoring/test 环境，包含 QuantLib，不能复用为 Solver image，也不能作为权限隔离通过的证据。

## 当前 dependency boundary

Python 固定为 `3.12.x`。当前 solver lock 只允许一个第三方 distribution：

```text
duckdb==1.5.5
```

NumPy、Pandas、SciPy、PyArrow、Numba、NumExpr、SQLAlchemy、fsspec 和 filesystem/cloud clients
均未锁定，也不应被 reference implementation 的未来重构悄悄引入。若某个新 variant 确实需要
NumPy/Pandas，必须先版本化权限合同、完整锁定依赖并补 enforcement tests。

## Direct-import allowlist

Production Solver-authored 计算代码的目标 allowlist 是：

```text
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

仓库中的 reference orchestrator 还使用 `pathlib`、`hashlib` 并直接 import `duckdb` 来读取本地
fixture/artifact；这些能力只属于受信开发 harness，不自动进入 production Solver allowlist。

## Data and tool allowlist

F2A Solver 只允许调用：

```text
query_public_child_v1
submit_trajectory_v1
```

Query adapter 必须按 variant 暴露当前 task 的 frozen public child。V4 只允许：

```text
solver_visible.underlying_daily
solver_visible.option_daily
solver_visible.pricing_metadata
```

V5.1 只允许：

```text
solver_visible.underlying_daily
solver_visible.f2a_option_quotes
solver_visible.option_contracts
solver_visible.pricing_inputs
solver_visible.physical_node_locations
solver_visible.f2a_contracts
```

V5.1 的 node-location table 只含公开 offsets，不含 drift/diffusion node values；contracts 分为
physical fitting、BSM inversion、linked validation 与 model-signal rows。两个 variant 的表名和列
allowlist 不能合并为 `SELECT *` fallback。

每题应对每个所需 relation 至多做一次批量查询，随后在内存中完成稳定排序、分组、期限匹配和
candidate enumeration。禁止按 candidate 或 option leg 重复往返 DuckDB。

## Allowed numerical and tabular operations

当前 reference algorithms 使用标准库 `float`/`Decimal`、list/tuple/dict、显式排序与稳定循环，
以及 `math.exp/log/sqrt/erf` 等基础原语。V5.1 BSM inversion 固定为 binary64 80-step bisection，
不得换成预制 root finder；linked variance 必须按 piecewise-linear squared diffusion 精确积分。

F2A 的 canonical operation order、row/pair order 和 dtype 仍由 variant contract 决定。任何通用
数值工具都不授权替换冻结算法：不得用 tolerance 改写 candidate-specific predicate：

```text
(s_j > 0 and g_j >= 0 P-a.s.)
or (s_j == 0 and g_j >= 0 P-a.s. and P(g_j > 0) > 0)
```

Parity 的恒零 future payoff 使用 open boundary `s_j > 0`；support-certified nonconstant nonnegative
payoff 使用 closed boundary `s_j >= 0`。Calendar 必须另外通过完整 pathwise certificate。不得使用
未声明的并行 reduction、随机顺序或不同 linear-algebra method。Legacy variant v1/catalogue v2 的
统一 strict-positive 字符串只用于重放；blocked variant v2/v3 不可运行；calendar-enabled executable
identity 是 v4。V5.1 的 X/U/T 是 linked-counterfactual model signal，不得套用 v4
executable-arbitrage predicate 作为其评分语义。

## Denied APIs and capabilities

即使 package 本身在 allowlist 中，以下能力仍禁止：

- 未列出的 tabular/numerical packages，以及文件、pickle、memory-map、SQL 和网络 I/O helpers；
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

- dependency lock 中只有声明的 distribution 及其冻结 transitive dependencies；
- 允许的 direct imports 成功，任一未列 import 失败；
- banned finance packages、NumPy/Pandas I/O 和 raw DuckDB connection 均不可用；
- DuckDB external access、extension loading、write/attach/copy 和 configuration re-enable 均失败；
- 每题 query count 不超过 contract，返回 rows 不含 private columns；
- fixed public child 在 SQL-to-memory 路径上可 deterministic replay；
- V4 Solver 与 trusted verifier 产生相同 canonical ORM answer；v5.1 Solver 与 verifier 的
  V0/V1/V2/V3 semantic layers exact-match。

## 变更规则

- 新增 distribution、direct import、tool 或 API 都是权限扩大，必须同时更新 lock、runtime
  gate、正向测试和至少一个拒绝该能力的负向测试。
- 版本升级必须重新冻结完整 transitive dependency set；不能只改本文中的版本号。
- Solver-visible schema 改动必须同步 query adapter 的列 allowlist 和数据泄漏测试。
- Authoring/verifier 依赖不得复制进本目录；尤其不能为了方便安装 QuantLib 或现成
  option/arbitrage package。
- Task variant 可以减少权限，但任何 variant 都不能覆盖本文件扩大权限。
