# BSM Market-Implied Greeks Task Package v2 返工说明

## 0. 任务定位

本文件是对 `synthetic-BSM-agent-task` 分支中以下 delivery 的定向返工说明：

```text
task_packages/deliveries/bsm_market_implied_greeks_v1/
└── 20260813_current_interface_100/
```

当前 delivery 含 100 个 `bsm_market_implied_greeks_v1` task，每个 task 有 160 条 option rows。此次工作只返工这一条 task family 的 solver interface、公开/私有边界和 portable delivery；不要扩张为整个项目重构。

### 必须遵守的范围

- 只处理 `bsm_market_implied_greeks_v1` task package。
- 不修改 F2A/arbitrage 任务。
- 不新增 pricing model、underlying dynamics、curriculum level 或 mutation axis。
- 不重构父数据库生成器、PSD correlation、P/Q parent snapshot 或其他无关模块。
- 不创建额外的项目副本、sandbox 副本、整仓 zip、原始数据导出或无关 dataset。
- 不逐个手工修改现有 100 个 delivery task；应修改 source generator，再统一重新 materialize。
- 不覆盖或原地修改现有 immutable delivery `20260813_current_interface_100`。
- 先完成 1–2 条 task 的 smoke build 和验证，再生成新的 100-task delivery。

## 1. 当前实现的阻断性问题

### P0-1：solver-visible contract 公开了完整答案

当前文件：

```text
trusted_tools/payloads/contract.json
```

以及公开 DuckDB relation：

```text
solver_visible.greeks_task_contract.contract_json
```

包含以下内容：

- BSM call/put pricing formulas；
- `d1`、`d2` formulas；
- normal CDF/PDF formulas；
- Delta、Gamma、Vega、Theta、Rho formulas；
- scaling formulas；
- exact floating-point operation order；
- 可直接翻译为 reference solver 的中间变量序列。

这使任务从“模型独立完成 BSM inversion 与 Greeks”退化为“把 JSON 伪代码翻译为 Python”。这是 task-solution leakage，必须删除。

### P0-2：公开 prompt 不是 task prompt

当前 `public/prompt.md` 只有一个 router：

```text
Call query_greeks_task_contract_v1 ...
Treat returned data ... as the complete authoritative specification.
```

它没有直接告诉 solver：

- 要计算哪些输出；
- market-implied Greeks 的含义；
- 各 Greek 的单位；
- option/underlying 数据如何获得和匹配；
- IV inversion 方法；
- allowed/forbidden packages；
- canonical output 与提交规则。

Prompt 应当是完整、独立、人类可读的任务说明，而不是指向一份公式答案 JSON 的路由器。

### P0-3：当前任务并未真正保留 option/underlying 数据获取结构

`query_greeks_task_inputs_v1` 当前只是返回一个预先 export 的、已经 join 好的 160-row JSON array。与此同时：

- `public/task.duckdb` 被标为 agent-visible；
- runtime 又禁止 raw DuckDB access；
- portable tool 实际不 query DuckDB，只对 `inputs.json` 做 `deepcopy`。

因此当前任务既没有让 solver 分别获取 option quotes 与 underlying quotes，也没有保留明确的 join 语义。

### P0-4：delivery 的“隐藏”目前主要依赖标签

每个 portable task 的同一个物理目录同时包含：

```text
public/
trusted_tools/
verifier/
```

`delivery_manifest.json` 仅将它们标记为 `agent_visible`、`tool_host_only`、`verifier_only`。这不是实际 sandbox 隔离。README 也明确说明 sandbox 本身不属于 delivery。

对于可能忽略 prompt allowlist、尝试遍历工作目录的 solver，不能只依靠 visibility label。必须生成一个物理上只含 solver-visible files 的 evaluation view，并确保实际 rollout 只 mount 该 view。

### P1-1：现有 leakage scanner 没有识别 mathematical answer leakage

当前 scanner 主要拦截：

