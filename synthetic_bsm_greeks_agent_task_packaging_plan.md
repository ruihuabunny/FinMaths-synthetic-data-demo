# Synthetic BSM Greeks Agent Task Packaging Plan

## Database + Prompt + Runtime Contract + Pytest + Reference Agent Trajectory

- Repository: `ruihuabunny/FinMaths-synthetic-data-demo`
- Target branch: `synthetic-BSM-agent-task`
- Packaging target: `bsm_market_implied_greeks_v1`
- Supporting variant: `bsm_analytic_greeks_v1`
- Coordinates:
  - analytic: `(L1,P0,M0,A0,D1,R1,F0)`
  - market-implied: `(L5,P0,M0,A1,D4,R1,F0)`
- Design sources:
  - `docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md`
  - `docs/plans/synthetic_bsm_multi_asset_psd_duckdb_greeks_plan.md`
  - `docs/plans/synthetic_bsm_multi_asset_psd_duckdb_greeks_codex_checklist.md`
- Status (2026-08-12): Phase A–E complete；golden task
  `bsm-mig-v1-bde472c5cb0ca8a660314c9e` is `ACCEPTED`。Phase F runner and
  nine-field dataset exporter are implemented. A predecessor interface has a
  Git-ignored local 100-task run; the current minimal-prompt interface has not
  been rebuilt as a full batch and no artifact has been promoted to `RELEASED`.

## 1. Executive decision

本任务继续使用我们已经确定的四件套：

```text
database + prompt + pytest + reference agent trajectory
```

但正式可执行包必须再加入一等的 execution-environment contract 和 manifest。因此单条任务定义为：

\[
\widetilde{\mathcal T}_i=(M_i,D_i,P_i,E_i,V_i,\tau_i^\star),
\]

其中：

- `M_i`：public-safe manifest、版本和 hashes；
- `D_i`：独立、冻结、solver-visible 的 DuckDB；
- `P_i`：最小任务路由 prompt；它只把 Agent 引向公开 method contract、ordered inputs、
  submission schema 和 effective runtime contract，不重复这些 source 的内容；
- `E_i`：真实生效的 solver runtime contract；
- `V_i`：hidden `pytest` hard verifier；
- `τ_i*`：在同一 solver 权限下可 replay 的参考 trajectory。

第一条 golden package 已落地为 `bsm_market_implied_greeks_v1`：从公开 bid/ask midpoint
做固定 80 步 BS inversion，再输出 Delta、Gamma、Vega、Theta、Rho。
`bsm_analytic_greeks_v1` 是较小的 convention/solver acceptance fixture，用来快速区分
“Greek 公式错误”和“IV inversion 错误”，但不是主交付。

本任务不迁移 F2A 的 arbitrage mutation、X/U/T、certificate、FP/FN 或 executable-spread 语义。

## 2. Packaging scope and semantic boundary

### 2.1 Main task

主任务固定为：

```text
read public task contract and option rows
→ compute Decimal bid/ask midpoint
→ cast midpoint once to binary64
→ invert BSM volatility with exactly 80 bisection iterations
→ compute five row-local unit Greeks
→ canonicalize to 8-decimal strings
→ write one ordered submission.json
```

### 2.2 Multi-asset / correlation boundary

Public child 来自同一个 22-asset、P/Q measure-qualified、PSD-by-construction parent，并抽取 8 个 underlyings。P/Q dependence IDs 与 joint-market contract 必须进入 package provenance 和 authoring gates。

但本 task 的每一行仍是 single-asset European vanilla：

- cross-asset correlation 不进入单个 BSM price、IV 或 Greek 公式；
- changing a legal correlation matrix must not change marginal row outputs when `S,K,T,r,q,σ` are fixed；
- public method contract 必须明确这一点；prompt 只路由到该合同，不能另写一份可能漂移的
  correlation 说明；
- full factor loadings、private calibration provenance 和 latent node heights 不进入 solver-visible DB。

### 2.3 Out of scope

- basket/index/spread/best-of payoff；
- smile/surface、Heston/CEV/local-vol；
- portfolio Greeks、hedging、VaR/ES；
- arbitrage detection、X/U/T、maximal spread；
- 对 `d1`、`d2` 或 pricing terms 做 regression；
- 读取 private parent tables 或 authoring IV audit；
- approximate verifier tolerance。

