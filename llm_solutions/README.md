# External LLM direct-submission runner

这个目录让外部 LLM 按照 `runs/bsm_market_implied_greeks` 的公开要求直接提交答案。
LLM 不再返回 `solver.py`；它必须使用与题面同名的三个 trusted tools：

1. 调用 `query_greeks_task_contract_v1` 一次；
2. 调用 `query_greeks_task_inputs_v1` 一次；
3. 收到上述公开数据后，调用 `submit_greeks_submission_v1` 一次。

第三个 tool call 的 arguments 就是最终答案，会原样 canonicalize 后写入
`submissions/<task_id>/submission.json`。普通 assistant 文本、Markdown、Python 源码或没有
通过 submission tool 提交的 JSON 都会被拒绝。

## Submission 契约

最终对象必须严格包含：

```text
task_id
submission_schema_version = bsm-market-implied-greeks-submission-v1.0.0
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
重复行或改变公开行序。完整约束仍以每个 package 的 `public/prompt.md` 和
`public/submission.schema.json` 为准，它们会完整发送给 LLM。

数学上，这是 USD money-market numeraire 对应的共同风险中性测度 `Q` 下的 European BSM
逆问题。可见 bid/ask 先用 Decimal 构造 midpoint，再 cast 为 binary64；IV 必须固定执行
80 次二分，随后用未量化 IV 计算五个 unit Greeks，最后按 Decimal `ROUND_HALF_EVEN`
量化到 8 位。

## API 要求

接口使用 OpenAI-compatible Chat Completions tool-calling 协议。服务必须支持请求中的
`tools`、`tool_choice`，并在响应的 `message.tool_calls` 中返回 function call。

```bash
export LLM_API_URL='https://your-provider.example/v1/chat/completions'
export LLM_API_KEY='your-secret-key'
export LLM_MODEL='your-model-name'
```

密钥只从环境变量读取，不会写进结果目录。本地无鉴权服务可以不设置 `LLM_API_KEY`；
`--api-key-env` 可指定其它密钥变量名。

## 运行

建议先解一道题。每题包含 160 行，直接提交需要较大的 context/output budget：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root runs/bsm_market_implied_greeks/20260810_100_tasks_parent_seed_20260806_selector_seed_0 \
  --limit 1 \
  --output-dir llm_solutions/hy3_direct_sol
```

输出目录不存在或已经存在但为空时都可以运行；非空目录不会被覆盖。单题成功后，再换一个
新的输出目录运行完整批次：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root runs/bsm_market_implied_greeks/20260810_100_tasks_parent_seed_20260806_selector_seed_0 \
  --output-dir llm_solutions/hy3_direct_sol_all
```

默认最多为每题启动两次独立 LLM 尝试。第一次提交若只有数值不匹配，第二次只收到通用的
公式、单位、二分和舍入检查提示，不会获得 oracle 数值。

默认还会在本地用 pinned QuantLib exact verifier 检查完整 submission。这个 verifier 只读取
公开 task rows，不会把期望答案发给 LLM。`--skip-trusted-verification` 可以只检查公开
tool schedule 和 submission schema，但这不能证明数值正确。

结果目录包含：

- `submissions/<task_id>/submission.json`：LLM 的最终提交；
- `tasks/<task_id>/attempts/`：每轮 API 响应、公开 tool transcript 和尝试结果；
- `selection.json`：所选任务与 API model；
- `run_summary.json`：全部任务的提交和验证状态。

注意：普通聊天模型未必能可靠完成 160 行、binary64 固定迭代且精确到 8 位的数值计算。
runner 会严格拒绝近似但不相等的答案，不会为了让模型通过而放宽数学或 canonicalization
要求。
