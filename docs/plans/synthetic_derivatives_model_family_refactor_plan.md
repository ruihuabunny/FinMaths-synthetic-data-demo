# Synthetic Derivatives Model-Family 重构计划

> 审计对象：`ruihuabunny/FinMaths-synthetic-data-demo`
>
> 分支：`synthetic-BSM-agent-task`
>
> 审计基线：`7ea46c9`（2026-08-17）
>
> 文档状态：基于当前实现更新的重构设计与实施 handoff；本文件是非规范性计划，当前行为以代码、配置、schema、测试和已验收 artifact 为准。

## 1. 这次更新后的结论

当前分支已经不再处于“先设计单 Greek package”的阶段。仓库已经完成：

- BSM analytic Greeks、scalar IV 和 market-implied Greeks 三类现有 variant；
- 100 个 combined IV + Greeks prompt-v2 task 的 verified delivery；
- Delta、Gamma、Vega、Theta、Rho、IV 六个 target 各 4 个实例的静态 6×4 suite；
- 同一 6×4 suite 向只读 DuckDB query v3 interface 的迁移；
- 24 个 task 在 source task、source DB、market content、derived task 和 derived DB 上的独立绑定；
- 面向六个单指标的 [`MetricSpec`](../../src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/metric_specs.py) 参数化，以及静态和 query-v3 两种 package materialization。

因此，本计划不再把“单 Greek 参数化”和“再生成 18–30 个 package”列为未来工作。新的重点是：

1. 给当前真实实现增加准确、稳定的 model-family identity；
2. 把设计 catalog 与 executable capability 分开，并让运行入口 fail closed；
3. 在不移动现有数值模块、不改写已验收 artifact 的前提下，引入最小 family dispatch；
4. 让 curriculum 与 mutation 使用 family-aware identity；
5. 用后续 L3 Monte Carlo 作为同一 model family 内的新 task/method 来验证抽象；
6. 等第二个真实 model family 出现后，再基于已证实的重复抽取通用模块。

## 2. 当前已实现基线

### 2.1 当前 family 的准确含义

当前 authoring 与 pricing 组合不是泛化的“所有 GBM/BSM”，而是：

- `P` 下 deterministic time-inhomogeneous GBM；
- `Q` 下 European Black–Scholes–Merton analytic pricing；
- 当前合同中声明的 P/Q measure mapping、common-Q 和 dependence 语义；
- visible midpoint 经固定 80-step bisection 反推 IV，再计算 analytic Greeks。

本计划将该组合的稳定 ID 定为：

```text
tdgbm_bsm
```

其中 `tdgbm` 表示 time-dependent deterministic-coefficient GBM。不要仅使用过宽的 `gbm_bsm`，否则未来 constant-parameter GBM、stochastic-rate GBM 或不同 P/Q mapping 会被错误归入同一 runtime identity。

建议 family metadata 明确持久化：

```yaml
model_family_id: tdgbm_bsm
underlying_dynamics_id: deterministic_time_inhomogeneous_gbm_p_v1
pricing_model_id: european_bsm_q_v1
measure_mapping_id: girsanov_drift_only_same_diffusion_v1
state_contract_id: tdgbm_bsm_daily_state_v1
model_coordinate: 0
```

### 2.2 当前 executable evidence

| 当前能力 | 现有实现身份 | 当前状态 | 主要证据 |
|---|---|---|---|
| Direct analytic Greeks | `bsm_analytic_greeks_v1` | library implemented | variant config、solver/verifier tests |
| Scalar implied volatility | `bsm_iv_scalar_v1` | library implemented | variant config、IV contract/tests |
| Combined market-implied IV + Greeks | `bsm_market_implied_greeks_v1` | portable verified | [`20260813_prompt_v2_100`](../../task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100/) |
| 六个单指标、静态输入 | `MetricSpec` × 6 | portable suite verified | [`20260814_metric_6x4_unique_db`](../../task_packages/deliveries/bsm_market_implied_metric_suite_v1/20260814_metric_6x4_unique_db/) |
| 六个单指标、DuckDB query v3 | `METRIC_SPECS_DB_QUERY_V3` × 6 | portable suite verified | [`20260814_metric_6x4_db_query_v3`](../../task_packages/deliveries/bsm_market_implied_metric_suite_v1/20260814_metric_6x4_db_query_v3/) |

