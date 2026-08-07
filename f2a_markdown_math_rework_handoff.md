# F2A Markdown / Math 返工交接单（给 Codex）

## 0. 工作范围与硬约束

- Repository: `ruihuabunny/FinMaths-synthetic-data-demo`
- Branch: `f2a-arbitrage-task`
- 审计日期: `2026-08-07`
- 只在 `f2a-arbitrage-task` 工作，不要切到或修改 `main`。
- 不要重建、append、sync、覆盖或 mutation 已冻结的 parent：
  `snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/parent.duckdb`。
- 当前任务首先是修正文档和 versioned contracts。除非另有明确授权，不要物化 F2A child/dataset，也不要把尚未完成的 catalogue v3 写成“已经可运行”。
- 修改前先列出计划和受影响文件；修改后给出 diff 摘要、测试结果和仍然 blocking 的项目。

## 1. 审计结论

总体设计没有被全部改坏：三类 arbitrage family、两个 logical point mutation、`000` control 加七种 positive signatures、单期限的 cross-sectional/cross-asset 现金流公式，大方向和主要代数都正确。

但当前 Markdown 还不能当作可直接实现的完整数学规范，原因有四类：

1. master framework 与 F2A task plan 对 `s = 0` 的套利判定互相冲突；
2. transaction-cost-aware calendar catalogue 缺少完整、可编码的现金流与边界证书；
3. target catalogue v3 仍混用现有 v1/v2 identity 和文件名，存在原地改变已声明合同的风险；
4. 多处把“spot mutation 不改变 cross-sectional bit”错误加强成“spot-only child 永远不能有 cross-sectional bit”。

当前 JSON skeleton 的 `calendar_family = null` 是正确的 blocking 状态，不能为了让文档看起来完成而直接打开。

## 2. 已核对正确的内容：不要随意改公式

### 2.1 Family 与 signature

Canonical order 保持：

```text
cross-sectional, cross-asset, calendar
```

目标 signature 保持：

```text
000 -> []
100 -> ["cross-sectional"]
010 -> ["cross-asset"]
001 -> ["calendar"]
110 -> ["cross-sectional", "cross-asset"]
101 -> ["cross-sectional", "calendar"]
011 -> ["cross-asset", "calendar"]
111 -> ["cross-sectional", "cross-asset", "calendar"]
```

这是 target feasibility set，不是当前 runtime 已经覆盖的能力。是否七种 positive signatures 在 baseline execution profile 下都可达，必须由 pilot feasibility audit 证明。

### 2.2 两个 logical mutation operator

保持：

```text
mutate_option_price_point_v1
mutate_underlying_spot_point_v1
```

Option mutation 只改变一个 logical `(valuation_date, option_id)` quote point，并从 parent half-spread 确定性派生：

```text
child.mid = parent.mid + delta_ticks * option_increment
child.bid = child.mid - (parent.mid - parent.bid)
child.ask = child.mid + (parent.ask - parent.mid)
```

一个 logical quote point 派生三个 public fields，`realized_chi_F` 仍为 1。Spot mutation 只改变 child 的 valuation-time `spot_close`，不改 option quotes。

### 2.3 Transaction-cost-aware terminal-spot primitive

在以下明确假设下，task plan 中的 `A_S/B_S` 公式是对的，不要退回 frictionless `S exp(-qT)`：

- `q(u)` 是 deterministic、continuous、non-negative proportional cash-distribution yield；
- 每次 underlying buy/sell 都按单边比例成本 `kappa = 0.0005`；
- dividend reinvestment/financing 与 terminal liquidation 都逐笔计费。

```text
A_S(t,T) = S_t * (1+kappa)/(1-kappa)
             * exp(-Q_q(t,T)/(1+kappa))

B_S(t,T) = S_t * (1-kappa)/(1+kappa)
             * exp(-Q_q(t,T)/(1-kappa))
```

其中 `Q_q(t,T) = integral[t,T] q(u) du`。这些是 finite-variation share schedules，不是 BSM delta hedge。

### 2.4 单期限 candidate 公式

以下公式已逐项检查，方向、multiplier、option fee、funding 和 terminal payoff 都一致：

