# BSM Market-Implied IV / Greeks Delivery 返修方案

## 0. 文档目的

本方案用于返修 `synthetic-BSM-agent-task` 分支中的 BSM market-implied Greeks portable delivery 流程。

目标不是修改底层 market generator，也不是重新生成 100 个 source package，而是从当前已经验证完成的 100 个 source task/database 中，无放回选择 24 个底层市场内容不同的数据库，并在 delivery 阶段生成：

- 4 个 market-implied IV agent tasks；
- 4 个 market-implied Delta agent tasks；
- 4 个 market-implied Gamma agent tasks；
- 4 个 market-implied Vega agent tasks；
- 4 个 market-implied Theta agent tasks；
- 4 个 market-implied Rho agent tasks。

最终严格交付 `6 × 4 = 24` 个 agent tasks，并满足：

> 24 个 agent tasks 的 source DB 在全局范围内均不得重复；同一个 source DB 不得被投影成两个不同的 IV/Greek tasks。

本文档是交给 Codex 的实施与验收 handoff。除非本方案明确要求，不得扩张为重新设计 authoring pipeline、重新生成 parent data、训练数据集或 sandbox。

---

## 1. 当前 GitHub 状况与返修依据

审计基准：

- Repository：`ruihuabunny/FinMaths-synthetic-data-demo`
- Branch：`synthetic-BSM-agent-task`
- 审计提交：`39b42435662ffb3376cca329c009fa95ab3af039`
- 当前 delivery：`task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100`

当前 delivery 已包含：

- 100 个唯一 task IDs；
- 100 个 `task.duckdb`；
- 100 个不同的 `task.duckdb` Git blob SHA；
- 100 个不同的 option payload blob SHA；
- 100 个不同的 underlying payload blob SHA。

因此，当前 100 个 source tasks 足以无放回选取 24 个候选数据库。

但是，现有 delivery 仍是综合输出：每个 task 同时要求返回：

- `market_implied_volatility`
- `unit_delta`
- `unit_gamma`
- `unit_vega_1volpt`
- `unit_theta_1calendar_day`
- `unit_rho_1pct`

现有实现中的以下部分均被写死为综合 Greeks contract：

- `scripts/package_bsm_greeks_delivery.py`
- `src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_delivery.py`
- `src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/prompt_renderer.py`
- `src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_tools.py`
- `src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/bsm_market_greeks_verifier_runtime.py`
- `schemas/bsm-greeks-submission-v2.schema.json`

因此不得只在 prompt 中删除若干字段。Prompt、submission schema、tool host、task identity、DuckDB identity、verifier 和 manifests 必须一起拆分。

---

## 2. 最终交付矩阵

| Target | 每类 task 数量 | 每行允许提交的结果字段 | 数值约束 |
|---|---:|---|---|
| IV | 4 | `row_id`, `iv_status`, `market_implied_volatility` | positive decimal8 |
| Delta | 4 | `row_id`, `unit_delta` | signed decimal8 |
| Gamma | 4 | `row_id`, `unit_gamma` | nonnegative decimal8 |
| Vega | 4 | `row_id`, `unit_vega_1volpt` | nonnegative decimal8 |
| Theta | 4 | `row_id`, `unit_theta_1calendar_day` | signed decimal8 |
| Rho | 4 | `row_id`, `unit_rho_1pct` | signed decimal8 |

总计：

```text
6 targets × 4 tasks per target = 24 agent tasks
24 agent tasks = 24 unique source DBs
```

### 2.1 Greek task 中 IV 的处理

Delta/Gamma/Vega/Theta/Rho task 仍然必须：

1. 从公开 bid/ask midpoint 恢复 market-implied volatility；
2. 使用完成 80 次 bisection 后、尚未 canonical rounding 的 binary64 IV；
3. 计算对应的单个 analytic Greek。

但是 Greek task 的最终 submission 不得包含：

- `market_implied_volatility`；
- `iv_status`；
- 任何其他 Greek。

