# BSM Market-Implied Greeks Agent Tasks

本目录是一组可独立运行和验收的 agent task。当前交付批次为
`20260813_prompt_v2_100/`，包含 100 个 `bsm-mig-v2-*` 任务实例。每个实例的目标相同：
从公开的标的市场信息和欧式期权 bid/ask 报价中反推出市场隐含波动率，并计算单位期权的
Delta、Gamma、Vega、Theta 和 Rho。

这些任务是运行时相互隔离的评测实例。处理新实例时，应重新读取该实例的公开数据和 `task_id`，
不能沿用其他实例的输入、输出或中间结果。

## 目录与可见性

批次入口是 `20260813_prompt_v2_100/batch_manifest.json`，其中列出了任务数量、任务 ID
及每个实例的相对路径。单个任务位于：

```text
20260813_prompt_v2_100/tasks/<task_id>/
├── evaluation_view/
│   ├── manifest.json
│   └── public/
│       ├── prompt.md
│       ├── runtime_contract.json
│       └── submission.schema.json
├── trusted_tools/
│   ├── toolset.json
│   └── payloads/
├── verifier/
├── task.duckdb
├── delivery_manifest.json
└── source_manifest.json
```

各部分应严格按以下边界使用：

| 路径 | 使用者 | 用途 |
| --- | --- | --- |
| `evaluation_view/` | agent | 唯一允许挂载给 agent 的文件视图，包含题目、运行限制和提交 schema |
| `trusted_tools/` | 工具宿主 | 定义三个受信工具，并保存查询工具返回的公开 payload；不可作为文件暴露给 agent |
| `task.duckdb` | 工具宿主、verifier | 宿主侧任务数据；agent 不得直接连接或读取 |
| `verifier/` | 评测方 | 对提交做可信验收；不可暴露给 agent，且不属于 solver 依赖 |
| 两个 source/delivery manifest | 交付与审计方 | 描述任务身份、版本、文件角色和交付状态 |

不要把整个 `<task_id>/` 目录直接挂载给 agent。agent 只能看到 `evaluation_view/`，公开数据则
必须通过受信查询工具取得。

## 如何运行一个任务

### 1. 选择任务

从 `batch_manifest.json` 的 `tasks` 数组选择一个 `task_id`，并以对应目录作为该次运行的
task root。每个任务都应启动一次独立的 agent 会话。

### 2. 配置 agent 环境

工具宿主读取 `trusted_tools/toolset.json`，按其中的
`static-json-query-schema-submit-v2` 协议注册以下工具：

- `query_greeks_underlying_market_v2`：返回标的 spot、估值日、无风险利率、连续股息率等公开定价上下文；
- `query_greeks_option_quotes_v2`：返回期权合约、bid/ask 和 `row_id` 等公开报价；
- `submit_greeks_submission_v2`：按公开 JSON Schema 接收完整结果。

将 `evaluation_view/` 作为 agent 唯一可读视图，并执行
`public/runtime_contract.json` 中的资源与能力限制。当前 solver 环境为 Python 3.12、无网络、
不可安装包或启动子进程，只允许题目列出的标准库。评测平台需要提供上述工具宿主；本交付包
本身不包含通用的一键 agent runner。

### 3. 让 agent 完成任务

agent 应首先完整阅读 `public/prompt.md`、`public/runtime_contract.json` 和
`public/submission.schema.json`，然后严格按顺序：

1. 调用一次 `query_greeks_underlying_market_v2`；
2. 调用一次 `query_greeks_option_quotes_v2`；
3. 按 `task_id`、`snapshot_id`、`valuation_date`、`underlying_id` 连接两组数据；
4. 对每个期权报价计算隐含波动率和五个单位 Greek；
5. 保持期权查询的原始行序，以一次 `submit_greeks_submission_v2` 调用提交全部结果。

两个查询工具和提交工具都只能调用一次。agent 必须自行实现 BSM 定价、隐含波动率求解与
解析 Greeks，不能调用 QuantLib、SciPy、NumPy 或其他定价函数，也不能读取宿主侧文件。

### 4. 提交格式

最终对象必须精确满足 `public/submission.schema.json`。顶层关键常量为：

```json
{
  "task_id": "<当前任务 ID>",
  "submission_schema_version": "bsm-market-implied-greeks-submission-v2.0.0",
  "method_id": "bsm-mid-iv-bisection80-analytic-greeks-v1",
  "status": "completed",
  "rows": []
}
```