query-v3 迁移结果与验证口径见 [`bsm_public_duckdb_query_v3_migration.md`](../../task_packages/reports/bsm_public_duckdb_query_v3_migration.md)。该报告记录了当时的全量回归基线 `506 passed`；这是已有报告中的证据，不表示每次阅读本计划时都重新运行了测试。

### 2.3 当前尚未实现的能力

[`solver/mc/`](../../src/synthetic_derivatives/solver/mc/) 仍是 scaffold。当前没有一套完整的：

- estimator API；
- deterministic draw bank；
- MC task contract；
- 受限 runtime/tool interface；
- 独立 verifier oracle；
- package materializer；
- end-to-end tests。

因此 L3 MC 不能加入 executable registry，也不能被 curriculum 采样。Heston、local vol、mixture、jump、rates/hybrid 等也仍是 catalog/design-space 条目，不是当前 runtime capability。

## 3. 重构目标与非目标

### 3.1 目标

本轮重构完成后，应满足：

1. `tdgbm_bsm` 被显式注册，并准确描述 dynamics、pricing、measure mapping 与 state contract；
2. task 的 model family、能力族、具体目标和 solver interface 是相互独立的身份；
3. [`derivatives_v2.json`](../../configs/task_space/derivatives_v2.json) 继续作为广义设计 catalog，但不能单独授权执行；
4. executable capability 必须有 generator/solver/verifier/package/runtime/test evidence；
5. authoring pipeline 通过显式 registry 解析 backend，当前输出保持等价；
6. scheduler 在采样前经过 capability gate，不能抽到只存在于 catalog 的 family/task；
7. 普通 mutation 不能通过修改 `M` 跨 model family；
8. 当前已验收 delivery、manifest、hash、task ID、runtime contract 和 release view 不被改写。

### 3.2 非目标

本轮不做：

- 不把 `authoring/`、`solver/`、`verifier/`、`packaging_*`、`training/` 一次性搬入新的 `common/`/`families/` 目录；
- 不为 Heston、local vol 等尚未实现的 family 创建空 backend 或虚假 executable 声明；
- 不把已有 package/query protocol v3 当作新的 semantic `TaskSpec v3`；
- 不重新生成或重写 100-task、6×4 static、6×4 query-v3 delivery；
- 不重复实现已经完成的单指标参数化；
- 不把 L3 MC 或第二个 model family 纳入本轮初始 Definition of Done；
- 不共享 Solver 与 Verifier 的数值 oracle。

## 4. 必须分开的四类身份

当前 [`TaskSpec`](../../src/synthetic_derivatives/task_space/models.py) 主要包含 `task_family_id`、coordinates、snapshot、method 和 output contract。新 semantic task schema 应把以下四类身份正交化：

| 字段 | 决定什么 | 当前示例 |
|---|---|---|
| `model_family_id` | underlying dynamics、pricing model、measure mapping、state contract | `tdgbm_bsm` |
| `task_family_id` | 能力/工作流大类 | `market_implied_metric` |
| `task_kind_id` | 一个具体 target 或 bundle | `delta`、`rho_1pct`、`core_greeks_bundle` |
| `solver_interface_id` | agent 可见数据、工具协议和提交方式 | `read-only-duckdb-query-schema-submit-v3` |

示例：

```yaml
task_id: tdgbm-bsm-market-delta-001
model_family_id: tdgbm_bsm
task_family_id: market_implied_metric
task_kind_id: delta
solver_interface_id: read-only-duckdb-query-schema-submit-v3

coordinates:
  L: 5
  P: 0
  M: 0
  A: 1
  D: 4
  R: 1
  F: F0

snapshot_id: DERIVATIVES-METALS-LIQUID-BSM-v1
snapshot_revision: 1
method_id: bsm-mid-iv-bisection80-analytic-delta-v1
output_contract_id: bsm-market-implied-delta-submission-v2.0.0
```