IV 是 Greek task 的内部计算 checkpoint，不是其额外 label。IV 自身由独立的四个 IV tasks 交付。

---

## 3. 必须冻结的全局不变量

### 3.1 数量不变量

```python
target_count == 6
tasks_per_target == 4
total_task_count == 24
```

### 3.2 Source 唯一性不变量

```python
len(set(source_task_ids)) == 24
len(set(source_database_file_digests)) == 24
len(set(source_market_content_digests)) == 24
```

### 3.3 Derived delivery 唯一性不变量

```python
len(set(derived_task_ids)) == 24
len(set(derived_database_file_digests)) == 24
```

### 3.4 一对一映射不变量

每个 source task/database 只能映射到一个 target：

```text
one source_task_id
    -> one source task.duckdb
    -> one target metric
    -> one derived task_id
    -> one derived task.duckdb
```

明确禁止：

```text
one source DB
    -> IV task
    -> Delta task
    -> Gamma task
```

即使三份 derived DB 的 `task_id`、`variant_id` 或文件 SHA 不同，也仍然违反本方案。

---

## 4. “底层 DB 不同”的严格定义

只比较 `task.duckdb` 文件 SHA 不够严格，因为同一个市场数据库仅修改 `task_id` 或 `snapshot_id` 后，文件 SHA 也会改变。

必须新增：

```python
market_content_digest(database: Path) -> str
```

### 4.1 Underlying rows 的 canonical projection

读取 `solver_visible.underlying_market_inputs`，删除纯 identity 字段：

- `task_id`
- `snapshot_id`

保留其余全部公开字段，并按以下 natural key 排序：

```text
valuation_date, underlying_id
```

### 4.2 Option rows 的 canonical projection

读取 `solver_visible.option_quote_inputs`，删除纯 identity/position 字段：

- `row_id`
- `task_id`
- `snapshot_id`

保留 option identity、contract 和 quote 字段，并按以下 natural key 排序：

```text
valuation_date,
underlying_id,
expiry,
strike,
call_put,
option_id
```

### 4.3 Canonical digest

将 normalized underlying rows 与 normalized option rows 使用项目现有 canonical JSON 编码后计算 SHA-256：

```python
sha256(
    canonical_json_bytes(
        {
            "underlying_market": normalized_underlyings,
            "option_quotes": normalized_options,
        }
    )
).hexdigest()
```

只有 `market_content_digest` 不同，才能认定底层 solver-visible market DB 内容不同。

Builder 必须同时验证 file digest 和 semantic market-content digest；任意一项重复都拒绝发布。

---

## 5. Deterministic 无放回分配

不得依赖 generator 的 `sampling_seed`，不得在 portable artifacts 中泄露 private selection provenance，也不得使用不可复现的随机抽样。

### 5.1 Allocation 输入

- 完整 source `run_summary.json`；
- 100 个 accepted source tasks；
- 每个 source task 的 `source_task_id`；
- `source_database_file_digest`；
- `source_market_content_digest`；
- 用户指定的公开 `allocation_id`。

### 5.2 Candidate ranking

对所有 semantic-unique candidates 计算：

```python
allocation_rank = sha256(
    canonical_json_bytes(
        {
            "allocation_policy_id": (
                "sha256-rank-round-robin-without-replacement-v1"
            ),
            "allocation_id": allocation_id,
            "source_task_id": source_task_id,
            "source_market_content_digest": source_market_content_digest,
        }
    )
).hexdigest()
```

按 `(allocation_rank, source_task_id)` 排序，取前 24 个。

### 5.3 Round-robin assignment

冻结 target 顺序：

```text
iv,
delta,
gamma,
vega_1volpt,
theta_1calendar_day,
rho_1pct
```

按四轮 round-robin 分配：

| Round | IV | Delta | Gamma | Vega | Theta | Rho |
|---:|---|---|---|---|---|---|
| 1 | S01 | S02 | S03 | S04 | S05 | S06 |
| 2 | S07 | S08 | S09 | S10 | S11 | S12 |
| 3 | S13 | S14 | S15 | S16 | S17 | S18 |
| 4 | S19 | S20 | S21 | S22 | S23 | S24 |