```text
sampling_seed
generator_seed
canonical_answer
oracle_answer
authoring_private/
reference/trajectory
```

它只检查 submission schema 中是否出现 `d1`/`d2` 字段，却没有检查 query response 或 DuckDB contract JSON 中的 formulas。因此当前所谓 leakage-clean 并不表示 task solution 没有泄露。

### P1-2：错误架构已被测试固定

当前 `test_bsm_greeks_prompt_runtime_drift.py` 明确断言 prompt 中不应出现：

- exactly 80；
- Greek units；
- `ROUND_HALF_EVEN`；
- allowed imports；
- denied imports；
- row-order requirements。

这些断言应被反转：prompt 必须包含必要的任务语义，但不得包含 BSM/Greeks closed-form formulas。

## 2. 必须保留的正确部分

以下部分原则上不应推倒重来：

- 当前 frozen parent snapshot 和已生成的 market quotes；
- 每条 task 的 8 underlyings × 2 expiries × 5 strikes × call/put，即 160 rows；
- 100 条 task 的 selection/data variation；
- 所有 task 共享 parent 时采用 `unsplit_shared_parent_snapshot`；
- 从公开 bid/ask midpoint 恢复 market-implied volatility；
- European BSM、flat continuously compounded risk-free/dividend curves；
- `Actual365Fixed`；
- 固定 80 次 bisection，volatility bracket `[1e-6, 5.0]`；
- 使用 recovered market IV 计算 analytic unit Greeks；
- QuantLib 1.39 仅用于 trusted verifier；
- verifier 从公开 quotes 独立重算，不读取 generator latent IV 或 private answer；
- canonical 8-decimal `ROUND_HALF_EVEN` strings；
- verifier 使用 exact canonical equality，不引入 numerical tolerance；
- binary all-tests-pass reward；
- reference trajectory 只记录 observable execution，不包含 hidden chain-of-thought；
- reference/oracle/private artifacts 不进入 evaluation view。

## 3. 数学与数值语义：private authoritative invariants

以下语义必须由 private reference implementation、trusted verifier 和 build-time tests 保持一致。它们可以存在于 authoring/private verifier source 中，但不得以公式或伪代码形式出现在 solver-visible files 或 query responses 中。

### 3.1 Market price 与 implied volatility

- Observed option price 使用 public bid/ask midpoint。
- 必须先以 `Decimal` 计算 midpoint，再进行一次 binary64 cast。
- IV 是由该 visible midpoint 反演得到的 annualized decimal volatility。
- IV 属于当前 quote-level、Q-measure BSM inversion。
- 禁止使用 P-measure historical drift/diffusion、generator latent volatility、private nodes 或 audit truth 替代 IV。
- Bisection bracket 固定为 `[1e-6, 5.0]`。
- 固定执行 80 次 updates；不得 early-stop。
- 不得改变 root-finding algorithm、bounds、comparison rule 或 final-root rule。
- 不得回归 `d1`、`d2` 或其他 pricing terms。
- 不得使用 fallback solver。

### 3.2 Unit Greeks

所有输出均为 one-unit option Greeks，不应用 `contract_multiplier`：

- `unit_delta`：每 `+1.00` spot unit 的 option-value 一阶变化率。
- `unit_gamma`：spot 每变化 `+1.00` 时 Delta 的变化率。
- `unit_vega_1volpt`：absolute volatility 增加 `0.01` 时的 option-value 变化。
- `unit_theta_1calendar_day`：expiry 固定、calendar time 向前经过一天时的 option-value 变化；与 QuantLib annual theta 除以 365 的语义一致。
- `unit_rho_1pct`：continuously compounded risk-free rate 增加 `0.01` 时的 option-value 变化。

Greeks 必须使用未舍入的、80 次 bisection 后的 recovered IV 计算。不得先 round IV 再计算 Greeks。

### 3.3 Canonicalization