- cross-asset discounted call/put bounds；
- executable put-call parity 的两个方向；
- call/put strike monotonicity；
- nonuniform-strike convexity 的最小整数比例
  `(K3-K2):(K3-K1):(K2-K1)`。

不要把这些改成“quote 与 BSM theoretical value 的差”，也不要恢复 raw same-strike maturity ordering。

### 2.5 Drift / volatility

没有发现当前 Markdown 把 F2A/ordinary successor 的 physical drift 写成 constant drift。正确语义是：

- scalar legacy 配置等价于 constant function；
- 当前 successor/F2A 配置使用 deterministic piecewise-linear physical drift/volatility；
- 每个 actual-calendar interval 对 drift 精确积分取平均、对 variance 精确积分取 RMS；
- `girsanov_drift_only` 表示当前 baseline 下换测度改变 drift、保持同一个 deterministic diffusion function，不表示 drift 是常数，也不表示 physical volatility 按定义等于 IV。

请不要把这部分“修正”为 constant drift。

## 3. 必须返工的问题

### P0-1：统一 canonical arbitrage predicate

问题：F2A task plan 已正确区分两种零边界，但 master framework、Solver README 和当前 configs 仍统一使用 `candidate_spread > 0`。

受影响文件：

- `docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md`
  - 当前把所有 template 都写成 `s_max > 0`；
  - 当前把 `arbitrage_type` 写成所有 `s_j > 0` candidate 的并集。
- `environments/solver/README.md`
  - 仍把 `candidate_spread > 0` 称为统一 decision rule。
- `configs/variants/bsm_arbitrage_finding_f2a_v1.json`
  - `decision_rule = candidate_spread_strictly_greater_than_zero`。
- `authoring/configs/f2a_dataset_v1.json`
  - `canonical_decision_rule` 仍是同一个过时字符串。
- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
  - 这里的第 5.1 节是本轮应统一到其他合同的正确基准。

统一为 candidate-specific predicate：

```text
candidate_is_arbitrage_j =
  (s_j > 0 and g_j >= 0 P-a.s.)
  or
  (s_j == 0 and g_j >= 0 P-a.s. and P(g_j > 0) > 0)
```

其中：

- put-call parity 的 future payoff 恒为零，所以必须 `s_j > 0`；
- bounds/monotonicity/convexity 的 future payoff 非负且在公开 support contract 下非恒零，所以 `s_j >= 0` 即可；
- calendar candidate 必须由其完整 pathwise certificate 决定 `g_j >= 0` 和 strict-gain 条件，不能只看 initial surplus。

要求：不要只改 Markdown 文案。新 public candidate contract、private selector contract、lineage schema const 和 tests 必须同步 version；不能在已有 immutable ID 下静默换 predicate。

### P0-2：calendar catalogue 还不是 executable spec

核心文件：

- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
- `README.md`
- `src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md`
- `src/synthetic_derivatives/authoring/README.md`
- `snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md`
- `docs/authoring_pipeline.md`

现有第 5.3 节给了思路，但缺少下列可编码合同：

1. 明确的 setup cost / initial surplus 公式，包含每一条 `T1`/`T2` option leg、`Phi_{t,T1}`、option fees 与 directional bid/ask。
2. 明确的 `T1` cash account 更新式：短期限 option settlement、第一段 terminal exposure、第二段 `Phi_{T1,T2}` 建仓 cash outflow，以及从 `T1` 到 `T2` 的精确 accumulation factor 和 operation order。
3. 完整的 `g_j(S_T1,S_T2)` 或 `W_T2(S_T1,S_T2)` 公式，明确 initial surplus 是否被排除在 certificate payoff 之外，以及如何存入 numeraire。
4. `Delta_1(S_T1)` 在 strike knots 上的唯一边界归属规则（例如左闭右开、最后一格闭合）。只检查 one-sided limits 不等于检查 Borel rule 在边界点的实际取值。
5. 二维 unbounded cells 的 recession cone / extreme-ray slope 条件和 finite-vertex 检查顺序；不能只写“tail slopes”而不给算法。
6. 实际 finite position grids、`Delta_0` grid、`Delta_1` grid、max absolute position、gcd normalization、state partition、tie-break 和 binary64 cast/reduction order。
7. self-financing、admissibility 和 discounted-wealth lower bound 的逐现金流证明测试。
8. nonzero/open-cell strict-gain 的精确定义，使 `P(g>0)>0` 能由公开 `P ~ Q` 和 full conditional support 推出。