相同 source run、相同 `allocation_id`、相同代码版本必须得到完全相同的 assignment。

### 5.4 显式 assignment 模式

可以额外支持 `--assignment-file`，但必须满足：

- 恰好 24 条；
- 每个 target 恰好 4 条；
- 24 个 source task IDs 唯一；
- 24 个 source database digests 唯一；
- 24 个 market-content digests 唯一；
- 所有 source tasks 都存在于 accepted source run。

不得因为用户提供显式 assignment 而跳过唯一性验证。

---

## 6. 为什么必须新增 suite-level builder

如果分别运行六次独立 batch builder，第二次运行无法知道第一次使用过哪些 source DB，容易发生跨 target 复用。

因此必须新增一次性 suite builder：

```python
build_portable_bsm_metric_suite(...)
verify_portable_bsm_metric_suite(...)
```

职责包括：

1. 加载并验证完整 source run；
2. 计算所有 candidate DB 的 file/content digests；
3. 一次性选择并冻结 24 个 assignments；
4. 分别构建六种 target，每种四个 tasks；
5. 验证全局 DB uniqueness；
6. 验证每个 metric batch；
7. 验证全部 24 个 leaf tasks；
8. 全部成功后才原子发布整个 suite。

任何一个 target/task 构建失败时，不得留下半成品 delivery。

---

## 7. 推荐交付目录

```text
task_packages/deliveries/
└── bsm_market_implied_metric_suite_v1/
    └── 20260814_metric_6x4_unique_db/
        ├── suite_manifest.json
        └── targets/
            ├── iv/
            │   ├── batch_manifest.json
            │   └── tasks/
            │       ├── <iv-task-id-1>/
            │       ├── <iv-task-id-2>/
            │       ├── <iv-task-id-3>/
            │       └── <iv-task-id-4>/
            ├── delta/
            │   ├── batch_manifest.json
            │   └── tasks/                 # exactly 4
            ├── gamma/
            │   ├── batch_manifest.json
            │   └── tasks/                 # exactly 4
            ├── vega_1volpt/
            │   ├── batch_manifest.json
            │   └── tasks/                 # exactly 4
            ├── theta_1calendar_day/
            │   ├── batch_manifest.json
            │   └── tasks/                 # exactly 4
            └── rho_1pct/
                ├── batch_manifest.json
                └── tasks/                 # exactly 4
```

每个 leaf task 继续保持现有 portable task 的物理隔离结构：

```text
<task-root>/
├── delivery_manifest.json
├── source_manifest.json
├── task.duckdb
├── evaluation_view/
│   ├── manifest.json
│   └── public/
│       ├── prompt.md
│       ├── runtime_contract.json
│       └── submission.schema.json
├── trusted_tools/
│   ├── toolset.json
│   └── payloads/
│       ├── underlyings.json
│       └── options.json
└── verifier/
    ├── README.md
    ├── __init__.py
    ├── conftest.py
    ├── oracle_config.json
    ├── requirements.lock
    ├── runtime.py
    ├── test_contract.py
    ├── test_data_identity.py
    └── test_semantics.py
```

六个 target 目录分别可以作为批次打包，但全局 cross-target uniqueness 由同一 suite root 下的 `suite_manifest.json` 证明。

---

## 8. Metric-specific contract registry

新增一个单一、冻结、可测试的 target registry，例如：

```text
src/synthetic_derivatives/
└── packaging_analytic_and_implied_greeks_iv/
    └── metric_specs.py
```

Registry 至少包含：

- target key；
- public output field；
- variant ID；
- method ID；
- submission schema version；
- verifier ID；
- task ID prefix/pattern；
- decimal sign constraint；
- public unit description；
- 是否需要 `iv_status`。

推荐 IDs：

