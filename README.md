# FinMaths Synthetic Data Demo

面向 LLM 训练的确定性合成金融衍生品数据项目。项目目标是把市场数据生成、任务定义、模型解题、独立校验和训练数据导出分成清晰的边界，并保证每个样本都可以通过固定配置与 seed 重放。

当前已完成 authoring pipeline 的第一版：使用 QuantLib 生成 underlying 路径和 European option 报价，通过 DuckDB 事务增量写入 snapshot。在此基础上，仓库已加入最小可运行的六维 `task_space` registry、受约束 `mutation` engine 和 adaptive `curriculum` scheduler；它们只按 id/hash 引用 snapshot，不修改 authoring 数据。Solver、verifier 与训练数据构建尚未实现。

## 设计流程

```text
Authoring
  -> 生成并冻结 market snapshot
  -> 注册六维 task variant 与 compatibility decision
  -> Mutation Engine 生成带 lineage 的 candidate pool
  -> Curriculum Scheduler 按 mastery 选择训练分布
  -> Solver 手工计算 IV / Greeks / smile
  -> Trusted verifier 独立复算并执行 exact equality
  -> 记录 ORM outcome 与 agent trajectory
  -> 导出 LLM training dataset
```

Authoring、Solver 和 Trusted verifier 是三个不同的权限边界：

- Authoring 可以使用固定版本的 QuantLib 和固定 seed 生成市场数据。
- Solver 只能读取公开快照与合同，不得调用现成的定价、IV、Greeks 或 smile API。
- Trusted verifier 可以使用固定版本的金融与数值包独立复算，但不能向 Solver 暴露 oracle 或 hidden tests。

## Authoring smoke test

仓库内置 [QuantLib generator 配置](configs/generators/quantlib_bsm_smoke_v1.json) 和 [DuckDB smoke snapshot](snapshots/public/quantlib_bsm_smoke_v1.duckdb)。这里的“5 种 option”表示 5 个 option contract templates；它们分别实例化到 5 个 underlyings 上，因此 5 个交易日的数据规模为：

| 对象 | 行数 |
|:---|---:|
| Underlying master | 5 |
| Option contract master | 25（5 × 5） |
| `underlying_daily` | 25（5 × 5 日） |
| `option_daily` | 125（25 × 5 日） |
| `pricing_metadata` | 25（5 × 5 日） |

先创建或幂等同步基准数据：

```bash
make install
make smoke
make snapshot-summary
```

加入第 6 个交易日只会新增该日的 5 条 underlying、25 条 option 和 5 条 metadata：

```bash
make append-day
```

增加 underlying 或 option template 时，先在 generator config 的相应数组中追加定义，然后运行：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database snapshots/public/quantlib_bsm_smoke_v1.duckdb \
  --config configs/generators/quantlib_bsm_smoke_v1.json \
  sync-config