- Solver arithmetic 为 IEEE-754 binary64，除明确要求的 Decimal midpoint/canonicalization 外。
- 最终各 numeric fields 输出为恰好 8 位小数的 strings。
- 使用 `ROUND_HALF_EVEN`。
- 禁止 `-0.00000000`，zero 必须 canonicalize 为 positive zero。
- 中间结果不得提前 rounding。
- Verifier 继续使用 exact string equality；不得加入 `isclose`、`allclose`、`atol`、`rtol` 或任何 tolerance API。

## 4. v2 目标 solver workflow

Solver 应看到并执行以下清晰流程：

1. 阅读一份完整的 `public/prompt.md`。
2. 调用一次 underlying-market query，获得 public spot 与 pricing context。
3. 调用一次 option-quote query，获得 public option contracts、bid 与 ask。
4. 按 public keys 匹配 option row 与 underlying-market row。
5. 对每个 option row 计算 public quote midpoint。
6. 自行实现 European BSM pricing。
7. 按指定固定 bisection contract 恢复 market-implied IV。
8. 自行实现 analytic BSM unit Greeks。
9. 按 schema 和 canonicalization rules 形成完整 submission。
10. 调用一次 submission tool。

Solver 必须依靠其自身的 BSM 知识完成第 6 和第 8 步；任务不得向其提供 pricing 或 Greek formulas。

## 5. v2 public data/query interface

### 5.1 删除 formula-contract query

完全删除：

```text
query_greeks_task_contract_v1
trusted_tools/payloads/contract.json
solver_visible.greeks_task_contract
```

不要将其改名后继续返回相同内容，也不要把 formulas 移入 prompt、schema、runtime contract、tool descriptions、error messages 或 README。

### 5.2 使用两个只返回数据的 query tools

建议冻结为：

```text
query_greeks_underlying_market_v2
query_greeks_option_quotes_v2
submit_greeks_submission_v2
```

每个 query 最多且必须调用一次；submission 最多且必须调用一次。

#### Underlying-market rows

至少返回：

```text
task_id
snapshot_id
valuation_date
underlying_id
spot
currency
risk_free_rate
dividend_yield
calendar
day_count
```

#### Option-quote rows

至少返回：

```text
row_id
task_id
snapshot_id
valuation_date
underlying_id
option_id
call_put
strike
expiry
time_to_expiry_actual365
bid
ask
contract_multiplier
exercise_style
settlement_type
```

匹配 key 必须在 prompt 中明确，至少包括：

```text
task_id + snapshot_id + valuation_date + underlying_id
```

Option rows 必须以 canonical submission order 返回。Solver 在 join 后必须保持该 row order。

### 5.3 Public/task database relations

将 task DuckDB 的三张 relation 改为：

```text
metadata.public_task
solver_visible.underlying_market_inputs
solver_visible.option_quote_inputs
```

数据库中不得再保存 solver-visible mathematical contract JSON。任何 public/tool-returned relation 都不得包含：

```text
latent_iv
generator_sigma
private_diffusion
market_implied_volatility
delta
gamma
vega
theta
rho
d1
d2
canonical_answer
oracle_answer
reference_answer
```

`public/task.duckdb` 应由 tool host 读取，而不是直接 mount 给 solver。Solver 通过 trusted query tools 获得 allowlisted rows。

## 6. v2 public prompt：必须按此结构渲染

下面是目标 prompt 文案。允许为 schema/tool version 做机械性名称调整，但不得删除任务语义，也不得加入 BSM/Greek closed-form formulas。