`d1/d2` 可以是 solver 公式内部的瞬时变量，但不得成为公开输入、目标字段、stored label、regression target 或提交字段。

## 3. Golden package directory

已落地的 source task instance 结构如下：

```text
environments/
└── solver/
    ├── README.md
    ├── capabilities.bsm_greeks_v1.json
    ├── requirements.lock
    └── tests/
        ├── test_allowed_capabilities.py
        └── test_denied_capabilities.py

task_packages/
└── bsm_market_implied_greeks_v1/
    └── <task_id>/
        ├── manifest.json
        ├── public/
        │   ├── task.duckdb
        │   ├── prompt.md
        │   ├── runtime_contract.json
        │   └── submission.schema.json
        ├── verifier/
        │   ├── conftest.py
        │   ├── test_contract.py
        │   ├── test_data_identity.py
        │   ├── test_semantics.py
        │   └── oracle_config.json
        ├── reference/
        │   ├── trajectory.jsonl
        │   ├── final_submission.json
        │   └── artifacts/
        │       ├── solver.py
        │       ├── input_digest.json
        │       └── self_check.json
        ├── authoring_private/
        │   ├── artifact_manifest.json
        │   ├── parent_identity.json
        │   ├── sample_manifest.json
        │   ├── oracle_answer.json
        │   ├── build_report.json
        │   └── leakage_report.json
        └── views/
            ├── authoring/manifest.json
            ├── train_dev/manifest.json
            └── evaluation/manifest.json
```

目录边界：

- `public/` 是 Agent 唯一可观察任务内容；
- `verifier/` 在正式 evaluation 中隐藏；
- `reference/` 只进入 train/dev、审计和示例视图；
- `authoring_private/` 永不进入 Agent sandbox；
- `manifest.json` 只能保存 public-safe identity、版本和 hashes；private seed/parameters 使用 private overlay。

## 4. Database package contract

### 4.1 Child selection

Golden task 固定：

- 从 frozen 22-underlying parent 无放回选择 8 个 underlyings；
- 1 个 valuation date；
- 每个 underlying 选择 2 个 live expiries；
- 每个 expiry 选择 forward-moneyness 最接近 ATM 的 5 个 strikes；
- call/put paired grid 全部保留；
- 上限 `8 × 2 × 5 × 2 = 160` rows；
- 任一必需 row 无 IV root、non-finite Greek 或落在 canonical rounding instability boundary 时，整题换 selector/date，不能发布后静默删 row。

### 4.2 Public relations

`public/task.duckdb` 只包含：

```text
metadata.public_task
solver_visible.greeks_task_inputs
solver_visible.greeks_task_contract
```

`metadata.public_task` 固定字段：

```text
schema_version, task_id, task_family, task_version, variant_id,
snapshot_id, snapshot_revision, status, valuation_date, currency,
underlying_count, option_row_count,
joint_market_contract_id,
p_dependence_spec_id, q_dependence_spec_id,
dependence_policy_id,
risk_neutral_measure_id, numeraire_id, rate_path_id,
selection_policy_id
```

`solver_visible.greeks_task_inputs` 至少包含：

```text
row_id, task_id, snapshot_id, valuation_date,
underlying_id, option_id, call_put,
spot, strike, expiry, time_to_expiry_actual365,
bid, ask, contract_multiplier, currency,
risk_free_rate, dividend_yield,
calendar, day_count, exercise_style, settlement_type
```

`solver_visible.greeks_task_contract` 固定为一行
`(task_id, method_id, contract_json)`；其中 canonical JSON 锁定：

- method ID；
- midpoint construction；
- IV bracket、80-step update rule 与 final-root rule；
- normal CDF/PDF definition；
- Greek definitions、units 和 scaling；
- dtype/cast order；
- output precision、rounding 和 row order；
- invalid-input behavior。

### 4.3 Public DB security

不得出现：

- `market.*` authoring tables；
- parent attachment、filesystem path 或 raw credentials；
- generator/sample seeds；
- latent drift/diffusion node values；
- factor-loading calibration provenance；
- authoring `option_pricing_audit`；
- QuantLib oracle values、canonical answer、hidden labels；
- reference trajectory 或 verifier results。