```

`sync-config` 会为新增品种回填当前已有日期区间；已存在的业务主键和相同 row hash 不会重写。完整的 schema、主键、增量规则和命令见 [Authoring Pipeline](docs/authoring_pipeline.md)。

## Deterministic physical drift / volatility

Generator config schema `1.1.0` 支持把 underlying 的 P-measure
`physical_drift` 和 `physical_volatility` 配置成时间的确定性分段线性函数。原来的
scalar 写法继续支持，并等价于 constant function。当前 checked-in
`quantlib_bsm_smoke_v1` 保留为 constant baseline；新 job 可以从已使用时间函数的
[QuantLib/BSM generator 模板](authoring/templates/quantlib_bsm_generator.template.json)
复制配置。核心配置片段如下；完整可运行字段以模板为准。

```json
{
  "schema_version": "1.1.0",
  "start_date": "2026-08-03",
  "underlyings": [
    {
      "underlying_id": "SYNTH-EXAMPLE-01",
      "initial_spot": 100.0,
      "physical_drift": {
        "type": "piecewise_linear",
        "nodes": [
          {"day_offset": 0, "value": 0.07},
          {"day_offset": 2, "value": 0.04},
          {"day_offset": 4, "value": 0.08}
        ],
        "extrapolation": "flat"
      },
      "physical_volatility": {
        "type": "piecewise_linear",
        "nodes": [
          {"day_offset": 0, "value": 0.22},
          {"day_offset": 2, "value": 0.30},
          {"day_offset": 4, "value": 0.18}
        ],
        "extrapolation": "flat"
      },
      "risk_free_rate": 0.03,
      "dividend_yield": 0.01,
      "base_implied_volatility": 0.23
    }
  ]
}
```

`day_offset` 是从 `start_date` 开始计算的日历日，不是 business-day index。节点间
使用线性插值；第一个节点必须为 0，节点必须按严格递增的整数 offset 排列，节点外
目前只支持 flat extrapolation。Physical volatility 的所有节点必须大于 0。例如
上面的 drift 在 day 0、1、2 分别为 `0.070`、`0.055`、`0.040`。

路径生成不是简单地取 close date 终点处的函数值，而是对每个
`previous close -> current close` 区间计算精确等效参数：

$$
\mu_{\mathrm{eff}}
=\frac{1}{\Delta t}\int_{t_0}^{t_1}\mu(t)\,dt,
\qquad
\sigma_{\mathrm{eff}}
=\sqrt{\frac{1}{\Delta t}\int_{t_0}^{t_1}\sigma^2(t)\,dt}.
$$

线性段端点为 $a,b$ 时，drift 的区间平均是 $(a+b)/2$，volatility 的区间
RMS 是 $\sqrt{(a^2+ab+b^2)/3}$。跨节点或跨周末的区间会在节点处拆分并按完整
日历区间积分。因此 Aug 3 到 Aug 4 的 drift 若从 `0.070` 线性走到 `0.055`，
传给 QuantLib 的 $\mu_{\mathrm{eff}}$ 为 `0.0625`；volatility 若从 `0.22`
走到 `0.26`，则 $\sigma_{\mathrm{eff}}$ 约为 `0.240277617`。

当前实现由项目代码完成 piecewise-linear 插值与精确积分，再把每个区间的等效参数
分别放入 QuantLib `FlatForward` 和 `BlackConstantVol`，最后调用
`BlackScholesMertonProcess.evolve`。这与 deterministic time-inhomogeneous GBM
在观测网格上的 exact transition 对齐：

$$
\log\frac{S_{t_1}}{S_{t_0}}
=\int_{t_0}^{t_1}\left(\mu(t)-\frac12\sigma^2(t)\right)dt
+\sqrt{\int_{t_0}^{t_1}\sigma^2(t)dt}\,Z.
$$

对应实现位置：

- [config.py](src/synthetic_derivatives/authoring/config.py)：配置解析、节点验证、线性插值、drift 积分与 volatility RMS；
- [generator.py](src/synthetic_derivatives/authoring/generator.py)：按真实日期区间计算等效参数并注入 QuantLib process；
- [pipeline.py](src/synthetic_derivatives/authoring/pipeline.py)：增量生成时保留每条路径的真实 previous date，保证一次生成和 append 的结果一致；
- [公开测试](tests/public/test_authoring_template.py)与[单元测试](tests/unit/test_authoring_config.py)：覆盖每日参数变化、精确积分、append invariance、配置校验与函数定义不可变性。

`market.underlyings.physical_drift` 和 `physical_volatility` 为兼容现有 schema，保存
函数在 `day_offset = 0` 的值。完整函数、当日实际使用的有效参数、区间起止日期和
reduction method 写入 `market.pricing_metadata.physical_dynamics`。这一扩展只改变
underlying 的 physical dynamics；期权 Q-measure 定价仍使用原有的
risk-free/dividend curves 和 deterministic $K,T$ smile。

可以直接检查每天实际使用的 interval-equivalent 参数：

```sql
SELECT
    valuation_date,
    underlying_id,
    CAST(json_extract_string(physical_dynamics, '$.drift') AS DOUBLE)
        AS effective_drift,
    CAST(json_extract_string(physical_dynamics, '$.volatility') AS DOUBLE)
        AS effective_volatility,
    json_extract_string(physical_dynamics, '$.interval_start') AS interval_start,
    json_extract_string(physical_dynamics, '$.interval_end') AS interval_end