| Target | Variant ID | Method ID |
|---|---|---|
| IV | `bsm_market_implied_iv_v1` | `bsm-mid-iv-bisection80-v1` |
| Delta | `bsm_market_implied_delta_v1` | `bsm-mid-iv-bisection80-analytic-delta-v1` |
| Gamma | `bsm_market_implied_gamma_v1` | `bsm-mid-iv-bisection80-analytic-gamma-v1` |
| Vega | `bsm_market_implied_vega_1volpt_v1` | `bsm-mid-iv-bisection80-analytic-vega-1volpt-v1` |
| Theta | `bsm_market_implied_theta_1calendar_day_v1` | `bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1` |
| Rho | `bsm_market_implied_rho_1pct_v1` | `bsm-mid-iv-bisection80-analytic-rho-1pct-v1` |

禁止把 BSM 公式、`d1/d2`、CDF/PDF operation formulas 或 oracle values 写进 registry、public schema、public manifest 或 trusted-tool payload。

---

## 9. Prompt 与 submission schema

### 9.1 Prompt renderer

新增：

```python
render_bsm_metric_prompt(spec, runtime_contract) -> str
```

每个 prompt 必须：

- 只要求一个 target output；
- 明确单位；
- 明确 unit option，不应用 contract multiplier；
- 明确两次 query 各调用一次；
- 明确 submit 调用一次；
- 保留 Decimal midpoint、binary64、80 次 bisection、固定 bracket 等方法约束；
- Greek task 明确说明 IV 是内部 checkpoint，不得提交 IV；
- 禁止提交其他 Greeks；
- 不提供任何 BSM/Greek 公式；
- 不泄露 reference、verifier、private、parent、generator 或 sampling information。

### 9.2 Submission schemas

新增六份冻结的 JSON schemas，或者由同一冻结 generator 生成并通过 golden-byte tests 固定。

建议路径：

```text
schemas/bsm-market-implied-iv-submission-v1.schema.json
schemas/bsm-market-implied-delta-submission-v1.schema.json
schemas/bsm-market-implied-gamma-submission-v1.schema.json
schemas/bsm-market-implied-vega-1volpt-submission-v1.schema.json
schemas/bsm-market-implied-theta-1calendar-day-submission-v1.schema.json
schemas/bsm-market-implied-rho-1pct-submission-v1.schema.json
```

所有 schema 必须：

- 使用 JSON Schema 2020-12；
- 顶层 `additionalProperties: false`；
- row-level `additionalProperties: false`；
- 固定 `submission_schema_version`；
- 固定 target-specific `method_id`；
- 强制 contiguous `row_000001...` order；
- decimal strings 恰好八位小数；
- 禁止 `-0.00000000`；
- Greek schema 不允许 `market_implied_volatility` 或其他 Greek 字段；
- IV schema 不允许任何 Greek 字段。

---

## 10. Task identity 与 derived database projection

拆分后的 prompt/schema/method 已改变，因此不得复用原来的 `bsm-mig-v2-*` task ID。

现有仓库已经通过测试冻结：solver-visible interface bytes 必须绑定到 task identity。新的 derived task ID 应绑定：

- source parent logical checksum；
- source market-content digest；
- target variant ID；
- target method ID；
- target prompt digest；
- runtime contract digest；
- target submission schema digest；
- metric projection schema version。

建议：

```python
derived_task_id = metric_specific_prefix + sha256(
    canonical_json_bytes(identity_payload)
).hexdigest()[:24]
```

### 10.1 Derived `task.duckdb`

新增：

```python
project_bsm_metric_database(
    source_database,
    output_database,
    metric_spec,
    derived_task_id,
    derived_snapshot_id,
)
```

只允许修改 identity metadata：

- `metadata.public_task.schema_version`
- `metadata.public_task.task_id`
- `metadata.public_task.task_version`
- `metadata.public_task.variant_id`
- `metadata.public_task.snapshot_id`
- underlying relation 中的 `task_id/snapshot_id`
- option relation 中的 `task_id/snapshot_id`