Exporter 必须在 read-only attach parent 后完成 `CREATE TABLE AS SELECT`，随后 detach；public child 自身使用新 identity。Checksum 计算 canonical logical rows，不依赖 DuckDB physical bytes 或 timestamps。

## 5. Prompt contract

`prompt.md` 是最小路由层，不是数学讲义、数据字典、schema 副本或权限副本。它必须由
versioned prompt contract 确定性渲染，并且只要求 Agent 读取以下四个公开 source of truth：

1. `query_greeks_task_contract_v1`：返回唯一的 BSM measure、定价、IV、Greeks、dtype、
   canonicalization 与 row-order 合同；
2. `query_greeks_task_inputs_v1`：返回完整且已经 canonical ordered 的 solver-visible rows；
3. `public/submission.schema.json`：定义唯一允许的提交结构、字段与字符串格式；
4. `public/runtime_contract.json`：定义 trusted tools、call limits、imports、filesystem、network
   和 resource budget。

Prompt 可以陈述一句任务目标，并要求按这四个 source 生成完整 submission；除此之外不再展开
公式、迭代 pseudocode、列名、Greek 单位、舍入规则、权限清单或禁止项。任何一个事实只能由其
owner source 定义；四个 source 之间不一致时 packaging 必须失败，不能靠 prompt 规定优先级。

### 5.1 Minimal routing text

规范化 prompt 只需要表达：

```md
# Market-implied BSM unit Greeks

Produce a complete submission for every public input row.

Call `query_greeks_task_contract_v1` and `query_greeks_task_inputs_v1` exactly
once each. Treat their returned data, `public/submission.schema.json`, and
`public/runtime_contract.json` as the complete authoritative specification.
Call `submit_greeks_submission_v1` exactly once.
```

Solver-interface version 可以把纯排版归入同一语义版本，但 stable identity 仍绑定 rendered prompt
digest，因此任何 byte change 都会得到新的 `solver_interface_digest`；改变 source 集合、
ownership、required action、trusted adapter surface 或 submission routing 时还必须升级该版本。

### 5.2 Mathematical ownership

概率测度、numeraire、ex-dividend spot 语义、flat continuous curves、Actual/365 Fixed、
Decimal quote midpoint、binary64 cast checkpoint、固定 IV 求根 law、normal CDF/PDF、五个
unit Greek 定义与 scale、contract-multiplier policy、8 位 `ROUND_HALF_EVEN`、negative zero
和 canonical row order 全部只存在于 `solver_visible.greeks_task_contract.contract_json`。
Prompt 不复述这些条件；submission schema 也只负责输出形状，不反向定义数学。

Prompt 不包含 oracle values、selected-row failure retries、parent private values、reference implementation 或 hidden test names。

## 6. Execution environment contract

### 6.1 Capability composition

每题有效权限：

\[
E_i=E_{\text{global solver allowlist}}\cap E_{\text{task overlay}}.
\]

Task overlay 只能删除能力或收紧预算，不能扩大 global allowlist。

建议新增 `environments/solver/capabilities.bsm_greeks_v1.json`：

```text
allowed direct imports:
  math, decimal, datetime, json, hashlib,
  dataclasses, typing, itertools

trusted tools:
  query_greeks_task_contract_v1   max_calls=1
  query_greeks_task_inputs_v1     max_calls=1
  submit_greeks_submission_v1     max_calls=1

filesystem:
  read only declared public package files
  write only submission/

denied:
  raw DuckDB connection
  ATTACH/COPY/INSTALL/LOAD
  QuantLib, py_vollib, mibian, rateslib
  packaged IV/Greek/pricing/surface APIs
  numpy/pandas/scipy finance shortcuts
  network, dynamic installation, subprocess
  undeclared filesystem and verifier/reference/private paths
```

`duckdb==1.5.5` 可以存在于 trusted query adapter/runtime image，但不因此给 Agent raw database connection 权限。

### 6.2 Frozen resource budget

Golden task 已冻结为：

```text
vCPU: 1
memory: 1 GiB
wall clock: 600 s
trusted query calls: 2 total
submission calls: 1
submission size: 5 MiB
network: disabled
process spawning: disabled
```

在 reference replay 后可以用新 environment version 调整，但同一 package identity 内不得修改。

### 6.3 Enforcement boundary

