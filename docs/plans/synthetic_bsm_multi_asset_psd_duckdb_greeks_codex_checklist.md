# Synthetic BSM multi-asset / PSD / DuckDB / Greeks：Codex 修改清单

> 目标分支：`synthetic-BSM-agent-task`  
> 仓库：`ruihuabunny/FinMaths-synthetic-data-demo`  
> 设计依据：`docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md`  
> 配套计划：`docs/plans/synthetic_bsm_multi_asset_psd_duckdb_greeks_plan.md`

## 给 Codex 的执行指令

只在 `synthetic-BSM-agent-task` 上继续工作。先阅读上面的框架文档和配套计划，再按本清单从 P0 到 P6 实施。不要 merge/cherry-pick 整个 F2A 分支，不要修改 `main`，不要改写任何已冻结 snapshot。

每完成一个阶段都运行该阶段测试；最终运行全量测试。若实现与框架文档冲突，以框架文档为准，并在提交说明中记录取舍。

## 已迁移基线（不要重复搬运）

- [x] Generator config 1.6：IV solver 不再属于 authoring market-data contract。
- [x] Underlying/option 分离的 minimum price increment 与 half-even tick quantization。
- [x] Tick-aligned initial state、OHLC、restart state 与 option quote canonicalization。
- [x] 旧 1.5 authoring-IV 配置只读保护；新写入使用 1.6 identity。
- [x] 当前 authoring 不再生成 `market.option_pricing_audit` 行；旧表只为读取历史 snapshot 保留。
- [x] `TABLE_SPECS` 不再把旧 IV audit 表当成当前 materialization contract。
- [x] Solver-visible pricing metadata 隐去 seed 和 deterministic-function node heights。
- [x] 通用 1.6 metals generator config、CLI/Makefile 默认值、replay/sampler 更新。
- [x] 七维 difficulty/task/mutation/curriculum JSON Schema 文件。
- [x] Solver 环境的最小 allowlist 基线（stdlib + pinned DuckDB；不提供衍生品现成答案包）。
- [x] 对应的通用 config/Q-pricing/snapshot tests；未迁移任何 F2A 业务测试。

## P0 — 拉取后先确认基线

- [x] `git branch --show-current` 必须输出 `synthetic-BSM-agent-task`。
- [x] `git status --short` 必须为空；不要在脏工作树里机械覆盖用户改动。
- [x] 建立 Python 3.12 环境并安装根目录 `requirements.lock`。
- [x] 运行 `make test`，记录通过/失败数量和首个失败。
- [x] 用 `configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json` 生成临时 DuckDB；不要写入 `snapshots/public/`。
- [x] 验证生成结果仍为 22 个 underlyings、65 个 business dates、1,232 个 option contracts、60,368 条 live option quotes。
- [x] 验证新 snapshot 的 `market.option_pricing_audit` 行数为 0，旧 checked-in snapshot 仍可只读查询且 byte-identical。

## P1 — 把相关性合同从“只有 P”补成 P/Q 明确分层

当前 `UnderlyingSimulationConfig` 和 `market.underlying_dependence` 的实际生成路径只完整覆盖 P-measure。目标是同币种 multi-asset BSM 市场在 P 和 Q 下都具有明确、可验证、PSD-by-construction 的 underlying-driver dependence；option contracts 不能成为相关矩阵的 driver。

- [x] 设计版本化的 measure-qualified dependence contract；至少明确 `P`、`Q`、driver order、regime、factor loading、idiosyncratic diagonal、correlation matrix、dtype 与 factorization order。
- [x] 对当前 `girsanov_drift_only + same_deterministic_diffusion` 合同，允许 Q 继承同一 Brownian covariance，但必须生成独立、显式的 Q metadata/identity，不能靠注释推断。
- [x] 保持公式 `R = ΛΛᵀ + D`，其中 `D = diag(1 - ||Λ_i||²)`；禁止事后 eigenvalue clipping/repair。
- [x] 校验 symmetry、unit diagonal、元素范围、finite values、row norms、driver uniqueness 和 exact declared ordering。
- [x] 明确拒绝 option/derivative IDs 出现在 P 或 Q driver order。
- [x] `solver_visible` 只公开完成任务所需的 P/Q correlation contract，不公开 seed、private loadings calibration provenance 或 latent node heights。
- [x] 增加 identity correlation 与合法 non-diagonal correlation 的边际不变性测试：固定所有单资产输入后，每个 vanilla option 的 BSM marginal price/surface 必须相同。
- [x] 增加 invalid loading、重复 driver、缺失 underlying、derivative driver、NaN/Inf、row norm > 1 的拒绝测试。