这里的 `task-v3.schema.json` 是 semantic task identity 的下一版本；它与仓库现有 package schema、runtime schema、toolset schema或 DuckDB query interface 中的“v3”没有继承关系。两类版本必须分别命名、分别迁移，不能因为数字相同而静默互换。

七维坐标 `(L,P,M,A,D,R,F)` 继续用于难度、结构、compatibility、lineage 和 diagnostics，但不再单独负责 runtime dispatch。尤其是：

- `M` 由 `model_family_id` 派生并校验；
- ordinary mutation 不允许修改 `M`；
- family、task kind、method、interface 或 output contract 的变化必须进入 deterministic task identity 与 lineage。

## 5. 最小新增架构

### 5.1 只增加 sidecar registry，不先移动实现

第一阶段新增：

```text
configs/model_families/
└── tdgbm_bsm_v1.json

configs/task_space/
└── executable_capabilities_v1.json

schemas/
├── model-family-v1.schema.json
├── executable-capability-v1.schema.json
└── task-v3.schema.json

src/synthetic_derivatives/model_families/
├── __init__.py
├── contracts.py
└── registry.py
```

暂不新增一份内容高度重复的 `derivatives_catalog_v3.json`。现有 [`derivatives_v2.json`](../../configs/task_space/derivatives_v2.json) 先明确标注为 design catalog；只有当 catalog 自身需要不兼容的 schema 变化时再升级。

### 5.2 Model-family contract

建议的最小 Python contract：

```python
@dataclass(frozen=True)
class ModelFamilySpec:
    model_family_id: str
    underlying_dynamics_id: str
    pricing_model_id: str
    measure_mapping_id: str
    state_contract_id: str
    model_coordinate: int
```

JSON 只保存声明式 identity 与 evidence ID。不要允许 JSON 提供任意 Python import path；所有 backend 都由显式 Python registry 解析，便于静态搜索、安全审计和测试覆盖。

### 5.3 Executable capability contract

capability 的唯一键至少包含：

```text
(
  model_family_id,
  task_family_id,
  task_kind_id,
  method_id,
  solver_interface_id
)
```

每条 capability 还要声明：

```yaml
status: portable_verified
output_contract_id: bsm-market-implied-delta-submission-v2.0.0
evidence:
  authoring_backend_id: tdgbm_bsm_authoring_v1
  solver_id: bsm_market_implied_delta_v1
  verifier_id: bsm_market_implied_delta_verifier_v1
  package_materializer_id: portable_metric_suite_v1
  runtime_contract_id: read_only_duckdb_query_v3
  test_ids: []
```

建议允许的状态至少为：

| 状态 | 含义 | 是否可进入 portable task pool |
|---|---|---:|
| `catalog_only` | 仅设计上合法 | 否 |
| `library_implemented` | 库内 solver/verifier 已实现，但尚未有完整 portable package evidence | 否 |
| `portable_verified` | generator/solver/verifier/package/runtime/tests 闭环完成 | 是 |

运行入口不得从 catalog 条目或一个笼统的 `implemented: true` 推断能力。缺少任一必需 evidence 时必须 fail closed。

### 5.4 当前 capability 映射

初始 registry 应显式映射当前事实，而不是重写现有 config：

| `model_family_id` | `task_family_id` | `task_kind_id` | interface | 初始状态 |
|---|---|---|---|---|
| `tdgbm_bsm` | `analytic_greeks` | `core_greeks` | current library interface | `library_implemented` |
| `tdgbm_bsm` | `implied_volatility` | `scalar_iv` | current library interface | `library_implemented` |
| `tdgbm_bsm` | `market_implied_metric_bundle` | `core_greeks_bundle` | current static v2 package interface | `portable_verified` |
| `tdgbm_bsm` | `market_implied_metric` | `iv` / `delta` / `gamma` / `vega_1volpt` / `theta_1calendar_day` / `rho_1pct` | current static interface | `portable_verified` |
| `tdgbm_bsm` | `market_implied_metric` | 同上六类 | `read-only-duckdb-query-schema-submit-v3` | `portable_verified` |

这层可以通过 compatibility adapter 引用现有 variant/package identity；不能修改已验收 package 中已冻结的旧名称。

## 6. 复用现有单指标实现，不重写 packaging

