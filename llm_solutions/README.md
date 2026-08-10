# External LLM solver runner

这个目录用于让外部 LLM API 为
`runs/bsm_market_implied_greeks` 中的任务生成一个通用 Python solver，并在仓库已有的
受限 runtime 中回放。外部 LLM 只会收到每个 package 的三个公开文件：

- `public/prompt.md`
- `public/runtime_contract.json`
- `public/submission.schema.json`

程序不会把 `reference/`、`verifier/`、`authoring_private/` 或 dataset 中的 `Outcome`
发送给 API。LLM 生成一次 `solve(tools)` 后，同一份代码会复用于所选的全部 task；每个
task 的 160 行公开输入只在本地受限 runtime 中通过 trusted tools 注入。

## 数学与数值契约

任务在 USD money-market numeraire 对应的共同风险中性测度 `Q` 下，对估值日已知的
`S, K, T, r, q` 条件定价。`T` 使用 Actual/365 Fixed 日历时间，`r/q` 是年化连续复利率，
`sigma_IV` 是年化瞬时收益波动率。European BSM 使用解析价格和 Greeks，没有路径
离散化。可见 bid/ask 先按题面规定构造 Decimal midpoint，再 cast 为 binary64，固定做
80 次二分；输出按 `Decimal(str(binary64))` 和 `ROUND_HALF_EVEN` 量化到 8 位小数。

## 配置 API

接口采用 OpenAI-compatible Chat Completions 请求格式。`LLM_API_URL` 必须是完整 endpoint，
例如服务商给出的 `.../v1/chat/completions`。密钥只从环境变量读取，不会写入输出文件：

```bash
export LLM_API_URL='https://your-provider.example/v1/chat/completions'
export LLM_API_KEY='your-secret-key'
export LLM_MODEL='your-model-name'
```

如果本地服务不需要鉴权，可以不设置 `LLM_API_KEY`。也可以用 `--api-key-env` 指定另一个
密钥环境变量名。

## 运行

先用一个 task 做完整冒烟测试：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root runs/bsm_market_implied_greeks/20260810_100_tasks_parent_seed_20260806_selector_seed_0 \
  --limit 1 \
  --output-dir /tmp/bsm-llm-smoke
```

确认通过后运行全部 100 个 task：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root runs/bsm_market_implied_greeks/20260810_100_tasks_parent_seed_20260806_selector_seed_0 \
  --output-dir /tmp/bsm-llm-all
```

默认执行两层检查：公开 runtime/schema 检查，以及使用仓库 pinned QuantLib 的 exact trusted
verification。后者只在本地运行，任何 oracle 数值都不会发给 LLM。若要模拟严格 evaluation
边界、只做公开检查，可加 `--skip-trusted-verification`。

API 返回的候选源码、公开请求和每次失败原因保存在 output directory。候选失败时程序最多
重新请求 `--generation-attempts` 次；可信校验失败只会反馈通用的公式/单位/舍入错误提示，
不会泄露期望答案。

已经生成过 solver 时，可以跳过 API 并复验或继续跑其它 task：

```bash
.venv/bin/python llm_solutions/run_bsm_market_implied_greeks.py \
  --run-root runs/bsm_market_implied_greeks/20260810_100_tasks_parent_seed_20260806_selector_seed_0 \
  --solver-source /tmp/bsm-llm-smoke/solver.py \
  --output-dir /tmp/bsm-llm-replay
```

结果目录包含：

- `solver.py`：最终通过 pilot 的通用 solver；
- `submissions/<task_id>/submission.json`：各题 canonical submission；
- `run_summary.json`：task 数量、状态和本地验证结果；
- `generation_attempts/`：API 原始响应、提取出的源码及每次 pilot 结果。

脚本拒绝覆盖已有 output directory，避免误删或混合不同运行的结果。