建议关注文件：

- `src/synthetic_derivatives/authoring/config.py`
- `src/synthetic_derivatives/authoring/underlying_daily_generator.py`
- `src/synthetic_derivatives/authoring/schema.py`
- `src/synthetic_derivatives/authoring/pipeline.py`
- `tests/unit/test_underlying_dependence.py`
- `tests/public/test_snapshot_schema.py`

## P2 — 导出独立的 solver-visible DuckDB

Authoring DB 和 task DB 必须是两个安全边界；不能仅依赖同一个文件里的 SQL view 作为最终发布隔离。

- [x] 新增 deterministic exporter，把 frozen authoring snapshot 投影为新的 public child DuckDB。
- [x] Public DB 只包含任务必需表，例如 snapshot/task context、underlying state、option contracts、option chain quotes、pricing context、P/Q dependence。
- [x] 不复制 `market` 私有 schema、generation audit、run IDs、seed、mutation lineage、authoring IV audit、hidden oracle 或 private function node heights。
- [x] 固定 table/column order、row order、DuckDB version、dtype、decimal scale、JSON canonicalization 与 export version。
- [x] 实现 canonical logical checksum；checksum 对物理文件布局和时间戳不敏感，对任一逻辑值改变敏感。
- [x] 实现 stable sample/task ID 和 deterministic subset manifest。
- [x] Export 前后做 leakage scan；递归检查嵌套 JSON，不能只检查顶层列名。
- [x] 同一 parent/config/exporter version 重放得到完全一致的 logical checksum。
- [x] Public DB 只能以只读方式交给 solver；verifier 可读取 parent，但 solver 不可读取。

可参考、但不要整文件复制 F2A：

- `src/synthetic_derivatives/authoring/f2a_database.py` 中的 logical checksum、stable sample ID、deterministic sampler 和 subset manifest 模式。

建议新文件：

- `src/synthetic_derivatives/export/solver_database.py`
- `src/synthetic_derivatives/export/contracts.py`
- `tests/unit/test_solver_database_export.py`
- `tests/integration/test_solver_database_replay.py`

## P3 — 通用 BSM 计算内核与 Greeks task contract

实现应是通用 BSM 模块，不能带 `f2a` 命名或套利语义。

- [x] 冻结输入合同：`spot > 0`、`strike > 0`、`tau > 0`、`sigma > 0`、continuous `r/q`、European call/put、spot Greeks。
- [x] 冻结 Greek convention：holding-what-fixed、单位、scaling、theta 时间单位、vega/rho 是每 1.0 还是每 1% 变化。
- [x] Solver 端只用 Python stdlib 基础数值原语实现 price、delta、gamma、vega、theta、rho；不得 import QuantLib/py_vollib/mibian/rateslib/scipy 的定价或 Greek API。
- [x] Trusted verifier 使用 pinned QuantLib `AnalyticEuropeanEngine` 独立复算。
- [x] 两端共享的只能是 schema/convention/canonicalization；不能共享实际 pricing/Greek implementation。
- [x] 固定 float64 运算顺序、cast checkpoints、decimal/字符串序列化和 canonical row ordering。
- [x] Canonical output 至少含 `task_id, valuation_date, option_id, price, delta, gamma, vega, theta, rho` 及 method/convention ID。
- [x] Call/put、ITM/ATM/OTM、短/长 maturity、低/高 vol、非零 dividend/rate 都要覆盖。
- [x] 加入 bounds、put-call parity、delta relation、gamma/vega positivity 等 invariant tests；这些是发布 gate，不替代独立 oracle。
- [x] `d1/d2` 只能作为瞬时内部变量；禁止把它们持久化为公开 label、feature、solver-visible metadata、目标列或回归捷径。

建议新文件：

- `src/synthetic_derivatives/solver/bsm.py`
- `src/synthetic_derivatives/tasks/bsm_greeks.py`
- `src/synthetic_derivatives/verifier/bsm_greeks.py`
- `schemas/bsm-greeks-output.schema.json`
- `tests/unit/test_bsm_solver.py`
- `tests/unit/test_bsm_greeks_contract.py`
- `tests/integration/test_bsm_greeks_verifier.py`

## P4 — 固定算法的 implied volatility（Greeks 稳定后再做）

- [x] 从 task/verifier config 定义 IV contract，不得放回 generator config。
- [x] 使用确定性 price bounds 和固定 volatility bracket。
- [x] 实现恰好 80 次 midpoint/bisection iteration；禁止 tolerance-based 提前退出。
- [x] 固定每步 comparison、midpoint、cast 和最终 endpoint/midpoint selection。
- [x] 明确 `OUT_OF_BOUNDS`、`NO_BRACKET`、invalid input 等 canonical status。
- [x] Solver 与 verifier 独立实现，最终 exact-compare canonical result/status。