[`metric_specs.py`](../../src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/metric_specs.py) 已经承担了 BSM-local task-kind registry 的核心职责，包括：

- target/display/output field；
- variant、method、schema、verifier 和 task version；
- DB schema、task ID prefix/pattern 和单位；
- query-v3 派生 identity。

因此第一阶段应增加 adapter，把 `MetricSpec` 映射为 semantic task/capability identity；不要复制六份公式，也不要另建一套竞争性的 metric registry。

[`portable_metric_suite.py`](../../src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv/portable_metric_suite.py) 已经处理静态/query-v3 materialization、唯一性、leaf/suite validation 和原子发布。当前应保留：

- 现有 `packaging_analytic_and_implied_greeks_iv` import path；
- package/runtime/toolset v1/v2/v3 contracts；
- current verifier、negative tests、leakage 与 replay 语义；
- [`training/bsm_market_greeks.py`](../../src/synthetic_derivatives/training/bsm_market_greeks.py) 的 BSM-specific nine-field mapping。

不要在本轮预先抽取一个大型 `packaging/common` kernel。只有 L3 MC 或第二个真实 family 出现后，才能根据两套已工作的实现识别真正重复的 artifact lifecycle；数值公式、oracle、operation order 和 family-specific schema 始终不能进入 shared kernel。

## 7. Authoring backend 的低风险接入

当前 [`AuthoringPipeline`](../../src/synthetic_derivatives/authoring/pipeline.py) 直接实例化 [`UnderlyingDailyGenerator`](../../src/synthetic_derivatives/authoring/underlying_daily_generator.py) 与 [`OptionDailyGenerator`](../../src/synthetic_derivatives/authoring/option_daily_generator.py)。[`GeneratorConfig`](../../src/synthetic_derivatives/authoring/config.py) 中的 `pricing_model`、`pricing_engine`、`physical_process` 目前主要是 provenance，不是可靠的 backend dispatch。

先增加薄 adapter：

```python
class TDGBMBSMAuthoringBackend:
    model_family_id = "tdgbm_bsm"

    def create_underlying_generator(self, config):
        return UnderlyingDailyGenerator(config)

    def create_option_generator(self, config):
        return OptionDailyGenerator(config)
```

然后让 pipeline 通过 registry 解析 backend。该步骤必须保留当前：

- checked-in 1.6 base config 到 1.7 joint-parent materialization；
- deterministic time-inhomogeneous GBM under `P`；
- common `Q`/numeraire/rate-path identity；
- drift-only Girsanov mapping 与 same Brownian covariance；
- analytic BSM vanilla pricing；
- row content、logical checksum、contract selection 与 source assignment。

unknown family 或尚未实现的 backend 必须在生成前失败。adapter 等价性通过后，才考虑新增 family-aware config 2.0；不要把现有 config dataclass 原地扩成容纳所有 stochastic-vol/rate state 的巨型结构。

## 8. Capability gate、curriculum 与 mutation

### 8.1 先 gate，后 scheduler

当前 [`AdaptiveCurriculumScheduler`](../../src/synthetic_derivatives/curriculum/scheduler.py) 只接收 coordinates，而 [`adaptive_v2.json`](../../configs/curricula/adaptive_v2.json) 还列出 non-BSM、exotic 和 rates/hybrid 等规划阶段。新的任务池构造顺序应为：

```text
design catalog
    -> semantic task candidates
    -> executable capability gate
    -> family-local stage mapping
    -> replay/current/explore sampling
```

这样即使 catalog 判断某组 coordinates 在结构上 compatible，也不会让缺少 solver/verifier/package/runtime evidence 的任务进入 scheduler。

冻结 `adaptive_v2.json` 为 legacy；新增 family-aware 配置时只注册当前真正 implemented 的 `tdgbm_bsm` stage/task selectors。保留已有 20/60/20 replay/current/explore mixture 与 binary hard reward。

现有 curriculum 文档中的 L0–L8 与 `adaptive_v2.json` 的 stage 名称不是一一同构关系。迁移时需要显式 mapping tests，不能仅凭相同层级编号直接复用。

### 8.2 普通 mutation 保持 family 不变

