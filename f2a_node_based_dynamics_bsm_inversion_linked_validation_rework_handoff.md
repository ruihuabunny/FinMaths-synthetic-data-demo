# F2A v5.1 Node Dynamics、BS Inversion 与 Linked Validation 交接说明

## 决策

Stage 2 采用两个并行但用途严格分离的计算：

1. 对每条 solver-visible bid/ask midpoint 执行
   `bsm-bisection-float64-80-v1`，输出 quote-specific market IV 与由其派生的 market
   `d1/d2`；
2. 使用 Stage-1 三节点 instantaneous diffusion 的 exact remaining-horizon integrated variance，
   输出 linked BSM price、linked `d1/d2`、parameter SE 与 price residual。

Market-IV repricing 只回答 visible quote 的 BSM inverse problem。它不得成为 clean
counterfactual；localisation 与 X/U/T signal 继续使用 linked price residual。`d1/d2` 在两条路径上
都只是 volatility 与公开 pricing inputs 的确定性派生量。

## 数学边界

- 历史 ex-dividend spot path 属于物理测度 `P`；Stage-1 conditional law 是按实际 calendar
  interval 精确积分的 deterministic time-inhomogeneous GBM baseline。
- 当前 variant 显式声明 `sigma_Q(u)=sigma_P(u)`，只作为该 deterministic-diffusion model 的
  measure mapping。
- Pricing 在 `USD-MONEY-MARKET-Q-v1`、`USD-MONEY-MARKET-ACCOUNT-v1`、USD 与共同
  rate/carry context 下进行。
- Piecewise-linear diffusion 的 squared integral 跨所有 interior nodes 精确计算；一个 linear
  segment 使用 `Delta t * (a^2 + a*b + b^2) / 3`。
- Annualized market IV 是 valuation-to-expiry total variance 的 RMS 表示，不是某个时刻的
  instantaneous diffusion node。

## 数值边界

BS inversion 使用 `[1e-6, 5.0]`、IEEE-754 binary64、恰好 80 次 bisection、无 early stop、
无 fallback。每次按 `(low + high) / 2` 取 midpoint；price 低于 observed 时更新 low，否则更新
high；最终 root 为 `(low_80 + high_80) / 2`。先检查 discounted BSM bounds 与 finite bracket；
失败时只返回 `invalid_bracket`。不得写入非有限 IV 或伪造 root；该 row 从 v5 model-signal
candidates 中排除，但不使 authoring 失败。独立 v4 execution audit 不依赖 IV，仍全量扫描 public
quotes。Canonical output 在所有内部计算结束后才按十位小数 half-even 量化。

## 版本与接口

- variant schema：`5.1.0`；
- output：`model-reconstruction-xut-full-trajectory-v2`；
- schema：`schemas/submission-v5.1.schema.json`；
- Stage-2 entrypoint：`evaluate_option_series(...)`；
- top-level output：`option_series_results`；
- series status：所有 rows 均完成固定 procedure 并得到明确 status 时为 `COMPLETE`；
- model-signal eligibility：`market_iv_eligibility_rule=converged_rows_only`；
- model signal output：`linked_counterfactual_value`。

旧 `schemas/submission-v5.schema.json` 仅用于历史 pilot replay。新 public DuckDB 发布独立的
`bsm_inversion_contract` 与 `linked_diffusion_validation_contract`，不得用旧 task identity 包装
v5.1 output。

## 验收重点

- Solver 与 trusted verifier 独立实现并 canonical exact-match；
- invalid bracket rows 明确标记、无非有限输出且不进入 v5 model-signal scan；
- linked price 与 signal 不随 market-IV repricing 被重置；
- market/linked `d1/d2` 均可由各自 volatility object 重算；
- active v5 code/schema 不存在 free `d1/d2` coefficient fields；
- 同一 public quotes 与 linked Stage-1 result 下，X/U/T bitmask 保持原定义；
- frozen v4 execution audit 不变。