- Runtime policy 在启动前与运行中强制；
- semantic pytest 只验证 canonical submission；
- policy violation 立即使 run 无效，不等待 pytest；
- prompt 只指向 `public/runtime_contract.json`；权限由 runtime enforcement 实施，不由 prompt
  文本授予；
- runtime audit 由 harness 保存，Agent 不可修改；
- reference solver 必须在完全相同的 `E_i` 下 replay。

仓库内 reference harness 以独立 `spawn` 子进程执行 solver，并强制 import/builtin/tool-call/
submission-size policy、CPU affinity、`RLIMIT_AS`、`RLIMIT_CPU` 和 parent wall timeout。
这些是可执行的本地 reference controls；生产 deployment 仍必须由 OS/container 层独立实施
network、filesystem 和 process isolation，不能把 AST/source policy 当作完整安全边界。

## 7. Canonical submission

MVP 只使用一个文件：

```text
submission/submission.json
```

固定结构：

```json
{
  "task_id": "bsm-mig-v1-<24-hex>",
  "submission_schema_version": "bsm-market-implied-greeks-submission-v1.0.0",
  "method_id": "bsm-mid-iv-bisection80-analytic-greeks-v1",
  "status": "completed",
  "rows": [
    {
      "row_id": "row_000001",
      "iv_status": "CONVERGED_FIXED_ITERATIONS",
      "market_implied_volatility": "0.21345678",
      "unit_delta": "0.51234567",
      "unit_gamma": "0.02345678",
      "unit_vega_1volpt": "0.12345678",
      "unit_theta_1calendar_day": "-0.01234567",
      "unit_rho_1pct": "0.04567891"
    }
  ]
}
```

Rules：

- 所有 numeric answers 都是恰好 8 decimal places 的 JSON strings；
- `ROUND_HALF_EVEN`；
- rows 按 `valuation_date, underlying_id, expiry, strike, call_put, option_id` 对应的 public `row_id` 顺序；
- 不允许 extra fields、duplicate/missing rows、NaN/Inf/scientific notation；
- 不提交 `d1/d2`、contract-multiplied Greeks、free-text reasoning 或 tolerance；
- MVP authoring gates 保证所有发布 rows 都有合法 bracket，所以 submission 不需要 fallback status。

## 8. Pytest hard verifier

Verifier 只能以：

```text
public task.duckdb + frozen method/oracle config + Agent submission
```

重建 task truth。不得直接用 generator latent volatility、private diffusion nodes 或 parent audit rows 判分。

### 8.1 Contract tests

- required file and exact JSON schema；
- exact task/method/schema IDs；
- no extra files or fields；
- all expected `row_id` exactly once；
- canonical row order；
- exact decimal-string format；
- finite and valid enum values。

### 8.2 Data identity tests

- public DB logical checksum matches manifest；
- task contract hash and runtime contract hash match；
- row count、8-underlying identity、paired call/put grid match；
- P/Q dependence IDs and joint-market contract match public provenance；
- no solver-visible private relations/columns。

### 8.3 Semantic tests

Trusted verifier 使用 pinned `QuantLib==1.39`：

1. 从 public bid/ask 以相同 Decimal rule 构造 midpoint；
2. 用 QuantLib analytic European price wrapper 执行同一个 80-step schedule；
3. 使用 recovered `σ_IV` 和公开 `S,K,T,r,q,type` 复算五个 Greeks；
4. 按同一 cast/rounding/serialization contract canonicalize；
5. 对每一字段做 exact string equality。

禁止 `pytest.approx`、`isclose`、`allclose`、`atol`、`rtol`。

### 8.4 Required negative tests

- 最后一位 decimal perturbation；
- 79/81-step 或 tolerance early-stop 导致且在 canonical output 上可观察的 root；显式
  schedule/source drift 即使舍入后不可观察，也由 runtime/source policy 拒绝；
- midpoint 在 Decimal 之前提前转 float；若 8-decimal output 未发生变化，只做 source-policy
  claim，不把不可观察实现差异伪装成 semantic verifier 能区分的事实；
- vega/rho 未按 1% scaling；
- theta per-year、per-business-day 或 sign 错误；
- call/put 公式混淆；
- contract multiplier 被错误乘入 unit Greeks；
- wrong day count/rate/dividend convention；
- missing/duplicate/extra row；
- correct values but wrong row order；
- extra `d1/d2` fields；
- copied oracle/private field leakage。