上例仅展示顶层结构，空的 `rows` 不能用于正式提交。每个公开期权行必须恰好对应一个结果行，
字段名、行序要求和数值格式以该任务的 prompt 与 schema 为准。尤其要注意：

- 结果是单份期权的单位 Greek，不乘 `contract_multiplier`；
- 数值以带 8 位小数的字符串提交，使用 `ROUND_HALF_EVEN`；
- 不得输出负零、额外字段或解释文字；
- 计算 Greek 时使用完整精度的隐含波动率，不能先把隐含波动率舍入。

## 本地验收提交

工具宿主应将提交调用收到的完整 JSON 保存为文件。评测方可在相应 task root 下建立独立的
verifier 环境并运行：

```bash
python3 -m venv /tmp/bsm-greeks-verifier-env
/tmp/bsm-greeks-verifier-env/bin/python -m pip install -r verifier/requirements.lock
BSM_GREEKS_SUBMISSION=/absolute/path/to/submission.json \
  /tmp/bsm-greeks-verifier-env/bin/python -B -m pytest -q verifier
```

测试全部通过即表示该提交满足任务契约和数值语义。`verifier/requirements.lock` 中的依赖只供
可信评测环境使用；其中即使包含 solver 禁用的库，也不能因此把这些库加入 agent 环境。

## 数学模型概览

下面只描述理解和完成任务所需的公开数学对象，不描述数据合成流程、参数抽样方式或参考答案
的构造方法。

任务使用带连续股息率的欧式 Black–Scholes–Merton（BSM）模型。对每个报价，在估值时点已知
现货价 `S`、执行价 `K`、到期日、期权方向、年化连续复利无风险利率 `r`、年化连续股息率
`q` 和 bid/ask 报价。时间采用 `Actual365Fixed` 年份单位，波动率的单位是每平方根年。

定价位于与货币市场账户 numeraire 对应的风险中性测度 `Q` 下。BSM 的除息现货过程写作：

```text
dS_t / S_t = (r - q) dt + sigma dW_t^Q
```

这里的 `sigma` 不是历史波动率或真实世界测度 `P` 下的状态，而是从当前期权报价反解出的
quote-level 市场隐含波动率。任务不模拟标的路径；它使用欧式 BSM 解析定价关系，因此没有
Monte Carlo 时间离散误差。

每个期权的模型输入价格先由 bid/ask 通过十进制算术取中点，再一次性转为 binary64。市场
隐含波动率 `sigma_IV` 是使 BSM 模型价格等于该可见中点的解：

```text
BSMPrice(S, K, T, r, q, sigma_IV, call_or_put) = (bid + ask) / 2
```

在本任务的有效 BSM 域内，价格随正波动率单调增加。为使不同实现得到同一数值身份，任务
固定在闭区间 `[1e-6, 5.0]` 上执行恰好 80 次二分更新，不允许提前停止或更换求根方法。

五个 Greek 是模型价格对相应输入的一阶或二阶局部敏感度：

- Delta：spot 增加 `1.00` 时的单位价格敏感度；
- Gamma：spot 增加 `1.00` 时 Delta 的变化率；
- Vega：波动率绝对增加 `0.01`（一个 vol point）时的单位价格敏感度；
- Theta：到期日固定、估值时间向前经过一个日历日时的单位价格敏感度；
- Rho：连续复利无风险利率绝对增加 `0.01` 时的单位价格敏感度。

这些量都在反解得到、尚未舍入的 `sigma_IV` 上按解析 BSM 模型计算，最后才统一规范化为
8 位小数字符串。

## 常见失败原因

- 把历史波动率、物理测度漂移或某个隐藏波动率当作隐含波动率；
- 用 binary64 直接计算 bid/ask 中点，而不是先做十进制中点计算；
- 改用 Newton、第三方 IV solver、提前停止的二分法或不同的波动率区间；
- 在计算 Greeks 之前舍入隐含波动率；
- 乘上合约乘数，或改变、遗漏、重复期权行；
- 提交 JSON 数字而非固定 8 位小数的字符串；
- 把 `trusted_tools/`、`task.duckdb` 或 `verifier/` 暴露给 agent。

## 披露边界

本 README 只说明公开任务契约、运行方式和必要的 BSM 背景。它有意不说明源数据分布、随机
种子、场景选择、参数生成、报价或 spread 的合成规则、隐藏状态、参考解生成、筛选标准以及
作者侧流水线。上述内容不是使用或验收这些 agent task 所必需的接口信息。
