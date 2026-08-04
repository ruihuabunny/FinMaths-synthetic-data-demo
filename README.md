# FinMaths Synthetic Data Demo

面向 LLM 训练的确定性合成金融衍生品数据项目。项目目标是把市场数据生成、任务定义、模型解题、独立校验和训练数据导出分成清晰的边界，并保证每个样本都可以通过固定配置与 seed 重放。

当前仓库处于结构设计阶段：目录和设计文档已经就位，生成器、solver、verifier 与训练数据构建代码尚未实现。

## 设计流程

```text
Authoring
  -> 生成并冻结 market snapshot
  -> 发布 task variant 与 method contract
  -> Solver 手工计算 IV / Greeks / smile
  -> Trusted verifier 独立复算并执行 exact equality
  -> 记录 ORM outcome 与 agent trajectory
  -> 导出 LLM training dataset
```

Authoring、Solver 和 Trusted verifier 是三个不同的权限边界：

- Authoring 可以使用固定版本的 QuantLib 和固定 seed 生成市场数据。
- Solver 只能读取公开快照与合同，不得调用现成的定价、IV、Greeks 或 smile API。
- Trusted verifier 可以使用固定版本的金融与数值包独立复算，但不能向 Solver 暴露 oracle 或 hidden tests。

## 仓库结构

```text
.
├── .venv/                         # 仓库本地 Python 虚拟环境，不提交 Git
├── authoring/
│   ├── configs/                   # Authoring job 的内部生成配置
│   └── templates/                 # 新 generator、snapshot 和 variant 的模板
├── configs/
│   ├── generators/                # 可发布的 generator 配置与版本声明
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
│   └── public/                    # 小型、冻结、可公开且带 hash 的市场快照
├── src/
│   └── synthetic_derivatives/
│       ├── authoring/             # 市场快照生成与冻结实现
│       ├── solver/                # 受限环境中的公式、求根、Greeks 与拟合实现
│       ├── training/              # Trajectory 清洗、split 和 LLM 数据导出实现
│       └── verifier/              # 独立 package oracle 与 hard verifier 实现
└── tests/
    ├── fixtures/                  # 小型冻结测试输入和公开期望结构
    ├── mutation/                  # 验证错误数字、方法和 schema 必须被拒绝
    └── public/                    # Snapshot、contract、重放和端到端公开测试
```

`.agents/` 与 `.codex/` 是本地协作工具使用的目录，不属于项目的数据生产接口。

## 各目录的职责

### `authoring/`

存放出题端的配置与模板，不放 Solver 可见的数据。后续 authoring pipeline 会用 pinned QuantLib、generator config、seed 和 RNG 生成 `underlying_daily`、`option_daily` 与 `pricing_metadata`，随后冻结并计算 hash。内部 audit 与 oracle 输出应保持私有。

### `configs/`

保存可复现行为所需的声明式配置：

- `generators/` 描述模型、参数、随机数生成器、draw order、定价 engine 和 generator version。
- `variants/` 描述某一道任务的 snapshot、金融约定、method IDs、数值顺序、舍入规则、Solver 权限和输出格式。

配置文件只描述合同，不存放实现代码或 hidden reference answer。

### `snapshots/`

保存生成后不可变的市场快照。`public/` 仅提交小型 demo；批量快照与私有快照不进入 Git。每个快照预计包含：

- `underlying_daily.csv`
- `option_daily.csv`
- `pricing_metadata.json`
- snapshot manifest 与 SHA-256 hash

IV、Greeks、smile、surface、VaR 和 ES 是从统一快照派生的任务结果，不在这里维护彼此独立的 truth tables。

### `schemas/`

定义跨边界的数据结构，包括 market snapshot、task variant、agent trajectory、Solver submission、verification report 和 dataset record。Schema 用于在进入下一阶段前拒绝缺字段、错单位、错顺序或非法数值。

### `src/synthetic_derivatives/`

项目的 Python 源码根目录，按权限拆为四个子包：

- `authoring/`：允许使用 QuantLib，负责生成、质量门控、冻结与 hash。
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
- `mutation/` 主动修改末位数字、单位、method ID、行顺序或 import，确认 hard verifier 必须失败。

生产 hidden tests 应放在 Solver 无法读取的独立环境中，不提交到公开仓库。

### `scripts/` 与 `runs/`

`scripts/` 将提供统一、非交互的项目入口；`runs/` 只保存本地临时运行结果、日志和报告。`runs/` 中的内容可以删除并重新生成，不作为数据集或 oracle 的可信来源。

## 虚拟环境约定

本项目只在仓库本地 `.venv` 中运行。实现阶段加入依赖锁文件后，命令将统一使用：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
```

`.venv/` 不提交 Git；可复现性由后续的 Python 版本声明和锁定依赖文件保证。

## 设计文档

- [金融衍生品确定性 ORM Hard-Verifier 框架](docs/financial_derivatives_deterministic_orm_hard_verifier_framework.md)
- [合成期权链 IV、Greeks 与 Smile Agent Trajectory 样例](docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)