### 8.5 Reward

\[
R_{\mathrm{ORM}}=
\mathbf 1\{\text{runtime policy valid}\}
\cdot
\mathbf 1\{\text{all applicable pytest tests pass}\}.
\]

无 partial credit，trajectory wording 不参与 reward。

## 9. Reference Agent trajectory

Reference trajectory 是一条正确的 observable execution path，不是 hidden chain-of-thought，也不是唯一允许解法。

建议事件顺序：

```text
read prompt/runtime/output schema
→ query greeks_task_contract once
→ query greeks_task_inputs once
→ validate keys, types and canonical row order
→ implement normal CDF/PDF and BSM price primitives
→ compute Decimal midpoints
→ run fixed 80-step inversion for every row
→ compute five unit Greeks
→ canonicalize and sort
→ run local schema/invariant self-checks
→ submit submission.json once
```

`trajectory.jsonl` 每个 event 只记录：

- `step_id`；
- `event_type`：observation/action/tool_result/decision/artifact/submission；
- tool name and public input/artifact reference；
- short auditable summary；
- status and digest。

不保存不可验证的隐藏思维链。Reference artifacts 可包含 solver source、query receipts、input digest、self-check report 和 final submission；不能包含 QuantLib、oracle answer、private parent 或 authoring retry history。

Reference trajectory 必须在 solver image、allowed imports、tool budgets 和 filesystem policy完全相同的 clean environment 中 replay，并产生 byte-identical `submission.json`。

## 10. Manifest and stable identity

`manifest.json` 建议字段：

```text
package_schema_version
task_id, task_family, task_version, variant_id
coordinates = {L,P,M,A,D,R,F}
parent_snapshot_id and parent logical checksum
public_child_snapshot_id and public logical checksum
valuation_date and ordered selected-underlying IDs
joint_market_contract_id
p_dependence_spec_id and q_dependence_spec_id
method_contract_id and digest
submission schema ID and digest
runtime environment/profile/lock/image IDs and digests
verifier ID and oracle-config digest
prompt/database/runtime/schema hashes
build status and release profile
```

现有 v1 manifest 不增加一个重复的 `solver_interface_digest` 字段；package verifier 从上述
public artifact/method digests 重算它，再核对由它导出的 `task_id`。

`solver_interface_digest` 必须在 task ID 生成前按以下 canonical payload 计算：

```text
interface_contract_version
method_contract_digest
prompt_digest
runtime_contract_digest
submission_schema_digest
```

`interface_contract_version` 管理 trusted adapter 名称、input mapping surface 和四源 routing
ownership；其语义改变时必须升级。Ordered input values 继续由 parent identity、selection、public
child logical checksum 和 manifest 绑定，不再次塞入 interface digest。这样修改 prompt
routing、method contract、solver interface version、输出 schema 或有效权限中的任一项，都会
创建新的 solver interface，而不能沿用旧 task identity。

Stable task ID：

```text
sha256(
  parent_logical_checksum
  | variant_id
  | valuation_date
  | ordered_selected_underlying_ids
  | method_contract_id
  | solver_interface_digest
  | package_schema_version
)
```

Checked-in `ACCEPTED` package 不做原地升级。本次最小 prompt 已先计算新的
`solver_interface_digest` 和 task ID，构建到新目录，并在通过 replay、verifier、leakage 与
view checks 后把 registry 和文档引用显式更新为新 identity。

Public manifest 不保存 sample seed、generator seed、latent values、rejected selectors、oracle answer 或 private filesystem paths。

## 11. Authoring / train-dev / evaluation views

同一个 source package 自动导出三种视图。

### Authoring bundle

包含全部 public、verifier、reference、private manifests、oracle answer、leakage/build/runtime reports。仅用于生成、QA 和审计。

### Train/dev bundle

包含 public package、reference trajectory、reference final submission、允许公开的 contract tests 和 runtime examples。不得包含 private parent values 或 hidden evaluation fixtures。

### Evaluation bundle

Agent 可见：

```text
manifest.json
public/task.duckdb
public/prompt.md
public/runtime_contract.json
public/submission.schema.json
submission runner
```

Agent 不可见：semantic pytest、oracle config/answer、reference trajectory、parent identity overlay、private build/leakage reports。Evaluation runner 在 server side 绑定同一 runtime contract 和 hidden verifier。