冻结 [`deterministic_v2.json`](../../configs/mutations/deterministic_v2.json) 为 legacy。新的 family-aware mutation config：

- 从 mutable axes 中移除 `M`；
- 将 `model_family_id` 标记为 immutable；
- 由 family registry 派生并验证 `M`；
- family、task kind、method、interface、output 或 snapshot 的改变都进入 child identity/lineage；
- 修改算法必须带新 `method_id`；
- 修改输出必须带新 `output_contract_id`；
- 修改数据 regime 必须带新 snapshot 或显式 child DB lineage。

Heston-to-BSM、stochastic-rate-to-deterministic-rate 等跨 family 比较是独立 authored model-risk/counterfactual task，不是把普通 BSM task 的 `M` 改成另一个值。

## 9. 分阶段实施顺序

### 已完成基线：单指标与 query-v3 package

以下旧计划项已经完成，作为 regression baseline 保留，不再列入待办：

- 六个 target 的 `MetricSpec` 参数化；
- 每个 target 4 个独立实例；
- source task/DB/market content 和 derived task/DB 唯一性；
- static suite 与 DuckDB query-v3 suite；
- exact row-id set、key-aligned exact verification、单次 submit；
- `PORTABLE_SUITE_VERIFIED` 迁移报告。

### Phase 1：sidecar identity 与 capability registry

新增 model-family、capability 和 semantic task-v3 schema/registry，以及旧 identity 的显式 adapter；不改变任何 runtime 默认路径。

验收：

- `tdgbm_bsm` metadata 与 `M=0` 一致；
- 当前三个 variant 和三个 delivery baseline 均能映射；
- static 与 query-v3 interface 被区分；
- catalog-only family 不会被标为 executable；
- 已有 TaskSpec/package 不被原地迁移。

### Phase 2：在新任务入口启用 fail-closed capability gate

将 gate 接入 task-pool construction、family-aware scheduler 前置过滤和新 package entrypoint。旧 accepted package 的 replay/verification 路径保持兼容。

验收：

- 缺少任一 capability key/evidence 时拒绝；
- `MetricSpec` 六个 target 的 current static/query-v3 映射全部通过；
- Heston/local-vol/MC scaffold 无法进入 portable task pool；
- capability 决策有 deterministic diagnostics。

### Phase 3：Authoring backend dispatch

增加 `TDGBMBSMAuthoringBackend`，让 pipeline 通过显式 registry 创建现有 generator，不移动 generator 文件。

验收：

- 固定 config/seed 的 public logical rows 与 canonical checksum 不变；
- one-shot、append、sync-config 行为不变；
- P/Q、dependence、option-chain 与 quote-noise gates 不变；
- unknown/unimplemented family 在写 artifact 前 fail closed。

### Phase 4：family-aware curriculum 与 mutation

冻结 v2 配置，新增只面向新 semantic task 的 family-aware scheduler/mutation path。

验收：

- scheduler 必须先通过 capability gate；
- local stage mapping 有显式测试；
- 20/60/20 与 binary reward 不变；
- ordinary mutation 不能改变 family/`M`；
- child identity 覆盖 family、kind、method、interface、output 与 snapshot lineage。

### Phase 5：以 L3 MC 验证同一 family 内的扩展

L3 Monte Carlo 属于 `tdgbm_bsm` family 内的新 task family/method/interface，不是新 model family。它应独立实现 estimator、draw bank、runtime、verifier 和 package materializer，并保留与 analytic path 的独立数值证据。

只有闭环完成后，才把 MC capability 从 `catalog_only` 升为 `portable_verified`。此阶段不属于本轮初始 DoD。

### Phase 6：实现第二个真实 model family 后再抽 common

选择 Heston、local vol 或其他 family 时，必须一次交付其真实 state contract、authoring backend、solver、verifier、package/runtime 和 tests。比较两套工作实现后，再抽取已经证实重复的 contract parsing、hashing、release views、leakage checks 等基础设施。

不要先建空 family 目录，也不要为了目标目录树进行大规模搬迁。

## 10. 文件级变更建议