在以上内容、实现和 proof tests 完成之前，必须保持：

```text
calendar_family = null
```

文档引用的 arXiv:2607.27649 使用 bounded absolute stock spread、reference/shadow geometry。它可以作为结构启发，不能充当本仓库 proportional-notional cost、dividend segment primitive 的证明。

### P0-3：不能在现有 v1/v2 identity 下原地打开 catalogue v3

问题：target plan 写 `candidate_catalogue = bsm-f2a-candidate-catalogue-v3`，但路径表仍指向：

```text
configs/variants/bsm_arbitrage_finding_f2a_v1.json
authoring/configs/f2a_dataset_v1.json
schemas/f2a-lineage.schema.json
```

当前实际状态是：

- variant ID: `bsm-arbitrage-finding-f2a-v1`；
- candidate catalogue ID: `bsm-f2a-candidate-catalogue-v2`；
- lineage schema `candidate_catalogue_id.const` 也是 v2；
- calendar disabled；
- dataset selector 仍是 positive/negative balance。

返工要求：

- 明确哪些 identity 可以保持、哪些必须升版；
- 至少为新的 public variant/candidate catalogue 使用新的 immutable ID；
- 若修改 private dataset contract、lineage required shape 或 mutation semantics，也要 version 对应 config/schema/engine ID；
- 文档路径表必须显示新旧并存及 migration/compatibility 规则，不能一边说“不能静默启用”，一边指示 Codex 覆盖同一个 `_v1.json`。

### P0-4：spot-only 的结论写得过强

正确不变量是：

```text
spot mutation leaves every option-only cross-sectional candidate unchanged
X_after = X_before
```

它不自动推出 `X_after = false`。如果 clean slice 在 mutation 前已经有 cross-sectional hit，spot-only child 会保留这个 bit。

当前错误/过强表述出现于：

- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
  - “oracle 给出该 type 就是实现不一致”；
  - “spot-only child 永不产生 cross-sectional type”。
- `src/synthetic_derivatives/authoring/README.md`
  - “scan 得到 cross-sectional bit 应视为 failure”。
- `snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md`
  - “若 scan 得出该 bit，materialization 必须失败”。
- `src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md`
  - “spot-only child 永不产生 cross-sectional type”。
- `README.md`
  - 当前“不能直接激活”措辞较接近正确，但需要与上述文件统一为 exact invariance。

修复方案二选一并明确写入合同：

1. 推荐：先独立扫描 clean slice，要求 `X_before = false`（最好要求完整 clean signature `000`），不满足就 deterministic skip；此后才可推出 spot child 的 `X_after = false`。
2. 或允许 baseline 已带 X 的 slice，此时 spot operator 可生成含 X 的 realized signature，但 X 不是 spot mutation 激活的。

不要把“cannot directly change”继续写成无条件 “cannot have”。增加 `X_after == X_before` 的 unit/property test。

### P1-1：selector 的 margin/guard 定义不完整

核心位置：`src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md` 第 4.4 节。

问题：

- `s_j(n;theta) = alpha + beta*n*Delta - TC_j(theta)` 把 bid/ask、fee、underlying cost 混进一个笼统 `TC`，容易重复扣费，也不保证所有 operator/candidate 全局 affine；
- `m_f = max s_j` 只看 initial surplus，不能代表 calendar terminal certificate 是否通过；
- parity 的触发边界是 `s > 0`，非恒零非负 payoff template 的边界是 `s >= 0`，不能用一个未定义的“nearest triggering surplus”混过去；
- calendar inactive/active separation 还需要 terminal payoff/cell slack，不能只用 USD initial surplus；
- scalar multiples 若未全部 gcd-normalize，会让较大 position vector 机械放大 surplus 和 guard，扭曲 family margin。

返工要求：