## 12. Packaging pipeline

```text
require frozen parent + exact source commit
→ deterministic 8-underlying/date selection
→ paired-grid and IV-root stability gates
→ export public-only child DuckDB
→ compute canonical logical checksum
→ generate public task/method contracts
→ global allowlist ∩ restrictive task overlay
→ validate effective runtime contract
→ render four-source minimal routing prompt
→ compute and bind solver_interface_digest
→ drift-test prompt against its versioned routing contract
→ generate hidden QuantLib oracle config/answer
→ generate reference submission under solver permissions
→ replay reference trajectory in clean sandbox
→ run semantic and policy negative tests
→ leakage scan all public artifacts and nested JSON
→ generate manifests and hashes
→ export authoring/train-dev/evaluation views
```

任何一步失败，该 task instance 不得标记 `RELEASED`。

## 13. Implementation phases

### Phase A — Freeze package interfaces

- [x] generic package manifest schema；
- [x] generic runtime contract schema；
- [x] generic observable trajectory event schema；
- [x] BSM Greeks submission schema；
- [x] BSM oracle config schema；
- [x] task/package/environment/method versioning rules。

这一阶段可以在 P/Q dependence 与 Greeks implementation 完成前先做。

### Phase B — Runtime and prompt

- [x] `capabilities.bsm_greeks_v1.json`；
- [x] restrictive task overlay；
- [x] effective-contract composer；
- [x] prompt template；
- [x] minimal four-source prompt renderer；
- [x] prompt/source-ownership/runtime drift tests；
- [x] positive/negative capability tests。

### Phase C — Public database package

- [x] deterministic selector；
- [x] root/canonical stability authoring gates；
- [x] three-relation child exporter；
- [x] logical checksum；
- [x] leakage scanner；
- [x] stable task ID generation binds the derived `solver_interface_digest`；v1 manifest 保持原 shape。

依赖主修改清单中的 P/Q contract 和 public-child exporter。

### Phase D — Verifier and reference run

- [x] independent QuantLib oracle wrapper；
- [x] contract/data/semantic pytest layers；
- [x] negative submission fixtures；
- [x] stdlib-only reference solver；
- [x] reference trajectory recorder/replayer；
- [x] byte-identical final submission replay。

依赖 BSM/Greeks 与 fixed 80-step IV contracts 完成。

### Phase E — Golden package acceptance

1. [x] analytic fixture all-pass；
2. [x] market-implied 8-underlying golden task all-pass；
3. [x] clean-room evaluation bundle solvable；
4. [x] all semantic negative submissions rejected；
5. [x] all declared runtime-policy attacks blocked；
6. [x] public/private leakage scan clean；
7. [x] authoring/train-dev/eval views contain exactly their allowlisted files。

### Phase F — Batch packaging

Golden interfaces 不变，只把 private nonnegative `sampling_seed` 参数化；valuation date、
method、runtime、submission schema 和 verifier 均保持冻结。

- [x] batch runner 只 materialize 一次 private frozen 22-underlying parent；
- [x] 按递增 seed 选择 distinct 8-underlying subsets，并记录可解释的 candidate rejection；
- [x] 每个 accepted package 都经过 reference replay、QuantLib exact verifier、leakage 与
  release-view checks；
- [x] verified packages 可导出按 `task_id` 排序的九字段 JSONL dataset，且不包含 private
  oracle 或 sampling seed；
- [x] dataset exporter 的单 package contract test；
- [x] 2-task scratch smoke：两个 task IDs/subsets 唯一，reference/QuantLib/package checks
  all-pass，dataset 恰有 2 records 且无 private oracle/seed；
- [ ] 用当前 solver interface 重建并保存完整 100-task batch 与独立 dataset copy；
- [ ] 审核 run summary/split policy 后，将获批 artifacts 显式 promote 为 `RELEASED`。

## 14. Actual repository changes