| 当前路径 | 本轮处理 | 延后处理 |
|---|---|---|
| `configs/task_space/derivatives_v2.json` | 明确为 design catalog，保持内容兼容 | 只有 schema 真正不兼容时才升级 catalog |
| `configs/task_space/` | 新增 `executable_capabilities_v1.json` | 第二个 family 完成后扩容 |
| `configs/model_families/` | 新增 `tdgbm_bsm_v1.json` | 按真实实现增加其他 family |
| `task_space/models.py` | 保留 current TaskSpec；新增 semantic v3 types/adapter | 评估旧入口退役条件 |
| `task_space/registry.py` | 分开 catalog compatibility 与 executable gate | capability evidence 自动审计 |
| `authoring/pipeline.py` | 改为显式 backend dispatch | 有第二个 backend 后再重组目录 |
| `authoring/*generator.py` | 原路径保留，薄 adapter 调用 | 有迁移收益时提供兼容 re-export |
| `curriculum/scheduler.py` | 新路径读取完整 semantic identity | 多 family sampling weights |
| `mutation/engine.py` | 新路径增加 family/`M` immutability | authored cross-family workflows |
| `packaging_analytic_and_implied_greeks_iv/metric_specs.py` | 作为 task-kind/capability adapter 来源 | 不复制六套 registry |
| `packaging_analytic_and_implied_greeks_iv/portable_metric_suite.py` | 保持当前 materializer | L3/第二 family 后评估 common kernel |
| `training/bsm_market_greeks.py` | 保持 BSM-specific mapper | 有第二个 mapper 后抽 outer record common 层 |
| `solver/mc/` | 继续标为 scaffold/catalog-only | Phase 5 完成完整闭环 |

## 11. 必须保护的已有行为

1. `P` 与 `Q` 测度明确分离，physical drift 不进入 Q-pricing；
2. 当前 drift-only Girsanov mapping 下的 P/Q Brownian covariance 身份；
3. option generator 不直接读取 underlying dependence/path-transition 私有接口；
4. frozen absolute strikes、stable contract IDs 和 static listing semantics；
5. quote noise 只作用于 bid/ask half-spread，BSM mid 不变；
6. visible midpoint → exactly 80-step IV inversion → analytic metric；
7. 不 regression `d1`、`d2` 或 BSM pricing terms；
8. Solver/Verifier 数值实现独立；
9. canonical exact comparison，不以 tolerance 替代 hard verifier；
10. fixed method、precision、operation order、rounding 与 serialization；
11. public-only child DB、recursive leakage gate 和 read-only replay；
12. query-v3 中 row order 无语义，必须验证 exact row-id set 并按 key 对齐；
13. accepted package 的 hashes、views、artifact identity 和 immutability；
14. F0 Greeks/IV task 不引入 arbitrage business outputs；
15. curriculum/mutation 不修改二值 verifier reward。

## 12. 回归与验证门槛

### 12.1 Phase 1–4 必须新增的测试

建议覆盖：

```text
tests/unit/test_model_family_registry.py
tests/unit/test_executable_capability_registry.py
tests/unit/test_task_v3_identity.py
tests/unit/test_family_curriculum.py
tests/unit/test_family_mutation_guards.py
tests/unit/test_authoring_backend_registry.py

tests/integration/test_tdgbm_bsm_backend_equivalence.py
tests/integration/test_catalog_runtime_separation.py
tests/integration/test_task_identity_adapter.py
tests/integration/test_family_capability_gate.py
```

核心 case：

- duplicate/unknown/unimplemented family；
- `model_family_id` 与 `M` 不一致；
- catalog compatible 但 executable unavailable；
- method/interface/output identity 冲突；
- current `MetricSpec` 六个 target 的双 interface 映射；
- cross-family ordinary mutation rejection；
- current authoring backend 的 golden equivalence。

### 12.2 不可变 artifact gate

Phase 1–4 不得改动：

```text
task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100/
task_packages/deliveries/bsm_market_implied_metric_suite_v1/20260814_metric_6x4_unique_db/
task_packages/deliveries/bsm_market_implied_metric_suite_v1/20260814_metric_6x4_db_query_v3/
```

合并前至少验证：