所有市场、合约和报价字段必须保持不变。

Projection 后必须：

- 重算 derived logical checksum；
- 重算 file digest；
- 导出与 derived DB 完全一致的 trusted-tool payloads；
- 证明 derived `market_content_digest == source market_content_digest`；
- 证明 source DB 只进入一个 derived task。

---

## 11. Verifier 返修

不得从综合 `reference/final_submission.json` 中切列并作为 ground truth。

每个 delivered verifier 必须从本 task 的公开 derived `task.duckdb` 重新计算答案：

1. 读取公开 rows；
2. 用 Decimal 计算 quote midpoint，再一次性 cast 到 binary64；
3. 在 `[1e-6, 5.0]` 上执行严格 80 次 bisection；
4. IV task canonicalize sigma；
5. Greek task 使用未舍入 sigma 通过 pinned QuantLib analytic engine 计算当前 target；
6. 应用冻结的单位 scaling；
7. `ROUND_HALF_EVEN` canonicalize 为 decimal8；
8. 对完整 submission 进行 exact equality；
9. 禁止 tolerance、`isclose`、`pytest.approx`。

建议将现有 hard-coded `MarketGreeksSubmission` verifier 改成 target-aware generic runtime：

```python
verify_market_metric_submission(
    task_inputs,
    submission,
    oracle_config,
) -> None
```

Verifier runtime 内必须包含冻结的 target whitelist，并验证 `oracle_config.json` 与对应 target spec 完全一致，不能接受任意 field/method 配置。

现有 `_adapt_portable_verifier()` 依赖字符串替换，应删除或停止用于新 suite。新 verifier files 应从稳定模板直接渲染，避免路径或函数名变化后静默生成错误 verifier。

---

## 12. Portable trusted tools

现有 `portable_tools.py` 使用 `MarketGreeksSubmission.from_mapping()`，会强制要求全部 Greeks，必须返修。

新 tool host 必须：

- 从当前 task toolset/metric spec 加载 target identity；
- 按当前 submission schema 严格验证；
- 保持两次 query、一次 submit 的 frozen schedule；
- 保持 submission byte budget；
- submission `task_id` 必须与 derived DB/query task ID 相同；
- 不允许综合 submission 通过单 metric task；
- 不允许 Delta submission 提交到 Gamma task；
- payload 必须由 derived DB 导出并进行 digest binding。

查询工具名称可以保持：

- `query_greeks_underlying_market_v2`
- `query_greeks_option_quotes_v2`

为减少 harness 迁移，submit 工具也可以继续使用：

- `submit_greeks_submission_v2`

但其实际 schema binding 必须变成当前 metric-specific schema。

---

## 13. Manifests

### 13.1 `suite_manifest.json`

建议结构：

```json
{
  "suite_schema_version": "bsm-market-metric-suite-v1.0.0",
  "delivery_id": "20260814_metric_6x4_unique_db",
  "delivery_status": "PORTABLE_SUITE_VERIFIED",
  "source_variant_id": "bsm_market_implied_greeks_v1",
  "allocation_policy_id": "sha256-rank-round-robin-without-replacement-v1",
  "allocation_id": "20260814_metric_6x4_v1",
  "target_order": [
    "iv",
    "delta",
    "gamma",
    "vega_1volpt",
    "theta_1calendar_day",
    "rho_1pct"
  ],
  "tasks_per_target": 4,
  "total_task_count": 24,
  "unique_source_task_count": 24,
  "unique_source_database_count": 24,
  "unique_market_content_count": 24,
  "unique_derived_task_count": 24,
  "unique_derived_database_count": 24,
  "assignments": []
}
```

每条 assignment 至少记录：

```json
{
  "target": "delta",
  "source_task_id": "...",
  "source_database_digest": "...",
  "source_market_content_digest": "...",
  "derived_task_id": "...",
  "derived_database_digest": "...",
  "relative_path": "targets/delta/tasks/..."
}
```

`suite_manifest.json` 属于 delivery audit surface，不得暴露给 solver。

