# Hy3 Chat Completions coding runner

这个目录让腾讯混元 Hy3 为 BSM market-implied Greeks 的 source package 或 portable
delivery 编写并执行 `solver.py`。Hy3 通过一个外层 Agent 工具 `run_python_solver_v1`
提交完整源码；源码必须定义 `solve(tools)`，并在仓库已有的受限 solver harness 内使用
题面声明的三个 trusted tools：

1. 调用 `query_greeks_underlying_market_v2` 一次；
2. 调用 `query_greeks_option_quotes_v2` 一次；
3. 匹配公开数据并计算全部结果后，调用 `submit_greeks_submission_v2` 一次。

源码在执行过程中完成第三次 trusted tool call，最终对象会 canonicalize 后写入
`submissions/<task_id>/submission.json`。Hy3 不需要在模型输出中复制 160 行结果；普通
assistant 文本、Markdown 或未通过执行工具提交的源码都会被拒绝。

`run_python_solver_v1` 不是第四个任务 trusted tool。它是外层 Agent 执行器：先审计源码，
再在独立 `spawn` 进程中执行，并由原有 runtime contract 强制两次查询和一次提交。source
package 使用原有 DuckDB-backed trusted adapter；portable delivery 只使用包内
`trusted_tools/payloads/*.json` 构造 `PortableGreeksTools`，不会回退到 DuckDB adapter。
允许的 imports、CPU、内存、墙钟时间和 submission 大小都沿用公开任务契约；网络、动态安装、
子进程、原始数据库、verifier、reference 和私有文件不可用。

## Submission 契约

最终对象必须严格包含：

```text
task_id
submission_schema_version = bsm-market-implied-greeks-submission-v2.0.0
method_id = bsm-mid-iv-bisection80-analytic-greeks-v1
status = completed
rows
```

每个 row 必须严格包含：

```text
row_id
iv_status = CONVERGED_FIXED_ITERATIONS
market_implied_volatility
unit_delta
unit_gamma
unit_vega_1volpt
unit_theta_1calendar_day
unit_rho_1pct
```

所有数值答案必须是恰好 8 位小数的 JSON 字符串；不允许额外字段、负零、科学计数法、缺行、
重复行或改变公开行序。完整约束以每个 evaluation view 中的 `public/prompt.md`、
`public/submission.schema.json` 和 `public/runtime_contract.json` 为准，它们会完整发送给
LLM。Prompt 直接声明任务、数据匹配、数值方法、单位和规范化规则；query tools 只返回
公开输入数据，不返回公式或解题步骤。

数学上，这是 USD money-market numeraire 对应的共同风险中性测度 `Q` 下的 European BSM
逆问题。可见 bid/ask 先用 Decimal 构造 midpoint，再 cast 为 binary64；IV 必须固定执行
80 次二分，随后用未量化 IV 计算五个 unit Greeks，最后按 Decimal `ROUND_HALF_EVEN`
量化到 8 位。

Runner 的 system/user 文本用于指导模型写代码，不替代 package 的公开职责划分。源码仍
必须在受限 harness 中查询 underlying contexts 和 option quotes；runner 不把 repo-side
analytic solver、reference artifact 或 verifier source 注入 evaluation prompt。

## API 接入

runner 使用 TokenHub 的 OpenAI-compatible Chat Completions 协议，默认配置为：

```text
POST https://tokenhub.tencentmaas.com/v1/chat/completions
model = hy3
reasoning_effort = high
```

Chat 请求中的 function tool 使用标准嵌套结构：`tools[].function` 内包含 `name`、
`description` 和 `parameters`。Hy3 通过 `choices[0].message.tool_calls` 提交 solver source。
若源码审计、运行、schema 或精确数值验证失败，runner 会用 `role = tool` 返回不含 oracle
数值的错误，让 Hy3 修正源码后重试。每轮完整 assistant message（包括
`reasoning_content` 和 `tool_calls`）都会保留在后续 `messages` 中。

```bash
export TOKENHUB_API_KEY='your-secret-key'
```

密钥只从环境变量读取，不会写进结果目录。`--api-url` 和 `--model` 可覆盖默认端点与模型；
也可以分别设置 `TOKENHUB_API_URL` 和 `TOKENHUB_MODEL`。新加坡地域可把 URL 改为对应的
TokenHub `/v1/chat/completions` 端点。`--api-key-env` 可指定其它密钥变量名。runner 不依赖
Token Plan 可能未开放的 Responses API。

## 运行

`--run-root` 可以指向单个 source `manifest.json`、单个 portable
`delivery_manifest.json`、包含 manifests 的 batch，或其父目录；runner 会按 `task_id` 去重。
建议先用今天 portable delivery 的第一题做 smoke：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100 \
  --limit 1 \
  --output-dir llm_solutions/hy3_coding_sol
```

输出目录不存在或已经存在但为空时都可以运行；非空目录不会被覆盖。单题成功后，再换一个
新的输出目录运行你已经 materialize 并审核过的 prompt-v2 batch：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root /absolute/path/to/prompt-v2/packages \
  --output-dir llm_solutions/hy3_coding_sol_all
```

仓库本地 `runs/.../20260810_100_tasks_*` 使用前一版 verbose prompt/interface，仅用于历史
审计；它不会自动升级成当前 `solver_interface_digest` 身份或 `RELEASED` 数据集。

每次 LLM 尝试默认最多可执行四版 solver，失败信息会在同一 Chat 对话中回填；每题默认
最多启动两次独立 LLM 尝试。数值不匹配时只返回通用的公式、单位、二分和舍入检查提示，
不会获得 oracle 数值。

默认还会在本地用 pinned QuantLib exact verifier 检查完整 submission。portable task 的
verifier 输入来自同一组静态 `underlyings.json` 和 `options.json`，source task 则读取
host-side task DuckDB；两者都不会把
期望答案发给 LLM。`--skip-trusted-verification` 可以只检查公开 tool schedule 和
submission schema，但这不能证明数值正确。

结果目录包含：

- `submissions/<task_id>/submission.json`：LLM 的最终提交；
- `tasks/<task_id>/attempts/*/python_runs/`：每版生成源码、执行结果和候选 submission；
- `tasks/<task_id>/attempts/`：每轮 API 响应、完整 tool transcript 和尝试结果；
- `selection.json`：所选任务与 API model；
- `run_summary.json`：全部任务的提交和验证状态。

runner 会严格拒绝近似但不相等的答案，不会为了让模型通过而放宽数学、runtime policy 或
canonicalization 要求。