- selector 直接调用 candidate-specific exact cashflow evaluator，不以统一 affine 假设代替；
- 先计算 terminal certificate，再定义 eligible candidate 的 initial-surplus distance；或定义包含 setup slack 与 terminal-cell/ray slack 的 guard vector；
- 为 parity 与 nonconstant-payoff templates 明确不同的 open/closed trigger boundary；
- 所有可缩放 position vectors 去除正整数倍重复并固定 normalization；
- active/inactive guard 只是 sample-selection rule，不进入 canonical verifier predicate。

### P1-2：mutation config 字段和 domain gate 与计划不一致

受影响文件：

- `configs/mutations/f2a_point_v1.json`
- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
- `src/synthetic_derivatives/authoring/README.md`

具体问题：

- spot operator 的 public child 字段是 `spot_close`，但 config 的 `logical_field` 是 `spot`；
- `child_option_price_in_model_domain` 会被理解为 BSM no-arbitrage/model-price domain，与计划要求的 numeric/quote domain 冲突；
- section 4.1 仍写“第一个越过目标边界且 positive spread 超过 guard”，与 section 4.4 的 exact realized-signature + 双边 guards selector 不完全一致。

返工要求：用 versioned mutation contract 明确 `spot_close`，把 gate 改成精确的 finite/nonnegative/side-order/tick-alignment 语义；不得用 BSM bounds/parity/convexity 把 task signal clip 回去。

### P1-3：public support、time origin 和 settlement timeline 仍缺合同字段

task plan 已正确把这些列为 blocking，但当前 public variant 未完整 version：

- future `P ~ Q` / null-set contract；
- positive conditional support；
- Q-volatility node `time_origin`、calendar-day offsets、units、interpolation/extrapolation；
- valuation timestamp、expiry timestamp、`T1` settlement/rebalance 和 `T2` liquidation 的顺序；
- dividend curve 必须是 cash-distribution yield，而不是 borrow/carry quote。

受影响文件/合同：

- `configs/variants/bsm_arbitrage_finding_f2a_v1.json`
- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
- `docs/authoring_pipeline.md`
- `src/synthetic_derivatives/authoring/README.md`
- `snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md`

这些必须成为 Solver-visible public contract，不能只由 verifier 私下假设。

### P1-4：七种 positive signatures 的可达性写得太像既成事实

当前 plan 一方面承认某些 signature 可能不可达，另一方面又把“feasibility set 覆盖七种 positives”写成无条件 integration acceptance。

修复：

- 先对 baseline execution profile 做 deterministic reachability audit；
- 输出每个 requested signature 的 reachable target/operator/tick windows 和不可达证明/诊断；
- 不可达时不要随机 retry、扩大 grid、修改 parent 或按 label 临时改 fee；
- 若新增 public execution profiles，必须先 version、在 mutation 前选择，并跨多个 signatures 平衡使用，证明 profile 本身不泄露 label；
- 只有 audit 证明可达后，才把七种 positives 写成 dataset acceptance；否则明确缩小当前发布 scope 或继续 blocking。

### P2-1：重复 Markdown headings

文件：

`src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`

重复项：

- `## 5. Independent oracle 与候选策略` 连续出现两次；
- `## 6. LLM 输出与 ORM` 连续出现两次。

删除重复 heading，并跑 Markdown link/heading lint。

### P2-2：AGENTS.md 的 family 顺序/命名不一致

`AGENTS.md` 当前写 `(calendar, cross_sectional, cross_asset)`，而 public canonical order 是：

```text
cross-sectional, cross-asset, calendar
```

统一 spelling 与顺序，避免实现把 tuple position 当作另一种 bit order。并把 “positive exact net certificate” 明确链接/定义为 candidate-specific predicate，而不是统一 `spread > 0`。

## 4. Markdown 全量审计清单

本次按 branch 相对 `main` 的 Markdown 范围逐份检查。

### 需要直接返工或同步