### 13.2 Target `batch_manifest.json`

每个 target manifest 必须：

- `task_count == 4`；
- `target_metric` 与目录一致；
- `variant_id/method_id` 与 metric spec 一致；
- 四个 source task/database/content digests 唯一；
- 引用 suite identity/assignment digest；
- `split_policy.policy == "one_metric_per_leaf_task"`。

### 13.3 Leaf `source_manifest.json`

现有 source manifest 假设 public artifacts 被原样复制，不再适用于 metric projection。

新 source manifest 必须明确区分：

- original source package/database identity；
- metric projection contract；
- derived task/database identity；
- source 与 derived market-content equality；
- source interface digest 与 derived interface digest。

不得伪称 metric-specific prompt/schema/database 与综合 source artifacts byte-identical。

---

## 14. Delivery profile 与 CLI

新增冻结 profile：

```text
configs/deliveries/bsm_market_implied_metric_suite_6x4_v1.json
```

Profile 至少冻结：

```json
{
  "profile_schema_version": "bsm-market-metric-delivery-profile-v1.0.0",
  "source_variant_id": "bsm_market_implied_greeks_v1",
  "targets": [
    "iv",
    "delta",
    "gamma",
    "vega_1volpt",
    "theta_1calendar_day",
    "rho_1pct"
  ],
  "tasks_per_target": 4,
  "total_task_count": 24,
  "allocation_policy_id": "sha256-rank-round-robin-without-replacement-v1",
  "require_unique_source_task": true,
  "require_unique_source_database": true,
  "require_unique_market_content": true,
  "require_unique_derived_task": true,
  "require_unique_derived_database": true
}
```

推荐 CLI：

```bash
python scripts/package_bsm_greeks_delivery.py \
  --run-root <completed-run-root> \
  --output-root task_packages/deliveries \
  --delivery-id 20260814_metric_6x4_unique_db \
  --profile configs/deliveries/bsm_market_implied_metric_suite_6x4_v1.json \
  --allocation-id 20260814_metric_6x4_v1 \
  --expected-source-task-count 100
```

成功输出必须包括：

```json
{
  "status": "PORTABLE_SUITE_VERIFIED",
  "target_count": 6,
  "tasks_per_target": 4,
  "total_task_count": 24,
  "unique_source_task_count": 24,
  "unique_source_database_count": 24,
  "unique_market_content_count": 24,
  "unique_derived_task_count": 24,
  "unique_derived_database_count": 24,
  "delivery_root": "..."
}
```

现有 `--task-id` 可以保留为 legacy combined builder 的参数；新 suite builder 如需人工指定，应使用完整 `--assignment-file`，避免 target assignment 含糊。

---

## 15. 文件级返修清单

### 15.1 保留的 legacy 路径

以下已有内容必须保持可验证且不得覆盖：

```text
task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100
```

现有：

```python
build_portable_bsm_greeks_delivery(...)
verify_portable_bsm_greeks_delivery(...)
```

可以继续保留为 legacy combined contract，避免破坏历史交付复现。

### 15.2 需要修改

```text
scripts/package_bsm_greeks_delivery.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/prompt_renderer.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_tools.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/database.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/bsm_market_greeks_verifier_runtime.py
task_packages/README.md
```

### 15.3 建议新增

```text
configs/deliveries/bsm_market_implied_metric_suite_6x4_v1.json
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/metric_specs.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/metric_allocation.py
src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_metric_suite.py
schemas/bsm-market-implied-iv-submission-v1.schema.json
schemas/bsm-market-implied-delta-submission-v1.schema.json
schemas/bsm-market-implied-gamma-submission-v1.schema.json
schemas/bsm-market-implied-vega-1volpt-submission-v1.schema.json
schemas/bsm-market-implied-theta-1calendar-day-submission-v1.schema.json
schemas/bsm-market-implied-rho-1pct-submission-v1.schema.json
tests/packaging_analytic_and_implied_greeks_iv/test_metric_specs.py
tests/packaging_analytic_and_implied_greeks_iv/test_metric_allocation.py
tests/packaging_analytic_and_implied_greeks_iv/test_portable_metric_suite.py
tests/packaging_analytic_and_implied_greeks_iv/test_metric_negative_submissions.py
```