```markdown
# Task: Market-implied BSM unit Greeks

For every public option quote, recover its market-implied volatility and compute the following Black–Scholes–Merton unit Greeks:

- `market_implied_volatility`
- `unit_delta`
- `unit_gamma`
- `unit_vega_1volpt`
- `unit_theta_1calendar_day`
- `unit_rho_1pct`

Return one result for every option row. Outputs are for one unit of the option; do not apply the contract multiplier.

## Public data

Call `query_greeks_underlying_market_v2` exactly once to obtain the public underlying spots and pricing contexts.

Call `query_greeks_option_quotes_v2` exactly once to obtain the public option contracts and bid/ask quotes.

Match each option row to its underlying-market row using `task_id`, `snapshot_id`, `valuation_date`, and `underlying_id`. Preserve the option-query row order in the submission.

Use only the returned public data. Do not access the raw database or any verifier, reference, private, audit, parent, or generator artifact.

## Model and numerical conventions

- Use the European Black–Scholes–Merton model.
- Treat the supplied risk-free rate and dividend yield as annualized continuously compounded decimals.
- Use `Actual365Fixed` time to expiry.
- Compute the observed option price from the bid/ask midpoint using Decimal arithmetic, then cast that midpoint once to binary64 for model calculations.
- Recover annualized market-implied volatility as a decimal using exactly 80 bisection updates on the closed volatility bracket `[1e-6, 5.0]`.
- Do not stop early, change the bracket, use a fallback, use another root finder, or regress pricing terms.
- Compute analytic BSM Greeks using the unrounded implied volatility obtained after all 80 updates.
- This is a quote-level Q-measure market-implied task. Do not use historical or P-measure drift/diffusion, hidden latent volatility, or private generator values.
- Implement BSM pricing, implied-volatility inversion, and analytic Greeks yourself. No pricing or Greek formulas are provided by the task.

## Units

- Implied volatility is annualized and expressed as a decimal; `0.20` means 20%.
- Delta is reported per `+1.00` change in spot.
- Gamma is reported as the Delta change per `+1.00` change in spot.
- Vega is reported for a `+0.01` absolute change in volatility.
- Theta is reported for one calendar day passing with expiry fixed.
- Rho is reported for a `+0.01` absolute change in the continuously compounded risk-free rate.

## Allowed and forbidden resources

Allowed direct imports:

`dataclasses`, `datetime`, `decimal`, `hashlib`, `itertools`, `json`, `math`, `typing`

Forbidden imports and shortcuts include:

`QuantLib`, `duckdb`, `mibian`, `numpy`, `pandas`, `py_vollib`, `rateslib`, `requests`, `scipy`, `socket`, `subprocess`, and `urllib`.

Do not use a package function that directly computes BSM prices, implied volatility, or Greeks. Do not use network access, package installation, dynamic imports, process spawning, undeclared filesystem access, raw database access, or hidden verifier/reference/private files.

## Output and submission

The final object must match `public/submission.schema.json` exactly.

- Include every public option row exactly once.
- Preserve the queried option row order.
- Do not add extra fields or commentary.
- Do not round intermediate values.
- Canonical numeric outputs must be decimal strings with exactly 8 digits after the decimal point, using `ROUND_HALF_EVEN`.
- Canonicalize negative zero to `0.00000000`.

Call `submit_greeks_submission_v2` exactly once with the complete submission.
```

### Prompt source-of-truth rule

- `prompt.md` owns mathematical task instructions and units。
- `runtime_contract.json` only owns permissions, tools and resource budgets。
- `submission.schema.json` only owns output shape and syntax。
- Data query responses only own public input values and ordering。
- Private verifier config only owns trusted oracle implementation details。

不得再声明一份含 formulas 的“authoritative public method contract”。

## 7. Allowed/forbidden resource contract

保留当前 stdlib-oriented solver environment，但同步修改 tool schedule：

```text
query_greeks_underlying_market_v2: 1
query_greeks_option_quotes_v2: 1
submit_greeks_submission_v2: 1
```

保留：

- Python 3.12；
- network disabled；
- dynamic installation disabled；
- process spawning disabled；
- raw DB connection disabled；
- verifier/reference/private access disabled；
- QuantLib、DuckDB、NumPy、Pandas、SciPy 与 finance shortcut packages 禁止进入 solver。