- `git diff -- task_packages/deliveries/` 为空；
- existing package/runtime/toolset schema digests 不变；
- current task IDs、DB bindings、manifest hashes 和 release views 不变；
- authoring rows、logical checksum、contract/source assignment 不变；
- current solver/verifier/package/runtime/leakage/replay tests 全部通过；
- 全量 `pytest` 与 `git diff --check` 通过。

### 12.3 独立性门槛

任何抽象都不得：

- 让 Solver 调用 Verifier 数值实现，或反向调用；
- 把 family-specific pricing、root finding、Greeks、MC reduction、calibration oracle 放入 shared utility；
- 让 package builder 根据 catalog 猜测 runtime capability；
- 通过默认值静默补齐缺失的 family/kind/interface identity。

## 13. 风险与规避

| 风险 | 规避措施 |
|---|---|
| `gbm_bsm` 命名过宽，未来模型身份冲突 | 使用 `tdgbm_bsm`，分别持久化 dynamics/pricing/mapping/state identity |
| 把 package/query v3 与 semantic TaskSpec v3 混淆 | schema 名称、adapter 和迁移测试完全分开 |
| catalog 声称支持尚不存在的模型 | 独立 executable capability registry，入口 fail closed |
| 预先设计 generic packaging 导致错误抽象 | 复用 `MetricSpec`/`portable_metric_suite`，等 L3 或第二 family 后再抽取 |
| family dispatch 改变 frozen authoring 输出 | 薄 adapter + row/checksum/source-assignment golden equivalence |
| mutation 通过 `M` 偷渡跨模型变化 | 新配置移除 `M`，family immutable，跨 family 使用 authored task |
| scheduler 抽到 non-BSM 规划条目 | capability gate 位于 scheduler 之前 |
| 新 identity 改写 accepted artifact | sidecar mapping 和显式 adapter；旧 package 不原地迁移 |
| 不同 metric 变体跨 split 泄漏 | 继续按 latent parent/snapshot/scenario lineage 分组 |

## 14. 本轮初始 Definition of Done

- [ ] `tdgbm_bsm` 成为显式、可校验的 model family；
- [ ] model family、task family、task kind、solver interface 四种身份分离；
- [ ] semantic TaskSpec v3 与 existing package/query v3 明确区分；
- [ ] current TaskSpec、variant 和 delivery 通过显式 adapter 映射，未被改写；
- [ ] design catalog 与 executable capability 分离；
- [ ] current static/query-v3 六 target capabilities 被准确登记；
- [ ] MC、Heston、local-vol 等不完整能力 fail closed；
- [ ] current generator 通过 `TDGBMBSMAuthoringBackend` registry dispatch；
- [ ] authoring rows/checksum/source assignment 与现状一致；
- [ ] scheduler 在 family-local stage mapping 前执行 capability gate；
- [ ] ordinary mutation 无法改变 model family 或 `M`；
- [ ] current three delivery baselines 与全部 accepted artifact byte-identical；
- [ ] Solver/Verifier 数值独立性没有降低；
- [ ] 新增 unit/integration tests、全量 `pytest` 和 `git diff --check` 通过。

以下明确不属于本轮初始 DoD：

- L3 MC 升级为 `portable_verified`；
- 第二个 model family；
- 大规模目录迁移；
- generic packaging/training kernel；
- 新一轮 18–30 个单 Greek package 生成。

## 15. 最终实施建议

最稳妥的路径是“先建立身份和门禁，再增加 dispatch，最后用真实新增能力验证抽象”：

1. 以 sidecar 方式登记 `tdgbm_bsm` 和 current capabilities；
2. 复用 `MetricSpec` 与现有 package evidence，不重写 accepted artifacts；
3. 在新任务入口加入 fail-closed gate；
4. 用薄 authoring adapter 证明 dispatch 不改变数据；
5. 冻结 legacy curriculum/mutation，为 semantic v3 task 增加 family-aware 新路径；
6. 以 L3 MC 作为同一 family 的第二种真实算法路径；
7. 只有第二个真实 model family 落地后，才按证据抽取 common infrastructure。

这使重构首先解决当前仓库真实存在的身份与 capability 问题，同时保护已经完成的 BSM 单指标、query-v3 和 immutable delivery 基线。