如果为保持 packaged verifier self-contained 而需要新 runtime 文件，应继续复制进每个 leaf verifier，不能依赖安装项目源码。

---

## 16. 测试方案

### 16.1 Metric registry 与 schema tests

- registry 恰好包含六个 target；
- target keys、variant IDs、method IDs、schema versions 全部唯一；
- IV schema 只允许 IV fields；
- 每个 Greek schema 只允许对应 Greek；
- `additionalProperties: false` 在顶层与 row 层均生效；
- signed/nonnegative/positive decimal8 约束正确；
- negative zero 被拒绝；
- combined legacy submission 被所有 single-metric schemas 拒绝。

### 16.2 Prompt tests

- prompt 只要求一个 output metric；
- Greek prompt 说明必须内部恢复 IV，但不得提交 IV；
- 单位文字与 target 一致；
- query/submit tool call schedule 正确；
- 保留 80 次 bisection 等方法约束；
- 不包含 `d1 =`、`d2 =`、operation formulas、oracle/reference/private 内容；
- 不出现其他 Greek 的 submission field。

### 16.3 Allocation tests

- 同一 source run 和 `allocation_id` 得到相同 assignment；
- 恰好选中 24 个 candidates；
- 每个 target 恰好四个；
- 24 个 source task IDs 唯一；
- 24 个 source DB file digests 唯一；
- 24 个 market-content digests 唯一；
- source task/database 不跨 target 复用；
- candidate 少于 24 时 fail closed；
- 显式 assignment 中任何重复均被拒绝。

### 16.4 Semantic DB uniqueness tests

- 同一 DB 只修改 `task_id` 后，file SHA 可以变化，但 `market_content_digest` 必须不变；
- 同一 DB 只修改 `snapshot_id` 后，`market_content_digest` 必须不变；
- 同一 rows 改变物理顺序后，canonical digest 必须不变；
- 任意 spot/strike/bid/ask/rate/dividend/expiry/contract 字段变化后，digest 必须变化；
- 将同一 market-content DB 分配给两个 target 时，suite builder 必须失败。

### 16.5 Derived database tests

- derived DB 只改变 allowlisted identity columns；
- source 与 derived `market_content_digest` 完全相同；
- derived DB metadata、payload、toolset、manifest task identity 一致；
- derived task IDs 在不同 target/interface 下不同；
- prompt/schema 任意 byte 改动都会改变 task identity；
- 24 个 derived task IDs 与 DB file digests 唯一。

### 16.6 Verifier tests

对六个 target 分别验证：

- canonical reference submission 通过；
- 目标字段最后一位 decimal 改动被拒绝；
- 缺行、重复行、乱序、额外字段被拒绝；
- wrong task ID/method ID/schema version 被拒绝；
- wrong target submission 被拒绝；
- Delta submission 不能用于 Gamma task；
- legacy combined submission 不能用于 single-metric task；
- Vega ×100、Rho ×100、annual Theta、wrong Theta sign、contract-multiplied Delta 均被拒绝；
- verifier 不使用 tolerance；
- verifier 不读取 packaged reference；
- verifier 在无法 import `synthetic_derivatives` 的隔离环境中仍可运行。

### 16.7 Portable suite integration tests

先构建最小 smoke suite：

```text
6 targets × 1 task = 6 tasks
```

通过后再运行正式 profile：

```text
6 targets × 4 tasks = 24 tasks
```

正式 suite 验证：

- target count = 6；
- 每类 task count = 4；
- total task count = 24；
- 所有五层 uniqueness counters 均为 24；
- 每个 leaf task 可 relocation 后独立加载；
- trusted tools 与 derived DB query 完全等价；
- artifact digest tampering 被拒绝；
- evaluation view leakage scan clean；
- 任意中途失败不会发布 destination；
- 已有 destination 不得覆盖；
- legacy combined delivery 仍通过原 verifier。