- `AGENTS.md`
- `README.md`
- `docs/authoring_pipeline.md`
- `docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md`
- `environments/solver/README.md`
- `snapshots/generated/f2a/parents/DERIVATIVES-METALS-F2A-TICK-ALIGNED-TDGBM-Q-v2/README.md`
- `src/synthetic_derivatives/authoring/README.md`
- `src/synthetic_derivatives/mutation/f2a_arbitrage_finding_agent_task_plan.md`
- `src/synthetic_derivatives/task_space/duckdb_agent_task_plan.md`
- `tests/unit/README.md`（新增 math/property tests 后同步测试目录说明）

### 已检查，未发现与本轮 F2A 数学合同直接冲突

- `authoring/templates/README.md`
- `docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md`
- `snapshots/generated/README.md`
- `snapshots/generated/sql_query/README.md`
- `snapshots/public/README.md`
- `snapshots/public/sql_query/README.md`
- `tests/public/README.md`

不要为了“全量修改”去改第二组文件；只有 link/status 因实际合同升版而失效时才做最小同步。

## 5. 相关非 Markdown 合同（必须一起核对，但不要原地破坏 identity）

- `configs/variants/bsm_arbitrage_finding_f2a_v1.json`
- `authoring/configs/f2a_dataset_v1.json`
- `configs/mutations/f2a_point_v1.json`
- `schemas/f2a-lineage.schema.json`
- `schemas/trajectory.schema.json`
- `schemas/submission.schema.json`
- `tests/unit/test_f2a_repo_contracts.py`

当前 `test_f2a_repo_contracts.py` 主要验证路径、ID 和 skeleton shape，不能证明 candidate 数学、calendar cashflow、zero-boundary predicate 或 spot-invariance 正确。

## 6. 必须新增/加强的测试

1. `candidate predicate boundary`：
   - parity: `s == 0` -> false；
   - nonconstant nonnegative payoff: `s == 0` -> true；
   - `s < 0` -> false；
   - 不使用 tolerance。
2. `A_S/B_S cashflow proof`：逐时验证 initial shares、dividend reinvestment/financing、每次 proportional cost、terminal liquidation 和 exact terminal exposure。
3. `single-expiry candidates`：每条 bounds/parity/monotonicity/nonuniform-convexity 策略同时测 initial cashflow 与逐状态 payoff。
4. `spot invariance`：对同一 child 前后完整扫描，断言 `X_after == X_before`；若 policy 要求 clean `000`，另测不满足者 deterministic skip。
5. `calendar cashflow`：逐项验证 `t/T1/T2`、cash accumulation、segment costs、boundary assignment、所有 finite vertices、one-sided limits、actual boundary values 和 recession rays。
6. `selector`：requested signature exact match；active/inactive setup 与 terminal slacks；unreachable deterministic skip；不改 fee/grid/parent。
7. `identity/versioning`：旧 v1/v2 config 仍可重放；新 variant/catalogue/schema 使用新 ID；旧 ID 下 calendar 继续被拒绝。
8. `no label leakage`：public task/child/ID/schema 不含 requested/realized signature、operator、target、selector trace、mutation count 或 private margins。
9. `Markdown consistency`：禁止残留统一 `candidate_spread_strictly_greater_than_zero` 作为新 catalogue predicate；canonical family order 全仓一致。

## 7. 建议的搜索与验收命令

先定位旧语义：

```bash
rg -n "candidate_spread_strictly_greater_than_zero|candidate_spread > 0|s_j>0|calendar_family|spot-only|Spot-only|bsm-f2a-candidate-catalogue-v2" \
  AGENTS.md README.md docs environments snapshots src configs authoring schemas tests
```

修改后至少运行：

```bash
pytest -q tests/unit/test_f2a_repo_contracts.py
pytest -q tests/unit
pytest -q tests/public
```

若实现了新 F2A math/runtime tests，再单独列出并运行对应 test files。不要把现有 repo-contract tests 通过误报为 calendar proof 已完成。

## 8. Codex 最终回报格式

完成后请给出：

1. 修改过的 Markdown/config/schema/test 路径；
2. 每个 P0/P1/P2 finding 如何处理；
3. 新旧 immutable IDs 的映射；
4. 哪些公式保持未变，以及为什么；
5. 测试命令与完整 pass/fail 结果；
6. 仍然 blocking 的项目；
7. 明确确认 frozen parent 未被修改、没有生成或提交未经授权的 child/dataset。