Runtime contract 中只列权限与 budget，不得包含 BSM formulas、Greek formulas、operation order 或 reference implementation hints。

## 8. Trusted verifier 与 hard verification

### 8.1 保持 private QuantLib oracle

Trusted verifier 继续：

- 使用 pinned `QuantLib==1.39`；
- 从 public quote data 独立重建 observed midpoint；
- 执行固定 80-step IV inversion；
- 使用 `QuantLib.AnalyticEuropeanEngine` 计算 Greeks；
- 按相同单位与 canonicalization 输出；
- 对完整 submission 做 exact canonical equality。

Verifier 不得读取 generator latent volatility 或 packaged reference answer。

### 8.2 不得为了 exact equality 公开 operation order

当前实现通过向 solver 公开 exact formula 与 operation order 来减少 floating-point drift，这不可接受。

正确处理方式是在 build-time publication gate 中保证数学等价实现经过 canonicalization 后一致：

1. 保留 stdlib manual implementation 与 QuantLib implementation 的独立 cross-check。
2. 对 IV 和五个 Greeks 分别检查两种实现 canonicalize 后完全相同。
3. 将接近 8-decimal rounding half-boundary 的 rows 从 publication candidate 中剔除，而不是只排除 exact tie。
4. 使用明确的 private rounding-boundary guard，例如对距离任一 half-quantum boundary 小于等于 `1e-11` 的 unrounded output 拒绝发布。
5. Build gate 失败时重新选择/生成 row；不得把 operation order 暴露给 solver 作为补救。

Verifier 本身仍然是无 tolerance 的 exact verifier；guard band 仅属于 private data publication gate。

## 9. 物理可见性与 delivery layout

### 9.1 Solver evaluation view

实际交给 solver 的物理目录只能包含：

```text
manifest.json
public/prompt.md
public/runtime_contract.json
public/submission.schema.json
```

如果接收平台需要其他 public tool descriptors，它们只能描述 tool name、argument schema 和 call budget，不得包含 payload path、host filesystem path、formulas 或 verifier details。

### 9.2 Tool-host bundle

仅供 trusted host 使用：

```text
task.duckdb
trusted_tools/toolset.json
trusted_tools/payloads/underlyings.json
trusted_tools/payloads/options.json
```

如果 host 可以直接 read-only query DuckDB，则可以不复制 JSON payload；无论哪种实现，solver 只能获得 allowlisted returned rows。

### 9.3 Verifier bundle

仅供 verifier runner 使用：

```text
verifier/README.md
verifier/requirements.lock
verifier/oracle_config.json
verifier/runtime.py
verifier/test_*.py
```

### 9.4 Authoring/train-dev only

以下内容绝不能进入 evaluation view：

```text
reference_solver.py
reference/final_submission.json
reference/trajectory.jsonl
authoring_private/oracle_answer.json
authoring_private/*
llm_solutions/*
verifier/*
trusted tool payload files
```

不要把 outer delivery root 本身当作 solver sandbox root。必须有测试证明 evaluation view 的真实 file tree 只包含 allowlisted files。

## 10. Leakage gate v2

Leakage scan 必须覆盖：

- `public/prompt.md`；
- `public/runtime_contract.json`；
- `public/submission.schema.json`；
- 所有 solver-visible DuckDB relations；
- 所有 trusted query 的实际返回 payload；
- tool descriptions 与 error messages；
- evaluation view 的完整 file tree。

### 必须拒绝的 formula/solution indicators

至少检查以下 keys/tokens；允许在 verifier/authoring-private source 中出现，但不允许在任何 solver-observable content 中出现：

```text
analytic_operation_formulas
analytic_operation_sequence
operation_order
d1 =
d2 =
cdf_d1
cdf_d2
cdf_minus_d1
cdf_minus_d2
discounted_spot *
discounted_strike *
diffusion_theta
vega_per_unit
rho_per_unit
normal_cdf formula
normal_pdf formula
reference_solver
canonical_answer
oracle_answer
latent_iv
generator_sigma
private_diffusion
```