可从 F2A 只提炼数学原语，不复制业务模块：

- `src/synthetic_derivatives/solver/f2a_stage2.py` 中的 normal CDF/PDF、BSM bounds/price、`BSMInversionContract` 和固定 80-step midpoint inversion。

## P5 — 七维 runtime 与 F0 默认值

实施前四个 v2 JSON Schema 已存在，但 Python runtime 仍是六维；本阶段只补齐结构，不实现 F2A 套利业务。

- [x] `AXES` 改为 `("L", "P", "M", "A", "D", "R", "F")`。
- [x] 更新 `TaskCoordinates`、serialization、stable task/child IDs、registry compatibility、mutation lineage、curriculum mastery/diagnostics。
- [x] 旧六维 task 在明确的 migration adapter 中补 `F0`；不能在多个调用点静默猜测。
- [x] 当前 BSM/Greeks task family 固定 `F0`。
- [x] JSON Schema 与 runtime enum 完全一致，并测试非法 F level、缺失 F、旧六维迁移和 deterministic IDs。
- [x] 不加入 arbitrage label、X/U/T、maximal spread、FP/FN calibration 或 F2A output projection。

建议关注文件：

- `src/synthetic_derivatives/task_space/models.py`
- `src/synthetic_derivatives/task_space/registry.py`
- `src/synthetic_derivatives/mutation/engine.py`
- `src/synthetic_derivatives/curriculum/scheduler.py`
- `schemas/difficulty-v2.schema.json`
- `schemas/task-v2.schema.json`
- `schemas/mutation-v2.schema.json`
- `schemas/curriculum-v2.schema.json`

## P6 — Solver 安全、集成验收与交付

- [x] Solver import audit 拒绝 QuantLib、现成 Greeks/IV/surface packages、动态安装、网络访问和私有 DB 路径。
- [x] Verifier 环境与 solver 环境分离；QuantLib 只能存在于 trusted authoring/verifier 环境。
- [x] 生成至少一个 small smoke task set，覆盖多个 underlyings、call/put、strike、expiry 和合法 non-diagonal Q correlation。
- [x] 从 public DuckDB 运行 solver，再由 trusted verifier exact-compare canonical output。
- [x] 两次独立 replay 的 task IDs、row order、checksums、prices、Greeks、IV（若已实现）完全一致。
- [x] 运行 `make test`，并新增一个不依赖 checked-in 私有 child 的 end-to-end temp-directory test。
- [x] 更新 README/authoring docs，只描述实际已实现功能；不要把 P-only dependence 写成 P/Q 都已实现。
- [ ] 最终提交按职责拆分，建议顺序：dependence contract → public DB export → BSM/Greeks → IV → seven-axis runtime → docs/tests。（本轮未创建 commit，留待人工 review 后处理。）

## 明确禁止迁移/实现

- [x] 不合并或 cherry-pick 整个 `f2a-arbitrage-task`、`f2a-2nd-revised`、`f2a-arbitrage-revised`。
- [x] 不迁移 `f2a_calibration.py`、`f2a_v5.py`、F2A child materializer、F2A mutation/operator/oracle/verifier/fixtures。
- [x] 不迁移 X/U/T、executable-arbitrage、transaction-cost certificate、calendar-arbitrage、maximal-spread、FP/FN calibration。
- [x] 不修改或重写 `snapshots/public/quantlib_bsm_smoke_v1.duckdb` 及任何 frozen parent。
- [x] 不把 authoring IV、Greeks、hidden truth、private parameters 或 seed 放进 solver-visible DB。
- [x] 不新增持久化的 `d1`/`d2` 目标、标签、公开中间表或回归任务。

## Definition of Done

- [x] 1.6 authoring snapshot 可 deterministic generate/freeze/replay，旧 1.5 IV-authored identity 只能读取。
- [x] 22-asset full option chain 与 P/Q PSD dependence contract 同时存在，derivatives 不进入 correlation matrix。
- [x] 独立 solver-visible DuckDB 无私有泄漏且 logical checksum 可重放。
- [x] BSM price + Greeks solver 与 pinned QuantLib verifier 逐字段 canonical exact equality。
- [x] 七维 runtime 对当前 task 使用 F0，旧六维有唯一、显式 migration path。
- [x] 全量测试通过；frozen snapshots 未改变；`main` 未改变。