FROM market.pricing_metadata
ORDER BY underlying_id, valuation_date;
```

常用的只读 DuckDB 查询集中保存在
[`snapshots/public/sql_query/`](snapshots/public/sql_query/README.md)，包括 snapshot 摘要、
underlying 时间序列、option chain、moneyness、pricing context 和 authoring audit。
每个 SQL 文件都在顶部提供可编辑的 `parameters` CTE，并显式固定结果排序。

## 仓库结构

```text
.
├── .venv/                         # 仓库本地 Python 虚拟环境，不提交 Git
├── authoring/
│   ├── configs/                   # Authoring job 的内部生成配置
│   └── templates/                 # 新 generator、snapshot 和 variant 的模板
├── configs/
│   ├── generators/                # 可发布的 generator 配置与版本声明
│   ├── task_space/                # 六维坐标范围和 compatibility registry
│   ├── mutations/                 # 受约束 mutation operators
│   ├── curricula/                 # stage、mastery bands 和采样 mixture
│   └── variants/                  # Solver 可见的 task/method/output contracts
├── datasets/
│   ├── generated/                 # 构建出的训练 JSONL/Parquet，不提交 Git
│   └── manifests/                 # 数据集版本、split、来源和 hash 清单
├── docs/
│   ├── examples/                  # 完整设计样例
│   └── *.md                       # 框架、架构和方法说明
├── environments/
│   ├── authoring/                 # Authoring 隔离环境定义
│   ├── solver/                    # Solver allowlist、denylist 与沙箱定义
│   └── verifier/                  # Trusted verifier 隔离环境定义
├── examples/
│   ├── submissions/               # 可公开的 canonical submission 样例
│   └── trajectories/              # 可公开的正向/负向 agent trajectory 样例
├── runs/                          # 本地 solver/verifier 运行结果，不提交 Git
├── schemas/                       # Snapshot、variant、trajectory、submission schemas
├── scripts/                       # venv、生成、校验和数据集构建入口脚本
├── snapshots/
│   └── public/                    # 小型、可公开且带 logical hash 的 DRAFT/FROZEN 快照
│       └── sql_query/             # 可复用、只读且显式排序的常用 DuckDB 查询
├── src/
│   └── synthetic_derivatives/
│       ├── authoring/             # 市场快照生成与冻结实现
│       ├── task_space/            # 六维 task grammar 与 compatibility
│       ├── mutation/              # 确定性 child task 与 lineage
│       ├── curriculum/            # 不改 task/reward 的 adaptive sampling
│       ├── solver/                # 受限环境中的公式、求根、Greeks 与拟合实现
│       ├── training/              # Trajectory 清洗、split 和 LLM 数据导出实现
│       └── verifier/              # 独立 package oracle 与 hard verifier 实现
└── tests/
    ├── fixtures/                  # 小型冻结测试输入和公开期望结构
    ├── unit/                      # task-space、mutation、curriculum 单元测试
    ├── integration/               # task manifest 与 authoring snapshot 边界
    ├── verifier_robustness/       # 验证错误数字、方法和 schema 必须被拒绝
    └── public/                    # Snapshot、contract、重放和端到端公开测试