不要简单禁止单词 `delta`、`gamma`、`vega`、`theta`、`rho`，因为 prompt 与 output schema 必须合法地命名目标字段；应禁止这些量的 closed-form expressions、precomputed input values 和 answer-bearing fields。

## 11. 必须修改的 source files

至少检查并按职责修改：

```text
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/prompt_renderer.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/contracts.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/database.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_tools.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_delivery.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/leakage.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/runtime.py
environments/solver/capabilities.bsm_greeks_v1.json
configs/task_packages/bsm_market_implied_greeks_v1.json
configs/variants/bsm_market_implied_greeks_v1.json
schemas/bsm-greeks-submission-v1.schema.json
task_packages/README.md
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_prompt_runtime_drift.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_package_contract.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_release_views.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_database_and_replay.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_negative_submissions.py
tests/integration/test_bsm_greeks_verifier.py
tests/unit/test_bsm_greeks_contract.py
```

`analytic_bsm_greeks_contract()` 或内部 formula-bearing structures 可以保留为 private authoring/verifier implementation detail，但 `market_greeks_method_contract()` 不得再把它们复制到 solver-visible DB/query payload。

## 12. Versioning 与 rematerialization

这次变更会改变 solver-observable interface，因此必须：

- bump solver-interface contract version；
- bump public task database schema version；
- bump portable toolset/host protocol version；
- bump runtime/tool names 与 submission tool version；
- 让 stable task ID 因新的 solver-interface digest 自动变化；
- 不复用现有 `bsm-mig-v1-*` IDs 假装 interface 未改变；
- 不覆盖 `20260813_current_interface_100`；
- 创建新的 delivery ID，例如：

```text
20260813_prompt_v2_100
```

如果 task ID prefix 由 schema/version policy 决定，也应升级为明确的 v2 identity，而不是仅修改文件内容。

另外，当前：

```text
configs/variants/bsm_market_implied_greeks_v1.json
```

中的 `package_path` 指向一个当前 branch 中不存在的旧路径。v2 应修正该 provenance/path，或删除不再可靠的 checked-in package pointer；不得保留 404 path 并声称 golden package 可定位。

## 13. Tests 与 acceptance criteria

### 13.1 Prompt tests

必须断言 prompt 包含：

- task objective；
- six output fields；
- option/underlying query workflow；
- join keys；
- European BSM；
- visible bid/ask midpoint；
- Q-measure market-implied IV；
- fixed 80-step bisection 与 bracket；
- five Greek unit conventions；
- no contract multiplier；
- allowed imports；
- forbidden packages/resources；
- exact output/canonicalization rules；
- query/submit tool schedule。

必须断言 prompt 不包含：

- BSM call/put pricing formula；
- `d1`/`d2` formula；
- any Greek closed-form formula；
- exact arithmetic operation sequence；
- oracle values；
- private/reference paths。

### 13.2 Query/data tests

- Underlying query 返回 exactly 8 canonical rows。
- Option query 返回 exactly 160 canonical rows。
- Every option row 唯一匹配一个 underlying-market row。
- Join 后仍为 160 rows，不丢失、不重复、不重排。
- Query payload 不含 formulas、latent values、Greeks 或 answer fields。
- Query tools each called exactly once；submission exactly once。

### 13.3 Database tests

- Public task DB relation allowlist exactly matches v2 schema。
- 不再存在 `solver_visible.greeks_task_contract`。
- 不存在 formula-bearing JSON column。
- 不存在 private/latent/oracle/reference fields。
- Parent DB bytes 保持不变。
- Child logical checksum 和 file digest 正确。

### 13.4 Verifier tests