---

## 17. 实施顺序

### Phase 1：冻结 contract，不生成全量 delivery

1. 新增 metric registry；
2. 新增六份 submission schemas；
3. 新增 target-specific prompt renderer；
4. 完成 registry/schema/prompt unit tests。

### Phase 2：实现 semantic DB fingerprint 与 allocator

1. 实现 `market_content_digest()`；
2. 实现 deterministic candidate ranking；
3. 实现 round-robin without-replacement assignment；
4. 实现 duplicate rejection；
5. 完成 identity-only mutation 和 semantic-duplicate tests。

### Phase 3：实现一个 single-metric leaf projection

1. 先使用一个 source package；
2. 生成一个 Delta task；
3. 完成 derived task ID、DB、prompt、schema、tools、verifier、manifests；
4. 验证 canonical solution 通过、combined solution 被拒绝。

### Phase 4：扩展到六个 target

1. 用同一 generic pipeline 构建六种 metric；
2. 不复制六份业务逻辑；
3. 完成 target-specific unit/negative tests；
4. 构建 6-task smoke suite。

### Phase 5：实现正式 suite builder

1. 一次性选择 24 个 unique source DBs；
2. 构建六个 target batches；
3. 写入 suite manifest；
4. 全局验证 assignment 与 DB uniqueness；
5. 原子发布。

### Phase 6：生成正式 24-task delivery

1. 使用冻结 profile 与 allocation ID；
2. 构建 `6 × 4` suite；
3. 运行全部 package/unit/integration tests；
4. 输出 selection/audit report；
5. 不修改或删除已有 100-task legacy delivery。

---

## 18. 最终验收标准

只有同时满足以下条件才能标记完成：

1. 最终存在且仅存在 24 个新 leaf tasks；
2. IV/Delta/Gamma/Vega/Theta/Rho 各四个；
3. 24 个 source task IDs 唯一；
4. 24 个 source database file digests 唯一；
5. 24 个 source market-content digests 唯一；
6. 24 个 derived task IDs 唯一；
7. 24 个 derived database file digests 唯一；
8. 任一 source DB 未被两个 target 复用；
9. 每个 task submission schema 只允许一个 target metric；
10. Greek task 不提交 IV，IV task 不提交 Greek；
11. verifier 从公开 DB 重新计算，不读取 reference；
12. exact decimal8 equality，无 tolerance；
13. prompt/schema/runtime/task ID/toolset/verifier/manifests 相互一致；
14. solver-visible artifacts 无答案、公式、private/reference/verifier 泄漏；
15. suite 可重复构建且 assignment deterministic；
16.任何失败均 fail closed，不发布部分结果；
17. 当前 `20260813_prompt_v2_100` legacy delivery 保持不变并继续通过验证。

最终 CLI summary 必须明确报告：

```text
PORTABLE_SUITE_VERIFIED
target_count = 6
tasks_per_target = 4
total_task_count = 24
unique_source_task_count = 24
unique_source_database_count = 24
unique_market_content_count = 24
unique_derived_task_count = 24
unique_derived_database_count = 24
```

---

## 19. 明确的非目标与保护项

本次返修不得：

- 重新生成 parent market database；
- 修改 underlying/option market generator；
- 修改 100 个 source tasks 的 market values；
- 将一个 source DB 投影成多个 target tasks；
- 将 BSM/Greek 公式写入 public JSON 或 prompt；
- 从 reference submission 截列充当 verifier truth；
- 删除或覆盖当前综合 100-task delivery；
- 扩张为训练集、SFT/RL 数据集或 sandbox 交付；
- 因便利而放宽 schema、row order、method、unit、rounding 或 verifier equality。

如果实现过程中发现当前 source run、schema/version 或 file allowlist 与本文档假设不一致，应停止并报告具体差异，不得自行猜测或静默修改交付语义。