```

`.agents/` 与 `.codex/` 是本地协作工具使用的目录，不属于项目的数据生产接口。

## 各目录的职责

### `authoring/`

存放出题端的配置与模板，不放 Solver 可见的数据。Authoring pipeline 使用 pinned QuantLib、generator config、seed 和 RNG 生成 `underlying_daily`、`option_daily` 与 `pricing_metadata`，随后冻结并计算 hash。内部 audit 与 oracle 输出应保持私有。

新增 authoring job 时可从 [QuantLib/BSM generator 模板](authoring/templates/quantlib_bsm_generator.template.json) 复制配置；字段约束和 DRAFT/freeze 用法见 [模板说明](authoring/templates/README.md)。

### `configs/`

保存可复现行为所需的声明式配置：

- `generators/` 描述模型、参数、随机数生成器、draw order、定价 engine 和 generator version。
- `task_space/` 描述 $L/P/M/A/D/R$ 六个轴与合法 product--model--method 组合。
- `mutations/` 描述允许改变的轴、单次最大变更轴数和方向约束。
- `curricula/` 描述 stage、20/60/20 replay/current/explore mixture 与 mastery 调度区间。
- `variants/` 描述某一道任务的 snapshot、金融约定、method IDs、数值顺序、舍入规则、Solver 权限和输出格式。

配置文件只描述合同，不存放实现代码或 hidden reference answer。

### `snapshots/`

保存生成后冻结的市场快照。`public/` 仅提交小型 demo；批量快照与私有快照不进入 Git。当前 public demo 使用 DuckDB，配套内容包括：

- `quantlib_bsm_smoke_v1.duckdb`：market、metadata 和 solver-visible views；
- `quantlib_bsm_smoke_v1.manifest.json`：revision、行数与 logical SHA-256；
- `sql_query/`：只读查询模板，不包含 canonical answer 或 hidden oracle。

IV、Greeks、smile、surface、VaR 和 ES 是从统一快照派生的任务结果，不在这里维护彼此独立的 truth tables。

### `schemas/`

定义跨边界的数据结构，包括 market snapshot、task variant、agent trajectory、Solver submission、verification report 和 dataset record。Schema 用于在进入下一阶段前拒绝缺字段、错单位、错顺序或非法数值。

### `src/synthetic_derivatives/`

项目的 Python 源码根目录。权限边界模块与训练编排模块分开：

- `authoring/`：允许使用 QuantLib，负责生成、质量门控、冻结与 hash。
- `task_space/`：只判断六维坐标和 task family 是否兼容，不生成任务、不决定采样。
- `mutation/`：从不可变母题生成确定性 child task 和 lineage，不读取模型表现。
- `curriculum/`：根据 stage 与 `pass@1` diagnostics 计算采样权重，不修改 frozen task 或二值 hard reward。
- `solver/`：只使用合同允许的基础原语，自行实现指定计算方法。
- `verifier/`：不得导入 Solver 的定价实现；使用独立 package oracle 复算并做 canonical exact equality。
- `training/`：把通过校验的任务、trajectory、证据和 outcome 转换为 LLM 训练记录，并按 snapshot 分组切分数据，避免泄漏。

生产部署时，四个子包不会共享同一个运行权限；代码分目录只是 repo 层面的组织方式。

### `environments/`

保存三个隔离环境的依赖与容器定义。Authoring/verifier 可以安装固定版本的金融包，Solver 环境只安装 allowlist 依赖，并禁用网络、动态安装和 hidden verifier 访问。

### `datasets/`

`generated/` 存放可重建的训练集，因此被 `.gitignore` 排除；`manifests/` 存放应提交的版本信息、数据来源、snapshot grouping、train/validation/test split 和内容 hash。训练集不得包含 hidden oracle、hidden tests 或 verifier 私有输出。

### `examples/`

保存能够公开审阅的小型产物：canonical submission、正向 trajectory、first-error 负轨迹等。示例用于解释合同，不能被生产 hidden verifier 当作唯一 oracle 来源。

### `tests/`

- `fixtures/` 提供稳定的小型输入。
- `public/` 检查公开 schema、snapshot identity、method contract 和端到端接口。
- `unit/` 检查 task grammar、受约束 mutation、lineage 与 curriculum sampling。
- `integration/` 检查 task manifest 仅按 id/hash 引用 authoring snapshot。
- `verifier_robustness/` 主动修改末位数字、单位、method ID、行顺序或 import，确认 hard verifier 必须失败；该目录与正式 task mutation engine 无关。

生产 hidden tests 应放在 Solver 无法读取的独立环境中，不提交到公开仓库。

### `scripts/` 与 `runs/`

`scripts/` 提供统一、非交互的项目入口；`runs/` 只保存本地临时运行结果、日志和报告。`runs/` 中的内容可以删除并重新生成，不作为数据集或 oracle 的可信来源。

## 虚拟环境约定

本项目只在仓库本地 `.venv` 中运行。命令统一使用：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
```

`.venv/` 不提交 Git；可复现性由 Python 版本声明和锁定依赖文件保证。

## 设计文档

- [DuckDB + QuantLib Authoring Pipeline](docs/authoring_pipeline.md)
- [金融衍生品 Task Mutation 与 Curriculum 扩展](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum.md)
- [合成期权链 IV、Greeks 与 Smile Agent Trajectory 样例](docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)