- QuantLib 1.39 verifier 从 public data 独立重算。
- Reference stdlib solver 与 QuantLib canonical outputs 全部一致。
- IV 和 Greeks 使用 unrounded final IV。
- Unit scaling、theta sign、day-count、continuous compounding 全部一致。
- Exact valid submission passes。
- 一个 digit 修改、row reorder、missing row、duplicate row、extra field、wrong method、wrong scaling、contract-multiplied Greeks 均 fails。
- Verifier 不使用 tolerance comparisons。

### 13.5 Visibility/leakage tests

- Evaluation view file allowlist exact。
- Evaluation view 不含 `trusted_tools/`、`verifier/`、`reference/`、`authoring_private/`、`llm_solutions/`。
- 对所有实际 query responses 执行 semantic leakage scan。
- 公式 token mutation tests 必须被 scanner 拒绝。
- 仅靠 `artifact_visibility` 标签、但物理文件仍暴露的伪 evaluation view 必须测试失败。

### 13.6 Batch acceptance

先执行：

1. source/unit/integration tests；
2. 1-task smoke build；
3. 2-task portable delivery smoke；
4. reference replay ×2，要求 byte-identical；
5. solver-visible leakage scan；
6. trusted verifier full pass。

以上全部通过后，才生成 100-task v2 delivery，并验证：

- exactly 100 unique task IDs；
- each task exactly 8 underlyings and 160 option rows；
- all 100 prompts/runtime/schema blobs correctly shared where intended；
- all DB/input payloads unique according to their selected data；
- no formula contract file in any task；
- batch remains one unsplit shared-parent evaluation group；
- delivery status 不得在 isolation/leakage gate 失败时标记为 verified/released。

## 14. 完成交付物

只交付以下内容：

1. 上述 source changes；
2. 更新后的 focused tests；
3. 通过 smoke validation 后生成的新 v2 100-task delivery；
4. 一份简短 change report，列出：
   - changed files；
   - new task/delivery version IDs；
   - smoke/full test commands and results；
   - leakage scan result；
   - representative evaluation-view file listing。

不要交付：

- 整个项目的额外复制品；
- 新 sandbox 工程；
- 无关代码或数据；
- 新 curriculum；
- F2A 改动；
- 额外 training dataset；
- 未经请求的 GitHub issue/PR；
- 对现有 immutable delivery 的原地覆盖。

## 15. Stop conditions

遇到以下情况时停止并报告，不得自行扩大 scope 或用弱化方案冒充完成：

- 接收平台无法把 solver evaluation view 与 tool-host/verifier bundle 物理隔离；
- 修改 prompt 后仍必须向 solver 公开 exact formulas 才能通过 verifier；
- v2 query interface 需要改变 task mathematical semantics；
- 无法在不读取 private/latent data 的前提下由 verifier 重算答案；
- source reference implementation 与 QuantLib 在 canonical outputs 上不一致；
- 100-task build 前 smoke validation 未全部通过。

## 16. Definition of Done

只有以下条件全部成立时才算完成：

- [ ] Prompt 独立、清晰地说明任务、单位、数据流程、权限与输出。
- [ ] Solver-visible/query-returned content 中不存在 BSM/Greek closed-form formulas。
- [ ] `query_greeks_task_contract_v1` 与 formula contract relation 已删除。
- [ ] Option 与 underlying public data 被明确分离并通过 trusted tools 获取。
- [ ] Solver 无法看到 raw DB、verifier、reference、private 或 tool-host payload files。
- [ ] QuantLib verifier 仍从 public quotes 独立重算并 exact-verify。
- [ ] 正确 solver 无需已公开 operation order，也能在 canonical outputs 上稳定通过。
- [ ] Leakage scanner 能拒绝公式、oracle、latent 和 hidden-path mutations。
- [ ] 新 solver interface 使用新版本与新 task IDs。
- [ ] 旧 `20260813_current_interface_100` 未被覆盖。
- [ ] 1-task、2-task 和 100-task validation 均通过。
- [ ] 没有无关项目重构或额外交付物。