```text
environments/solver/capabilities.bsm_greeks_v1.json
environments/solver/capabilities.global_v1.json
environments/solver/README.md

schemas/agent-task-package-v1.schema.json
schemas/agent-task-runtime-contract-v1.schema.json
schemas/agent-task-trajectory-v1.schema.json
schemas/bsm-greeks-submission-v1.schema.json
schemas/bsm-greeks-oracle-config-v1.schema.json

configs/task_packages/bsm_market_implied_greeks_v1.json

src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/contracts.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/bsm_market_greeks_verifier_runtime.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/database.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/leakage.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/package.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/parent.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/prompt_renderer.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/reference_solver.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/runtime.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/trajectory.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/views.py
src/synthetic_derivatives/training/bsm_market_greeks.py

scripts/package_bsm_greeks_task.py
scripts/run_bsm_greeks_batch.py

tests/packaging_analytic_and_implied_greeks_iv/conftest.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_database_and_replay.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_dataset_export.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_negative_submissions.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_package_contract.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_prompt_runtime_drift.py
tests/packaging_analytic_and_implied_greeks_iv/test_bsm_greeks_release_views.py
```

不要复制 F2A-specific capability profile、submission schema、trajectory outcome 或 certificate files；只复用通用 packaging abstractions。

## 15. Suggested commit sequence（尚未执行）

1. `add generic agent-task package and runtime schemas`；
2. `add BSM Greeks capability profile and prompt contract`；
3. `add public-child package exporter and manifest`；
4. `add hidden QuantLib verifier and negative submissions`；
5. `add reference replay and golden package views`。

当前实现尚未按这五项形成新的 commit split。若在 review 后提交，每个 commit 都必须保持
现有 frozen parents、F2A branches 和 `main` 不变。

## 16. Definition of Done

### Database and provenance

- [x] one independent frozen public DuckDB；
- [x] exactly 8 underlyings and complete selected paired grid；
- [x] no private relations/metadata；
- [x] deterministic logical checksum and task ID；
- [x] P/Q PSD joint-market IDs bound into provenance；
- [x] changing legal correlation leaves marginal expected answers unchanged。

### Prompt and runtime

- [x] prompt routes only to contract query、input query、submission schema 与 runtime contract；
- [x] mathematical and numerical semantics live only in the public DB method contract；
- [x] solver-interface digest binds its version、prompt、method、schema 与 runtime；
- [x] task overlay cannot expand global allowlist；
- [x] trusted tool, import, filesystem, network and resource policies are enforced；
- [x] QuantLib/pricer/IV/Greek shortcuts and raw DB access are blocked。

### Submission and verifier

- [x] exact one-file canonical JSON submission；
- [x] fixed 80-step IV and five Greek conventions frozen；
- [x] trusted QuantLib verifier independently recomputes from public data；
- [x] exact string equality with no tolerance；
- [x] reference submission all-pass；
- [x] every observable semantic negative case fails；79/81-step 与 early-float 的不可观察情形由 runtime/source policy 拒绝。

### Reference trajectory and release views

- [x] trajectory records observable events/artifacts, not hidden reasoning；
- [x] reference run uses the same solver permissions and budgets；
- [x] replay is byte-identical；
- [x] evaluation bundle excludes verifier/oracle/reference/private files；
- [x] train/dev and authoring views contain only their declared files；
- [x] manifests and hashes verify every accepted source artifact；
- [x] 2026-08-10 implementation checkpoint passed the then-current full suite (`260 passed`) without frozen snapshot changes；this is a historical gate, not the current test count。

### Batch and release promotion

- [x] batch runner and nine-field dataset exporter are implemented and contract-tested；
- [x] batch records distinct task IDs/subsets, candidate rejections and verification summary；
- [ ] current-interface 100-task batch and exported dataset have been rebuilt and audited；
- [ ] approved packages have been explicitly rebuilt/promoted as `RELEASED`。

## 17. Final recommendation

五个接口和 8-underlying golden package 已冻结并通过 clean-room、negative submissions、
runtime attacks 与 leakage scan。当前只参数化 private selector seed，batch runner 与 dataset
exporter 已就绪；下一项受控工作是执行完整 batch、审核数据集与 split provenance，然后再
显式决定是否 promotion 到 `RELEASED`。

现有结构保留 `(database, prompt, pytest, reference trajectory)` 的直观业务边界；prompt
缩减为四个 public source 的路由层，数学真相留在 DB method contract，权限真相留在 effective
runtime contract。`solver_interface_digest` 防止 prompt/interface 变化复用旧 task identity；
生产环境仍需 OS/container isolation。F2A 的 arbitrage 语义没有进入本 task。
