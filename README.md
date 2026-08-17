# FinMaths Synthetic Data Demo

面向 LLM 训练与 agent evaluation 的可重放合成金融衍生品项目。仓库把 market
authoring、公开数据导出、task contract、受限 Solver、独立 verifier、portable packaging
和训练数据导出划分为不同权限边界；固定配置、版本、seed、随机流和数值约定后，同一
market snapshot 可以确定性重放。

当前可运行主线是 GBM–BSM：QuantLib 在 authoring/trusted verifier 边界中生成或复算，
Solver 侧使用冻结合同允许的标准库实现。`P`-measure 历史路径、`Q`-measure 定价状态和
visible-price implied volatility 始终是不同对象。

## 当前实现状态

| 能力 | 状态 | 实现位置 |
|:---|:---:|:---|
| `P`-measure underlying 与静态 option chain authoring | 已实现 | `src/synthetic_derivatives/authoring/` |
| P/Q-qualified underlying dependence 与 common-`Q` vanilla pricing context | 已实现 baseline | `authoring/`、generator configs |
| Frozen parent → public-only DuckDB child | 已实现 | `src/synthetic_derivatives/export/` |
| 七维 task space、deterministic mutation、adaptive curriculum | 最小版本已实现 | `task_space/`、`mutation/`、`curriculum/` |
| Analytic BSM、visible-price IV、market-implied unit Greeks | 已实现 | `solver/analytic_and_implied_greeks_iv/` |
| 独立 QuantLib hard verifier 与 canonical exact comparison | 已实现 | `verifier/`、package-local verifier runtime |
| Combined IV+Greeks v2 portable delivery | 已实现，100 tasks | `task_packages/deliveries/bsm_market_implied_greeks_v1/` |
| Single-metric 6×4 static-query 与 DuckDB-query v3 suites | 已实现，24 tasks each | `task_packages/deliveries/bsm_market_implied_metric_suite_v1/` |
| BSM-specific verified training export | 已实现 | `training/bsm_market_greeks.py` |
| L3 Monte Carlo Greeks | 仅 scaffold/计划 | `solver/mc/`；尚无 estimator、draw bank、variant 或 verifier |
| Basket/spread joint payoff、new model families、F2A arbitrage | 尚未实现 | 仅设计或非活动计划 |

“目录存在”不代表 runtime capability 或实现完成；尤其 `solver/mc/` 目前只有可导入骨架。
已接受的 source packages、portable deliveries 和 public snapshots 是冻结制品，不原地修改。

## 快速开始

要求 Python `3.12.x` 和 `make`。依赖锁定在 `requirements.lock` 与各 runtime lock 中。

```bash
make install
make snapshot-summary
make test
```

`snapshot-summary` 只读 checked-in public snapshot。生成或追加数据默认写入 `/tmp`：

```bash
make smoke
make append-day
```

构建一个独立的 BSM Greeks source package：

```bash
.venv/bin/python scripts/package_bsm_greeks_task.py \
  --base-parent-config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  --output-root /tmp/bsm-greeks-packages
```

完整命令与产物边界见 [scripts 说明](scripts/README.md) 和
[agent-task package 说明](task_packages/README.md)。

## 权限边界

| Boundary | 可以读取/执行 | 不得跨越的边界 |
|:---|:---|:---|
| Authoring | private config、seed、QuantLib、frozen parent 写入 | 不向 Solver 暴露 latent state/oracle |
| Public export | 只读 frozen parent，写独立 public child | 不修改 parent，不复制私有列 |
| Agent Solver | effective runtime profile 与 trusted adapters | 无 QuantLib、网络、动态安装、raw private DB/verifier |
| Trusted verifier | public task inputs、submission、pinned QuantLib/DuckDB | 不导入 Solver numerics，不读取 reference answer |
| Training/export | verified source package 与公开 trajectory | 不输出 oracle、hidden tests、selector seed 或 private lineage |

共享边界仅限输入、单位、method identity、canonicalization 和 schema；Solver 与 verifier
不能共享定价、求根或 Greek 数值实现。

## 仓库导航

| 想做什么 | 从这里开始 |
|:---|:---|
| 理解整体架构和模块状态 | [`src/synthetic_derivatives/README.md`](src/synthetic_derivatives/README.md) |
| 修改 snapshot generator | [`src/synthetic_derivatives/authoring/README.md`](src/synthetic_derivatives/authoring/README.md) |
| 理解 public DuckDB export | [`src/synthetic_derivatives/export/README.md`](src/synthetic_derivatives/export/README.md) |
| 修改 analytic BSM/IV/Greeks | [`solver package README`](src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/README.md) |
| 检查 runtime allowlist | [`environments/README.md`](environments/README.md) |
| 检查 portable delivery | [`task_packages/README.md`](task_packages/README.md) |
| 运行或扩展测试 | [`tests/README.md`](tests/README.md) |
| 阅读架构、curriculum、plans 与 reports | [`docs/README.md`](docs/README.md) |
| 查询 public snapshot | [`snapshots/public/sql_query/README.md`](snapshots/public/sql_query/README.md) |

## 文档规则

根目录只保留项目入口。当前行为以代码、versioned config/schema、frozen manifest 和通过的
tests 为准；`docs/plans/`、`docs/curricula/` 与历史 handoff/report 记录目标或迁移历史，
不能单独证明功能已经实现。
