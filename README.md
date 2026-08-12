# FinMaths Synthetic Data Demo

面向 LLM 训练的可重放合成金融衍生品数据项目。项目把市场数据生成、任务定义、模型解题、
独立校验和训练数据导出划分为不同权限边界；固定完整配置、版本、seed、随机流与数值约定
后，同一 market snapshot 可以确定性重放。

当前可运行主线使用 QuantLib 生成物理测度 $\mathbb P$ 下的 underlying 路径和风险中性
测度 $\mathbb Q$ 下的 European option 报价，再由 DuckDB 事务化、增量地保存 snapshot。
仓库内置的 public metals snapshot 已冻结，包含 22 个 underlying、65 个 business dates、
1,232 个静态期权合约和 60,368 条有效期内报价。

## 当前状态

| 能力 | 状态 | 说明 |
|:---|:---:|:---|
| Authoring pipeline | 已实现 | 支持 create、range sync、append、NOOP、quality gates、revision、manifest 与 freeze。 |
| $\mathbb P$-measure underlying simulation | 已实现 | 分段线性 drift/volatility 按真实日历区间精确缩约；factor loading 只耦合 underlying shocks。 |
| Single-asset $\mathbb Q$ pricing | 已实现 | 共同 measure/numeraire/rate-path identity；authoring 只生成 canonical quotes，不生成 IV/Greek answers。 |
| Static option chain | 已实现 | 固定 listing strike、到期日/价内外筛选、可重放 bid/ask spread noise。 |
| Task space / mutation / curriculum | 最小版本已实现 | 七维 compatibility registry、显式旧六维迁移、确定性 lineage 与 adaptive sampling weights；当前任务固定为 F0。 |
| Public solver DuckDB export | 已实现 | Frozen parent → 独立 public child；稳定采样/任务 ID、subset manifest、logical checksum、递归 leakage scan 与只读打开。 |
| Analytic BSM/IV/Greeks Solver / trusted verifier | 已实现 | 标准库 price/Greeks/固定 80 步 IV 与 pinned QuantLib 独立 oracle 按 canonical decimal exact compare。 |
| BSM Greeks agent task package | 已实现 golden task | 8-underlying、160-row 三关系 DuckDB、自动渲染 prompt、有效 runtime allowlist、hidden pytest、reference trajectory 与三种 release views。 |
| Training export | BSM-specific 已实现 | Golden package 含 observable reference trajectory；Phase F runner 可把 verified packages 导出为九字段 JSONL。完整 100-task batch 尚未执行，跨 family 通用 ORM exporter 仍未实现。 |
| 多资产 $\mathbb Q$ dependence / joint payoff | 部分实现 | P/Q underlying-driver covariance 已显式分层；basket/index/spread payoff 尚未实现。 |

当前的 volatility mapping 是一个明确的 baseline 假设：Girsanov change of measure 只改变
drift，并令确定性扩散函数满足 $\sigma_Q(t)=\sigma_P(t)$。这不是“physical volatility
按定义等于 implied volatility”，也不是可推广到随机波动率、局部波动率或 jump model 的
通用结论。

## 快速开始

要求 Python `3.12.x`、`make`，并在类 Unix 环境中运行。依赖锁定为 QuantLib `1.39`、
DuckDB `1.5.5` 和 pytest `8.4.1`。

```bash
make install
make snapshot-summary
make test
```

`snapshot-summary` 只读 checked-in 的 frozen snapshot。若要生成或追加数据，默认目标位于
`/tmp`，不会改写仓库内的 public DuckDB：

```bash
make smoke
make append-day
```

也可以显式指定临时数据库：

```bash
DATABASE=/tmp/my-synthetic-market.duckdb make smoke
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/my-synthetic-market.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  summary
```

常用的只读 SQL 位于
[`snapshots/public/sql_query/`](snapshots/public/sql_query/README.md)。完整 CLI、schema、
增量规则与冻结语义见 [Authoring Pipeline](docs/authoring_pipeline.md)；authoring 包的模块边界
见 [`src/synthetic_derivatives/authoring/README.md`](src/synthetic_derivatives/authoring/README.md)。
独立 public child 的表、checksum 与泄漏边界见
[`src/synthetic_derivatives/export/README.md`](src/synthetic_derivatives/export/README.md)。

可在仓库外重放完整 golden-package 构建：

```bash
.venv/bin/python scripts/package_bsm_greeks_task.py \
  --base-parent-config configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json \
  --output-root /tmp/bsm-greeks-packages
```

## Analytic BSM、IV 与 Greeks 内核

直接波动率变体 `bsm_analytic_greeks_v1` 在 USD money-market numeraire 对应的
$\mathbb Q$ 下定价 ex-dividend spot。已知 valuation-date spot 后，常参数过程为

$$
\frac{dS_t}{S_t}=(r-q)dt+\sigma dW_t^{\mathbb Q},
$$

其中 $r/q$ 是 flat continuous 年化 rate/yield，$\sigma>0$ 是
$\mathbb Q$-measure 年化瞬时波动率，期限严格使用 Actual/365 Fixed。European call/put
采用精确解析 BSM law；这不是路径离散化，也不把 physical volatility 当成 implied
volatility。

Solver 在 [`solver/bsm.py`](src/synthetic_derivatives/solver/bsm.py) 中只用 Python stdlib
计算 unit-option price、spot Delta/Gamma、每 1 vol point Vega、每 calendar day Theta 和每
1 percentage point Rho。Trusted verifier 在
[`verifier/bsm_greeks.py`](src/synthetic_derivatives/verifier/bsm_greeks.py) 中使用固定的
QuantLib `1.39` `AnalyticEuropeanEngine` 独立复算。两端只共享
[`tasks/bsm_greeks.py`](src/synthetic_derivatives/tasks/bsm_greeks.py) 中的输入、单位与
canonicalization contract，不共享定价或 Greek 实现；所有数值结果经
`Decimal(str(binary64))`、8 位 `ROUND_HALF_EVEN` 后逐字段 exact compare。

直接 Greeks 内核接受给定的 $\sigma_Q$。独立的
[`bsm_iv_scalar_v1`](configs/variants/bsm_iv_scalar_v1.json) 则只从 solver-visible bid/ask
构造可见逆问题：先按
`(Decimal(str(bid)) + Decimal(str(ask))) / 2` 得到精确 midpoint，再 cast 一次为 binary64。
它先检查贴现 European BSM price domain，再检查固定 volatility bracket `[1e-6, 5.0]`，
成功时无条件执行 80 次 midpoint update，不使用 tolerance、early stop、Newton/Brent 或
fallback。结果明确区分 `OK`、`INVALID_INPUT`、`OUT_OF_BOUNDS` 和 `NO_BRACKET`。

Solver 的 [IV 实现](src/synthetic_derivatives/solver/bsm_implied_volatility.py) 使用 stdlib BSM
price；[trusted IV verifier](src/synthetic_derivatives/verifier/bsm_implied_volatility.py) 在相同
冻结迭代 schedule 下调用 QuantLib price，并在
[IV output schema](schemas/bsm-implied-volatility-output.schema.json) 后逐字段 exact compare。
Canonical IV 是从实际可见 midpoint 恢复的值，不是 generator 的 hidden volatility；任何
standardized-normal 中间量仍不进入 schema、配置或输出。

## Market-implied Greeks golden agent task

已接受的 D4 task 位于
[`task_packages/bsm_market_implied_greeks_v1`](task_packages/bsm_market_implied_greeks_v1)。
它从同一 frozen 22-asset P/Q parent 确定性选择 8 个 underlyings，并对每个 underlying
保留两个最近 live expiries、每个 expiry 按 $|\log(K/F)|$ 最近 ATM 的五个 strikes 及完整
call/put pairs，共 160 rows。Agent-visible DuckDB 只含：

- `metadata.public_task`；
- `solver_visible.greeks_task_inputs`；
- `solver_visible.greeks_task_contract`。

Agent 不获得 raw DuckDB handle；trusted query adapter 各读取 contract/inputs 一次，并只允许
一次 submission。Effective runtime 由 global allowlist 与 task overlay 取交集，禁用网络、
动态安装、子进程、QuantLib/现成 IV/Greek packages 和私有路径。Reference solver 在同一
权限与工具预算下两次 replay 得到 byte-identical submission，再由独立 QuantLib verifier
从公开 bid/ask 重做 80-step inversion 和五个 unit Greeks，最终逐字符串 exact compare。
该输入仍标为 `D4`：任务载体是 DuckDB market snapshot，并要求读取冻结的 contract/input
relations、保持跨关系 identity 与 canonical row workflow；`D1` 只表示直接给一张结构化表。
是否把 raw SQL connection 暴露给 Agent 是 runtime security 选择，不会把 market-snapshot
data/tool axis 降成 D1。
Package 保持 `ACCEPTED`（单条 golden task）。Selector seed 已可由 Phase F batch runner
参数化，并可导出九字段 JSONL；完整 100-task run 与 `RELEASED` promotion 尚未执行。Authoring 私有
artifact manifest 覆盖全部 public/verifier/reference/private 源制品；package verifier 还会
逐文件确认 authoring、train/dev、evaluation 三个 view 与各自源文件字节一致。

方法合同要求恰好 80 步，但 79/80/81 步根之间约 $10^{-24}$ 的差异在 8 位输出下通常不可
观察；因此 source/runtime policy 拒绝显式 schedule 改写，semantic verifier 只声称验证
实际可观察的 canonical output，不虚构仅凭 8 位结果即可证明迭代次数。

## 阅读导航

- 想运行项目：从上面的“快速开始”和 [public tests](tests/public/README.md) 开始。
- 想修改 generator：先读 [authoring package](src/synthetic_derivatives/authoring/README.md) 和
  [unit-test invariants](tests/unit/README.md)。
- 想理解完整数学与训练框架：读
  [框架设计文档](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)。
- 想查看可执行查询：读 [public SQL 说明](snapshots/public/sql_query/README.md)。
- 想检查 golden agent package：读 [task package 说明](task_packages/README.md) 和
  [packaging tests](tests/packaging/README.md)。

## 设计流程

```text
已实现：Authoring
  -> 生成并冻结 market snapshot（correlated profile 同时冻结 underlying dependence spec）
  -> 确定性导出独立 public-only child DuckDB
  -> 标准库 analytic BSM price / Greeks
  -> 标准库固定 80 步 visible-price IV inversion
  -> Pinned QuantLib trusted verifier canonical exact compare
  -> 注册七维 task variant 与 compatibility decision（当前 BSM task 固定 F0）
  -> 物化三关系 D4 golden task package 与 authoring/train-dev/evaluation views
  -> 参数化 private selector seed 批量构建 accepted packages
  -> 将已验证 package 导出为九字段 JSONL dataset
  -> Mutation Engine 生成带 lineage 的 candidate pool
  -> Curriculum Scheduler 按 mastery 选择训练分布

规划中：
  -> 执行并审核完整 100-task batch 与 split provenance
  -> 将获批 artifacts 显式 promote 为 RELEASED
  -> 泛化跨 task-family ORM outcome/dataset export
```

Authoring、Solver 和 Trusted verifier 是三个不同的权限边界：

- Authoring 可以使用固定版本的 QuantLib 和固定 seed 生成市场数据。
- Solver 只能读取公开快照与合同，不得调用现成的定价、IV、Greeks 或 smile API。
- Trusted verifier 可以使用固定版本的金融与数值包独立复算，但不能向 Solver 暴露 oracle 或 hidden tests。

七维坐标 $\tau=(L,P,M,A,D,R,F)$ 分别表示 reasoning、product、model、numerical method、
data/tool、risk output 和 arbitrage-finding 难度。坐标必须先通过 compatibility registry；
不能把七个轴无条件做 Cartesian product。当前 BSM/Greeks task family 显式固定为 `F0`，
runtime 尚未实现 F2A 等套利业务；旧六维 task 只能通过唯一的显式 migration adapter 补入
`F0`。各 level 的完整含义见
[七维 Task Grammar](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md#七维-task-grammar-与难度空间)。

## Public metals snapshot

仓库内置 [22-metal generator 配置](configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json)
和 [DuckDB public snapshot](snapshots/public/quantlib_bsm_smoke_v1.duckdb)。文件名保留旧的
`quantlib_bsm_smoke_v1` 路径以兼容现有入口，但逻辑 snapshot 已更新为
`DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3`：

| 对象 | 行数 |
|:---|---:|
| Underlying master | 22 |
| Option contract master | 1,232（22 × 56） |
| `underlying_daily` | 1,430（22 × 65 日） |
| `option_daily` | 60,368（只在 expiry 前报价） |
| `pricing_metadata` | 1,430（22 × 65 日） |
| `option_pricing_audit` | 60,368（private） |

Option candidate grid 为 6 个期限 × 11 个 listing-moneyness × call/put。Inclusive
liquidity filter 只保留 30/60/90/180 天和 0.85–1.15 moneyness，因此每个 underlying
挂牌 56 个 static contracts。每个 valuation/expiry 区间先对 deterministic
$\sigma_Q^2(t)$ 精确积分并取 RMS，再用 QuantLib analytic BSM engine 计算 mid；
`mid = settlement_price`，deterministic clipped-Gaussian quote noise 只作用于 bid/ask
half-spread。Private audit 从这个已量化 canonical mid 反解 IV，不把定价输入 volatility
直接冒充 IV。

以上计数和 private IV audit 只描述不可变的 config `1.5.0` checked-in demo。当前 writable
config `1.6.0+` 不再生成 authoring IV answers；D4 golden task 使用另一个冻结的 config
`1.7.0` P/Q parent，并从 public bid/ask 重新定义 inverse problem，不修改这个历史 snapshot。

查看已冻结 public snapshot：

```bash
make install
make snapshot-summary
```

若要从配置重放，请生成到新文件；不要修改已冻结的 checked-in snapshot：

```bash
.venv/bin/python scripts/edit_snapshot.py \
  --database /tmp/metals-liquid-tdgbm-q-v2.duckdb \
  --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json \
  create-smoke
```

完整 schema、主键、增量规则和命令见 [Authoring Pipeline](docs/authoring_pipeline.md)。
原 [5-underlying legacy smoke config](configs/generators/quantlib_bsm_smoke_v1.json) 仍用于
快速 additive/compatibility tests，不再对应 checked-in public DuckDB。当前 metals profile
是合成的市场规模近似，不宣称复刻某个真实交易所的 calendar、carry、expiry 或
settlement 规范。

## Deterministic physical drift / volatility

Generator config schema `1.1.0+` 支持把 underlying 的 P-measure
`physical_drift` 和 `physical_volatility` 配置成时间的确定性分段线性函数。原来的
scalar 写法继续支持，并等价于 constant function。当前 checked-in metals config
`1.5.0` 由 [sampling script](scripts/sample_physical_dynamics.py) 为每只 underlying 独立
抽取 sampling seed、node-offset grid、drift phi/std、log-vol phi/std 和最终节点值。所有
分布都有硬边界；22 组 offsets 和每一类 hyperparameter realized value 均强制互不相同。
脚本把 global/per-underlying seeds、bounds、realized parameters 和 nodes 全部冻结到 config；
重放 snapshot 时不会重新抽样。核心配置片段如下；完整 provenance 以 checked-in config
为准。

```json
{
  "schema_version": "1.5.0",
  "start_date": "2026-08-03",
  "underlyings": [
    {
      "underlying_id": "SYNTH-EXAMPLE-01",
      "initial_spot": 100.0,
      "physical_drift": {
        "type": "piecewise_linear",
        "nodes": [
          {"day_offset": 0, "value": 0.07},
          {"day_offset": 2, "value": 0.04},
          {"day_offset": 4, "value": 0.08}
        ],
        "extrapolation": "flat"
      },
      "physical_volatility": {
        "type": "piecewise_linear",
        "nodes": [
          {"day_offset": 0, "value": 0.22},
          {"day_offset": 2, "value": 0.30},
          {"day_offset": 4, "value": 0.18}
        ],
        "extrapolation": "flat"
      },
      "risk_free_rate": 0.03,
      "dividend_yield": 0.01
    }
  ],
  "q_pricing": {
    "risk_neutral_measure_id": "USD-MONEY-MARKET-Q-v1",
    "numeraire_id": "USD-MONEY-MARKET-ACCOUNT-v1",
    "rate_path_id": "USD-FLAT-CONTINUOUS-RATE-v1",
    "measure_change": "girsanov_drift_only",
    "volatility_mapping": "same_deterministic_diffusion",
    "implied_volatility_solver": {
      "method": "QuantLib.VanillaOption.impliedVolatility",
      "target_quote": "canonical_mid",
      "accuracy": 1e-12,
      "max_evaluations": 1000,
      "minimum_volatility": 1e-6,
      "maximum_volatility": 4.0
    }
  }
}
```

`day_offset` 是从 `start_date` 开始计算的日历日，不是 business-day index。节点间
使用线性插值；第一个节点必须为 0，节点必须按严格递增的整数 offset 排列，节点外
目前只支持 flat extrapolation。Physical volatility 的所有节点必须大于 0。例如
上面的 drift 在 day 0、1、2 分别为 `0.070`、`0.055`、`0.040`。

路径生成不是简单地取 close date 终点处的函数值，而是对每个
`previous close -> current close` 区间计算精确等效参数：

$$
\mu_{\mathrm{eff}}
=\frac{1}{\Delta t}\int_{t_0}^{t_1}\mu(t)\,dt,
\qquad
\sigma_{\mathrm{eff}}
=\sqrt{\frac{1}{\Delta t}\int_{t_0}^{t_1}\sigma^2(t)\,dt}.
$$

线性段端点为 $a,b$ 时，drift 的区间平均是 $(a+b)/2$，volatility 的区间
RMS 是 $\sqrt{(a^2+ab+b^2)/3}$。跨节点或跨周末的区间会在节点处拆分并按完整
日历区间积分。因此 Aug 3 到 Aug 4 的 drift 若从 `0.070` 线性走到 `0.055`，
传给 QuantLib 的 $\mu_{\mathrm{eff}}$ 为 `0.0625`；volatility 若从 `0.22`
走到 `0.26`，则 $\sigma_{\mathrm{eff}}$ 约为 `0.240277617`。

当前实现由项目代码完成 piecewise-linear 插值与精确积分，再把每个区间的等效参数
分别放入 QuantLib `FlatForward` 和 `BlackConstantVol`，最后调用
`BlackScholesMertonProcess.evolve`。这与 deterministic time-inhomogeneous GBM
在观测网格上的 exact transition 对齐：

$$
\log\frac{S_{t_1}}{S_{t_0}}
=\int_{t_0}^{t_1}\left(\mu(t)-\frac12\sigma^2(t)\right)dt
+\sqrt{\int_{t_0}^{t_1}\sigma^2(t)dt}\,Z.
$$

对应实现位置：

- [config.py](src/synthetic_derivatives/authoring/config.py)：配置解析、节点验证、线性插值、drift 积分与 volatility RMS；
- [generator_common.py](src/synthetic_derivatives/authoring/generator_common.py)：共享 pinned QuantLib、calendar/day-count、日期转换、价格量化与 namespaced RNG；
- [underlying_daily_generator.py](src/synthetic_derivatives/authoring/underlying_daily_generator.py)：按真实日期区间计算等效参数、生成 P-measure underlying path/dependence 和 pricing metadata；
- [option_daily_generator.py](src/synthetic_derivatives/authoring/option_daily_generator.py)：生成 frozen option contracts，并从 realized spot 生成 daily quotes；不提供 underlying path/dependence API；
- [pipeline.py](src/synthetic_derivatives/authoring/pipeline.py)：增量生成时保留每条路径的真实 previous date，保证一次生成和 append 的结果一致；
- [公开测试](tests/public/test_authoring_template.py)与[单元测试](tests/unit/test_authoring_config.py)：覆盖每日参数变化、精确积分、append invariance、配置校验与函数定义不可变性。

`market.underlyings.physical_drift` 和 `physical_volatility` 为兼容现有 schema，保存
函数在 `day_offset = 0` 的值。完整函数、当日实际使用的有效参数、区间起止日期和
reduction method 写入 `market.pricing_metadata.physical_dynamics`。`start_date` 行是
$S(t_0)=S_0$ 的 initial condition，不伪造前一日 transition；后续 business-date 行按真实
calendar interval 演化。

本节所述 frozen config `1.5.0` 另声明一个明确、局部的模型假设：在这个 deterministic-diffusion
GBM baseline 中，Girsanov change of measure 只把 drift 从 $\mu_P(t)$ 改为 $r-q$，扩散函数
保持 $\sigma_Q(t)=\sigma_P(t)$。这不是“physical volatility 按定义等于 IV”，也不是通用
的 volatility-risk-premium 结论。European option 在 $[t,T]$ 使用

$$
\sigma_{Q,\mathrm{eff}}(t,T)
=\sqrt{\frac{1}{T-t}\int_t^T\sigma_Q^2(u)\,du},
$$

由 QuantLib 计算未舍入理论价；mid 量化到 8 位后，再由该历史配置声明的 QuantLib solver 对这个
可见 canonical mid 反解 IV。完整 Q mapping、输入 volatility、未舍入价格、反解结果和
失败状态只保存在 private `market.option_pricing_audit`，没有 solver-visible view。

可以直接检查每天实际使用的 interval-equivalent 参数：

```sql
SELECT
    valuation_date,
    underlying_id,
    CAST(json_extract_string(physical_dynamics, '$.drift') AS DOUBLE)
        AS effective_drift,
    CAST(json_extract_string(physical_dynamics, '$.volatility') AS DOUBLE)
        AS effective_volatility,
    json_extract_string(physical_dynamics, '$.interval_start') AS interval_start,
    json_extract_string(physical_dynamics, '$.interval_end') AS interval_end
FROM market.pricing_metadata
ORDER BY underlying_id, valuation_date;
```

## Correlated underlying simulator

Generator config `1.2.0` 在上述单资产 physical dynamics 之上增加
`underlying_simulation`。其 `driver_order` 只能包含 underlying ids，相关结构不接受
option ids：

```json
{
  "schema_version": "1.2.0",
  "underlying_simulation": {
    "dependence_spec_id": "SYNTH-P-SPOT-FACTOR-v1",
    "measure": "P",
    "driver_order": ["SYNTH-U01", "SYNTH-U02"],
    "formulation": "factor_loading",
    "factor_loading_matrix": [[0.8], [0.5]],
    "idiosyncratic_diagonal": "derive_from_row_norms",
    "matrix_dtype": "float64",
    "factorization_method": "factor_loading_direct",
    "factorization_order": "declared_driver_order",
    "time_grid": "business_daily",
    "regime_id": "constant"
  }
}
```

若 $\lambda_i^\top$ 是 $\Lambda$ 的第 $i$ 行，则 generator 确定性派生

$$
D=\operatorname{diag}\!\left(1-\lVert\lambda_1\rVert^2,\ldots,
1-\lVert\lambda_n\rVert^2\right),
\qquad
R=\Lambda\Lambda^\top+D,
$$

并按

$$
Z_t=\Lambda\eta_t+D^{1/2}\varepsilon_t
$$

替换每个 underlying GBM close transition 的标准正态冲击。Common factor 和
idiosyncratic streams 分别按 `dependence_spec_id/factor/date` 与
`dependence_spec_id/underlying_id/date` 派生，因此 one-shot 与 append 完全一致。

完整合同持久化在 authoring 表 `market.underlying_dependence`。Legacy P-only snapshot
不会在 solver view 中暴露 dependence row；config `1.7.0` 的完整 P/Q pair 会通过
`solver_visible.underlying_dependence` 投影合同字段，但不公开 seed、run 或 calibration
provenance。`option_daily_row()` 仍只读取固定 spot 和原有 pricing inputs：在相同 spot 下
切换合法 $R$，option surface 与 analytic Greeks 必须完全不变。可运行 P-only 配置见
[correlated-underlying 模板](authoring/templates/quantlib_bsm_correlated_underlyings.template.json)，
完整增量规则见 [Authoring Pipeline](docs/authoring_pipeline.md)。

## OptionChainBuilder

Generator config `1.3.0` 用一个显式 `option_chain` 合同替代逐条
`option_templates`。当前第一版只支持 `listing_rule=snapshot_start` 与
`roll_rule=static`，避免在尚未定义 exchange schedule 时隐式滚动：

```json
{
  "schema_version": "1.3.0",
  "option_chain": {
    "chain_id": "STATIC-GRID-v1",
    "expiry_days": [30, 90, 180],
    "moneyness_grid": [0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2],
    "call_put": ["call", "put"],
    "listing_rule": "snapshot_start",
    "roll_rule": "static",
    "strike_increment": 0.5,
    "strike_rounding": "ROUND_HALF_EVEN",
    "exercise_style": "european",
    "settlement_type": "cash",
    "contract_multiplier": 100
  }
}
```

`OptionChainBuilder` 要求 `moneyness_grid` 与 `strike_grid` 恰好选择一个，再按 expiry、
grid value、call/put 的规范顺序展开完整 Cartesian grid。每个 moneyness 只在 listing
date 使用一次：以 listing spot 乘 moneyness，再按
`strike_increment` 与 `ROUND_HALF_EVEN` 得到绝对 strike。`market.option_contracts`
保存 listing date/spot/moneyness 与 frozen strike；后续 spot 变化、append 或
`sync-config` 都不会改写合约。若直接使用 `strike_grid`，输入 strike 只按相同 increment
规范化并冻结，不依赖 listing spot。`market.option_chain_specs` 私有保存 grid type/value、listing/roll
与 rounding 合同，不创建 solver-visible view。

同一 expiry/strike 必须同时存在 call 和 put，expiry 与所选 grid 必须正值且严格
递增；若 strike increment 使不同 grid inputs 舍入到同一 strike，配置会被拒绝。Chain
规则和已挂牌合约在同一 snapshot 内不可修改。schema `1.0`--`1.2` 的 legacy
`option_templates` 仍保持兼容。

Config `1.4.0` 把 expiry/moneyness arrays 作为 candidate grid，再按 inclusive
listing-moneyness band 与 maximum expiry 筛出实际挂牌合约。完整 liquidity rule 与 quote
model 保存在 private `market.option_chain_specs`；筛选不会随 daily spot 变化。其
side-specific deterministic Gaussian noise 只乘在 baseline bid/ask half-spread 上，
QuantLib BSM NPV 仍保存为 `mid = settlement_price`。

本阶段没有引入逐 option correlation。Config `1.7.0` 已增加共同
$\mathbb Q$/numeraire/rate path 下独立的 Q-measure underlying-driver dependence identity；
basket/spread 等联合 payoff 定价仍属于后续阶段。

## Market snapshot、pricing model 与 pricing engine 的边界

下一阶段按“一个 snapshot 是一个已经实现的市场世界”设计。这里的市场事实是合约、
underlying 状态和报价，而不是生成这些报价时使用的模型。当前 schema 已通过
`(snapshot_id, date, option_id)` 主键保证同一个 snapshot 中同一合约同一天只有一条
`bid / ask / mid`；后续不能为了比较模型而在 `option_daily` 中增加 model 维度。

```text
private authoring DGP
  one generation model + one canonical generation engine + latent state/seed
                               |
                               v
public frozen market snapshot
  one contract/time/venue -> one bid/ask/mid
                               |
                 +-------------+-------------+
                 v                           v
task convention + IV solver          valuation/calibration runs
  one derived BSM IV                 BSM / Heston / Bates / local vol ...
                                     theoretical price + residual + diagnostics
```

具体边界如下：

- 对固定的 spot、strike、expiry、discount/dividend curves 和 BSM convention，可行价格
  区间内部的一条 European option mid 对应唯一的 BSM implied volatility；bid/ask 则
  对应一个 IV interval。违反价格边界时应标记为“无有效 IV”，而不是产生第二个 IV。
- 同一报价可以被 BSM、Heston、Bates 或 local-vol 等多个 valuation model 解释；各模型
  的 theoretical price、calibrated parameters、residual 和 diagnostics 属于 task/run
  output，不是第二份市场报价，也不能覆盖 snapshot 中的 mid。
- 每个 snapshot 只选一个 private canonical generation model 和一个 canonical generation
  engine 产生未舍入理论价格。有限差分或 Monte Carlo 等其他 engine 用于 cross-engine
  verification、容差检查和数值诊断，不产生并列的 market truth。
- 若要比较不同 data-generating models，应由相同私有 `market_scenario_id` 派生不同的
  `snapshot_id`，形成 counterfactual market worlds；不能在同一个 snapshot 中按 model
  重复同一合约的价格。
- Market snapshot 默认只保存报价，不保存 IV truth table。若为了性能缓存 IV，它必须是
  带 `source_quote_id`、`iv_convention_id`、输入曲线版本、solver method 和 tolerance 的
  derived analytics，且可从报价重建。
- 只有显式建模多个 venue 时，报价业务键才增加 `venue_id`；consolidated snapshot 仍然
  只保留一组 NBBO-like bid/ask，而不是用 model id 伪装 venue。

元数据需要进一步拆成 public market context 与 private authoring provenance：

| 边界 | 应保存的内容 | 建议字段 |
|:---|:---|:---|
| Public snapshot | valuation timestamp、calendar/day count、currency、discount/dividend/borrow curves、settlement 与 quote precision | `iv_convention_id` 由 task contract 引用 |
| Private authoring | P/Q generation model、canonical engine、latent parameters/state、seed/RNG、未舍入理论价格 | `generation_model_id`、`generation_engine_id` |
| Task/run output | 用于解释同一市场报价的 model/engine、校准参数、理论价格、残差、收敛信息 | `valuation_model_id`、`valuation_engine_id` |

Authoring DB 内的 `solver_visible.pricing_metadata` 仍是 smoke v1 的过渡 view；它不是最终
发布安全边界。当前实现通过独立 child exporter 只复制 curves、currency、calendar/day-count、
共同 Q context 和任务选中的 market rows，且递归拒绝 seed/RNG、run lineage、latent node
heights 与 private answers。生产 Solver 只接收 child 文件，不接收 authoring DB。

### Underlying correlation 与 derivative no-arbitrage 边界

当前 $R^P/R^Q$ 都只排列 underlying/model drivers，不表示 option 之间的相关矩阵，也不
直接进入 vanilla option pricing engine。Config `1.7.0` 在共同 $\mathbb Q$、money-market
numeraire 与共享 flat-rate path 下，以显式 drift-only Girsanov mapping 保留同一 Brownian
covariance；每个资产的完整 option surface 仍由其 coherent BSM marginal model 生成。以
money-market account 为例：

$$
B_t=\exp\left(\int_0^t r_s\,ds\right),
\qquad
V_t^{(i)}
=B_t\,\mathbb E^{\mathbb Q}\!\left[
\frac{X_i}{B_{T_i}}\middle|\mathcal F_t
\right].
$$

本文保留 $B_t$ 专门表示 money-market numeraire；underlying factor-loading matrix
统一记为 $\Lambda_t$，不再复用字母 $B$。

若未来做 $\mathbb Q$-measure multi-asset Monte Carlo，生成器仍先模拟 underlying
drivers：给定共享利率路径后，各 underlying 的 idiosyncratic base noises 相互独立，再按
固定 `driver_order` 使用 $R_t$ 耦合。`driver_order` 不包含 option ids；同一 underlying
上的所有 strike、expiry 和 call/put 都读取同一 underlying state path。相关结构为：

$$
R_t=R_t^\top,
\qquad
\operatorname{diag}(R_t)=\mathbf 1,
\qquad
R_t\succeq0,
\qquad
D_t=\operatorname{diag}\!\left(
1-\lVert\lambda_{1,t}\rVert^2,\ldots,
1-\lVert\lambda_{n,t}\rVert^2
\right)\succeq0,
\qquad
R_t=\Lambda_t\Lambda_t^\top+D_t.
$$

若 $\Lambda_t$ 的第 $i$ 行为 $\lambda_{i,t}^{\top}$，则对相互独立的
$\eta_t\sim N(0,I_k)$ 与 $\varepsilon_t\sim N(0,I_n)$，联合冲击按

$$
Z_t=\Lambda_t\eta_t+D_t^{1/2}\varepsilon_t
$$

生成，且 $\operatorname{Cov}(Z_t)=R_t$。

因此，`conditional independence given the shared interest-rate path` 只描述相关扰动前的
underlying baseline。只要 $R_t$ 有非零非对角元，最终 underlying 在仅给定利率路径后
就是 conditionally correlated。对 Heston、stochastic-rate 或 hybrid models，
`driver_order` 和 $R_t$ 覆盖 underlying 的 spot/volatility/rate driver blocks，并保持
各边际模型原有的内部相关结构。

这一阶段的实现合同如下：

- 每个同币种 snapshot 只声明一个 `risk_neutral_measure_id`、一个 `numeraire_id` 和一个
  `rate_path_id`；不能逐 option 混用 measure、discounting convention 或 rate path。
- 每个资产的全部 strike、maturity 与合约变体必须来自同一个 coherent marginal model
  snapshot；相关矩阵只耦合随机驱动，不改变边际参数或完整 option surface。
- Underlying dependence 合同至少冻结 `dependence_spec_id`、measure、underlying
  `driver_order`、$\Lambda_t$、$D_t$、$R_t$、matrix dtype、time grid 与 regime id；
  derivative contracts 不在矩阵中占行或列。
- 合法 $R_t$ 默认由固定的 $\Lambda_t\Lambda_t^\top+D_t$ 构造，再按相同 dtype、顺序与
  canonicalization 重放；symmetry、单位对角与 PSD 都由构造保证，不依赖带 tolerance 的
  事后 eigenvalue 判定。
- 每个 scenario 先生成一次共享利率路径，再从稳定 underlying driver id 派生 base streams，
  最后按冻结的 $\Lambda_t$ 与 $D_t$ 耦合；固定 config/seed 时 one-shot、append 和 replay
  必须一致。
- 所有 basket、index、spread 或其他多资产 payoff 必须读取同一个 joint underlying
  process 与 dependence snapshot，不能独立生成 derivative prices 后再做相关拼接。
- No-arbitrage 来自共同 $\mathbb Q$/numeraire、coherent marginal models 与一致联合定价，
  而不是来自 conditional independence 或相关矩阵。Put--call parity、strike
  monotonicity/convexity 和 calendar consistency 作为实现层 sanity checks 保留。

当前 scope 只包含同一币种与同一市场计价体系，不包含 FX、quanto、cross-currency
derivatives、多币种利率或 numeraire conversion。该方案保证模型内部一致性与可复现性，
但不声称风险中性测度唯一。

### Authoring realism roadmap

建议按下面顺序增强，而不是先堆叠多个 pricing engines：

1. **Option chain baseline（已完成）。** config `1.3.0/1.4.0` 已支持同一 expiry 下多个
   puts/calls 和 strikes、多个 expiries 的完整 $K\times T$ 网格；listing moneyness 已冻结
   为绝对 strike，合约跨 valuation dates 保持身份不变；候选网格按期限和 listing
   moneyness 做 immutable liquidity filtering。Weekly/monthly/quarterly 动态 listing/roll
   仍待 exchange profile 阶段实现。
2. **同币种 common-$\mathbb Q$ pricing context（已完成 vanilla baseline）。** Config
   `1.7.0` 已冻结共同 $\mathbb Q$/money-market numeraire/rate-path identity、独立 P/Q
   dependence IDs，以及 drift-only same-Brownian-covariance mapping。Derivative contracts
   不进入 correlation matrix；basket/index/spread 等联合 payoff 定价仍未实现。
3. **公共市场状态与私有 DGP 隔离（task 发布边界已完成）。** Authoring DB 内部的
   `pricing_metadata` 仍是过渡 view，但 generic exporter 与 task-specific materializer 已将
   production task 投影为独立 public-only child。扩充 Heston/Bates 等 richer DGP 前仍应继续
   收紧 authoring 内部 metadata 分层。
4. **曲线、日历与合约约定。** 用非 flat discount/dividend/borrow term structures、离散
   dividend/corporate actions、真实交易所 holidays/early closes、strike increments、
   multiplier、exercise/settlement style 和 valuation timezone 替换 smoke 简化项。
5. **报价微观结构与流动性状态。** Spread 应随 premium、moneyness、maturity、vega 和
   liquidity 改变；加入合法 tick、bid/ask size、zero bid、stale/missing quote 与 quality
   flags。Volume/open interest 应有跨日持续性，不能每天独立均匀抽样。
6. **扩展且跨日一致的 $\mathbb P/\mathbb Q$ latent state。** 在基础相关耦合上加入共同
   variance/regime/jump state、time-varying $R_t$ 以及 spot--volatility/rate driver blocks，
   并用明确的 risk premia 区分 $\mathbb P$-measure path dynamics 与 $\mathbb Q$-measure
   pricing dynamics；扩展联合分布时仍保持共同 measure、numeraire 和统一定价算子。
7. **多个 DGP family，但每个 snapshot 仍只有一个。** 保留当前 deterministic-
   time-varying-diffusion BSM 作为可解释 baseline，再分别生成 Heston、Bates/jump-diffusion、local-vol 等独立
   snapshot；每个模型先选 canonical analytic/FD engine，再用另一 engine 做 verifier。
8. **更长的历史与 regime coverage。** 从 5-day smoke 扩展到覆盖 calm/high-vol、earnings、
   jump 和 liquidity stress 的多时间段、多 seed scenario；最后再升级 underlying OHLC、
   overnight gap、volume 和 corporate-action 细节。

当前 authoring 只生成最终可见报价及其必要 provenance，不生成 derived IV answer；IV
属于 solver-visible quote 上的 task/verifier inverse problem。公开 snapshot 只暴露市场
可观察量和任务明确允许的 conventions。

### 下一步实现计划

后续实现按依赖顺序推进；每一阶段都必须保持固定 config/seed 可重放、one-shot 与 append
结果一致、frozen snapshot 不可修改以及 revision 稳定。

| 阶段 | 模块 | 首次实现范围 | 完成标准 |
|:---|:---|:---|:---|
| 0（已完成） | `UnderlyingSimulator` / `UnderlyingDependenceSpec` | config `1.2.0` 以 $\Lambda/D/R$ 耦合 P-measure underlying close shocks；private authoring 表冻结合同 | one-shot/append 一致；旧 config 无回归；固定 spot 时 option quote 不受 $R$ 影响；option ids 不进入矩阵 |
| 1（已完成） | `OptionChainBuilder` | config `1.3.0/1.4.0` 声明 candidate expiry/moneyness grid、成对 call/put、static listing/roll、liquidity filter 与 strike increment；listing date 冻结绝对 strike；quote noise 只扰动 BSM half-spread | 合约 id 跨日稳定；one-shot、append 与 `sync-config` 一致；22 品种、65 日 public profile 通过；far-expiry/far-strike candidates 不挂牌 |
| 2（已完成） | `CommonQPricingContext` / `QUnderlyingDependenceSpec` | config `1.7.0` 声明唯一的 $\mathbb Q$/numeraire/rate path、独立 P/Q IDs，以及 drift-only same-Brownian-covariance mapping | P/Q specs 同时由 $\Lambda/D/R$ 构造；option ids 不进入 $R_t$；合法相关性不改变 vanilla margins；后续联合 payoff 必须来自同一 joint process |
| 3（已完成） | Independent public-child exporter | Frozen authoring parent 确定性投影 task rows；public 层只留 spot/contract/quotes/curves/P-Q dependence，private 层保留 generation provenance | child 无 `market` schema、seed/RNG/run/oracle/node heights；stable IDs、subset manifest、logical checksum 与 read-only replay 通过 |
| 3A（已完成） | D4 BSM Greeks golden package | 三关系 8-underlying/160-row DuckDB、trusted adapters、fixed-80 IV + five Greeks、QuantLib verifier 与三种 release views | reference replay byte-identical；canonical exact compare、runtime attacks、negative submissions、leakage 和 view allowlists 全部通过 |
| 3B（工具已完成，运行待审核） | Phase F batch / BSM training export | 共享一次 frozen parent materialization，按 distinct selector seeds 构建 accepted packages，并导出按 task ID 排序的九字段 JSONL | batch runner 与单包 dataset contract test 已完成；完整 100-task run、split audit 与 `RELEASED` promotion 尚未执行 |
| 4 | `ExchangeProfile` 与 `QuoteModel` | 加入真实 holiday/early-close、timezone、expiry/settlement、strike/tick rules，以及随 moneyness、maturity、vega、premium、liquidity 变化的 spread；补充 size、stale/missing/zero-bid 与 quality flags | 所有公开报价符合声明的 exchange profile；volume/open interest 和 liquidity state 具有跨日持续性 |
| 5 | `CurveState` 与 richer driver blocks | 在 common-$\mathbb Q$ 与 measure-qualified underlying dependence 合同下支持非 flat curves、离散 dividend/corporate actions、共享 variance/regime/jump state、time-varying $R_t$ 及 spot--volatility/rate blocks | 同一 valuation timestamp 使用同一个 market-state/dependence version；边际内部相关结构不被 cross-asset coupling 改写；$\mathbb P/\mathbb Q$ 差异由显式 risk premia 描述 |
| 6 | `GenerationModel` / `GenerationEngine` registry | 先保留 deterministic-time-varying-diffusion BSM baseline，再分别加入 Heston、Bates/jump-diffusion、local-vol snapshot DGP；每个 snapshot 只选一个 canonical engine，另一个 engine 做 verifier | 不同 DGP 使用不同 `snapshot_id`；同一 snapshot/合约/时间仍只有一条市场报价；cross-engine error 在预设 tolerance 内 |
| 7 | Scenario campaign | 扩展日期跨度、seed、calm/high-vol/earnings/jump/liquidity-stress regimes，并升级 OHLC、overnight gap、volume 与 corporate actions | manifest 记录 scenario lineage；dataset split 按 snapshot/scenario 分组，避免同一 latent world 跨 train/test 泄漏 |

最小可审阅的提交顺序建议为：

1. 已完成 `UnderlyingSimulator`、配置校验、private dependence persistence 与
   append-invariance/derivative-boundary tests。
2. 已完成 `OptionChainBuilder`、配置校验、private chain/liquidity/quote spec、冻结 listing
   strike 与 contract-id/append-invariance tests。当前 public profile 为 22 品种 × 每品种
   4 个液态 expiries × 7 个液态 strikes × call/put；BSM mid 不变，quote noise 只扰动
   bid/ask half-spread。
3. 已为当前 vanilla authoring contract 加入共同 $\mathbb Q$/numeraire/rate-path identity、
   独立 P/Q dependence specs 与显式 covariance mapping；需要多资产联合 payoff 时必须
   消费这一 joint underlying context，而不是构造 derivative correlation。
4. 已完成独立 public-child、D4 golden-package 发布边界，以及 Phase F batch/dataset tooling；
   完整 batch 执行与 release promotion 仍需审核。Authoring DB 内部更细的 metadata split、
   Heston/Bates 等新 DGP 需分别版本化后再接入。

因此 measure-qualified P/Q underlying dependence、static `OptionChainBuilder`、
common-$\mathbb Q$ vanilla baseline、独立 public child 和一条 accepted D4 golden package
均已完成。Batch 参数化与 BSM-specific dataset exporter 也已实现；后续工作是执行并审核
完整 batch、显式 release promotion、部署级 OS/container sandbox，以及
exchange/curve/richer-DGP roadmap；pricing model registry 仍应在这些边界稳定后推进。

常用的只读 DuckDB 查询集中保存在
[`snapshots/public/sql_query/`](snapshots/public/sql_query/README.md)，包括 snapshot 摘要、
underlying 时间序列、option chain、moneyness、pricing context 和 authoring audit。
每个 SQL 文件都在顶部提供可编辑的 `parameters` CTE，并显式固定结果排序。

## 仓库结构

```text
.
├── .venv/                         # 仓库本地 Python 虚拟环境，不提交 Git
├── authoring/
│   ├── configs/                   # Authoring job 的内部生成配置
│   └── templates/                 # 新 generator、snapshot 和 variant 的模板
├── configs/
│   ├── generators/                # 可发布的 generator 配置与版本声明
│   ├── task_space/                # 七维坐标范围和 compatibility registry
│   ├── mutations/                 # 受约束 mutation operators
│   ├── curricula/                 # stage、mastery bands 和采样 mixture
│   ├── variants/                  # Solver 可见的 task/method/output contracts
│   └── task_packages/             # Golden package selector/runtime 配置
├── datasets/
│   ├── generated/                 # 构建出的训练 JSONL/Parquet，不提交 Git
│   └── manifests/                 # 数据集版本、split 和来源清单
├── docs/
│   ├── examples/                  # 完整设计样例
│   └── *.md                       # 框架、架构和方法说明
├── environments/
│   ├── authoring/                 # Authoring dependency lock
│   ├── solver/                    # Solver dependency lock、capability profiles 与本地受限 harness contract
│   └── verifier/                  # Trusted verifier dependency lock 与说明
├── examples/
│   ├── submissions/               # 可公开的 canonical submission 样例
│   └── trajectories/              # 可公开的正向/负向 agent trajectory 样例
├── runs/                          # 本地 solver/verifier 运行结果，不提交 Git
├── schemas/                       # Snapshot、variant、trajectory、submission schemas
├── scripts/                       # venv、生成、校验和数据集构建入口脚本
├── snapshots/
│   └── public/                    # 小型、可公开且带 revision 的 DRAFT/FROZEN 快照
│       └── sql_query/             # 可复用、只读且显式排序的常用 DuckDB 查询
├── task_packages/                 # Accepted public/verifier/reference/private overlays 与 release views
├── src/
│   └── synthetic_derivatives/
│       ├── authoring/             # 市场快照生成与冻结实现
│       ├── export/                # Frozen parent → public-only solver DuckDB
│       ├── task_space/            # 七维 task grammar、显式 legacy migration 与 compatibility
│       ├── mutation/              # 确定性 child task 与 lineage
│       ├── curriculum/            # 不改 task/reward 的 adaptive sampling
│       ├── packaging/             # Agent package、prompt/runtime、三关系 DB 与 view materializer
│       ├── solver/                # 受限环境中的公式、求根、Greeks 与拟合实现
│       ├── training/              # Verified BSM package → 九字段 JSONL；通用跨 family exporter 待实现
│       └── verifier/              # 独立 package oracle 与 hard verifier 实现
└── tests/
    ├── fixtures/                  # 小型冻结测试输入和公开期望结构
    ├── unit/                      # task-space、mutation、curriculum 单元测试
    ├── integration/               # task manifest 与 authoring snapshot 边界
    ├── verifier_robustness/       # 预留目录；当前定向拒绝测试位于 integration/packaging
    ├── packaging/                 # Golden package E2E、runtime attacks、replay 与 release views
    └── public/                    # Snapshot、contract、重放和端到端公开测试
```

`.agents/` 与 `.codex/` 是本地协作工具使用的目录，不属于项目的数据生产接口。

## 各目录的职责

### `authoring/`

存放出题端的配置与模板，不放 Solver 可见的数据。Authoring pipeline 使用 pinned QuantLib、generator config、seed 和 RNG 生成 `underlying_daily`、`option_daily` 与 `pricing_metadata`，随后冻结并记录 revision。内部 audit 与 oracle 输出应保持私有。

新增 authoring job 时可从 [QuantLib/BSM generator 模板](authoring/templates/quantlib_bsm_generator.template.json) 复制配置；字段约束和 DRAFT/freeze 用法见 [模板说明](authoring/templates/README.md)。

### `configs/`

保存可复现行为所需的声明式配置：

- `generators/` 描述模型、参数、随机数生成器、draw order、定价 engine 和 generator version。
- `task_space/` 描述 $L/P/M/A/D/R/F$ 七个轴与合法 product--model--method 组合。
- `mutations/` 描述允许改变的轴、单次最大变更轴数和方向约束。
- `curricula/` 描述 stage、20/60/20 replay/current/explore mixture 与 mastery 调度区间。
- `variants/` 描述某一道任务的 snapshot、金融约定、method IDs、数值顺序、舍入规则、Solver 权限和输出格式。
- `task_packages/` 描述 golden task 的私有 selector、有效 runtime profile、submission/trajectory/oracle schema identities 和 release profile。

配置文件只描述合同，不存放实现代码或 hidden reference answer。

### `snapshots/`

保存生成后冻结的市场快照。`public/` 仅提交小型 demo；批量快照与私有快照不进入 Git。当前 public demo 使用 DuckDB，配套内容包括：

- `quantlib_bsm_smoke_v1.duckdb`：market、metadata 和 solver-visible views；
- `quantlib_bsm_smoke_v1.manifest.json`：snapshot/config 版本标识、revision 与行数；
- `sql_query/`：只读查询模板，不包含 canonical answer 或 hidden oracle。

IV、Greeks、smile、surface、VaR 和 ES 是从统一快照派生的任务结果，不在这里维护彼此独立的 truth tables。

### `schemas/`

定义跨边界的数据结构，包括 market snapshot、task variant、agent trajectory、Solver submission、verification report 和 dataset record。Schema 用于在进入下一阶段前拒绝缺字段、错单位、错顺序或非法数值。

### `src/synthetic_derivatives/`

项目的 Python 源码根目录。权限边界模块与训练编排模块分开：

- `authoring/`：允许使用 QuantLib，负责生成、质量门控、冻结与 revision。
- `export/`：只读 frozen authoring parent，写入独立 public child，并固定 stable IDs、
  subset manifest、logical checksum、schema allowlist 与 recursive leakage gate。
- `task_space/`：只判断七维坐标和 task family 是否兼容，并提供唯一的旧六维显式迁移入口；不生成任务、不决定采样。
- `mutation/`：从不可变母题生成确定性 child task 和 lineage，不读取模型表现。
- `curriculum/`：根据 stage 与 `pass@1` diagnostics 计算采样权重，不修改 frozen task 或二值 hard reward。
- `packaging/`：从 frozen P/Q parent 构建三关系 agent DB，组合 runtime allowlist、渲染 prompt、记录 observable trajectory，并导出隔离 views。
- `solver/`：只使用合同允许的基础原语，自行实现指定计算方法。
- `verifier/`：不得导入 Solver 的定价实现；使用独立 package oracle 复算并做 canonical exact equality。
- `training/`：验证 source package 后导出 BSM market-Greeks 九字段 JSONL，并记录 parent
  grouping、task ordering 与无 private-oracle/seed 声明；通用跨 family exporter 尚未实现。

生产部署时，四个子包不会共享同一个运行权限；代码分目录只是 repo 层面的组织方式。

### `environments/`

保存三个权限边界的 dependency locks 与 capability declarations。当前仓库提供 solver 的
受限 reference harness，但不提交完整 production container；部署时仍须在 OS/container 层
禁用网络、动态安装、未声明 filesystem 和 hidden verifier 访问。

### `datasets/`

`generated/` 存放可重建的训练集，因此被 `.gitignore` 排除；`manifests/` 存放应提交的版本信息、数据来源、snapshot grouping 和 train/validation/test split。训练集不得包含 hidden oracle、hidden tests 或 verifier 私有输出。

### `examples/`

保存能够公开审阅的小型产物：canonical submission、正向 trajectory、first-error 负轨迹等。示例用于解释合同，不能被生产 hidden verifier 当作唯一 oracle 来源。

### `tests/`

- `fixtures/` 提供稳定的小型输入。
- `public/` 检查公开 schema、snapshot identity、method contract 和端到端接口。
- `unit/` 检查 task grammar、受约束 mutation、lineage 与 curriculum sampling。
- `integration/` 检查 task/authoring 边界，以及 frozen parent 到 public-only child 的确定性重放、
  parent byte immutability、logical checksum、leakage 和 read-only handoff。
- `packaging/` 构建 temp P/Q parent 与完整 golden package，覆盖 runtime attacks、negative
  submissions、clean evaluation replay、leakage、hashes 和 release views。
- `verifier_robustness/` 当前是预留目录；末位数字、单位、method ID、行顺序和 import 的
  定向拒绝测试现位于 `integration/` 与 `packaging/`。

生产 hidden tests 应放在 Solver 无法读取的独立环境中，不提交到公开仓库。

### `scripts/` 与 `runs/`

`scripts/` 提供统一、非交互的项目入口；`runs/` 只保存本地临时运行结果、日志和报告。`runs/` 中的内容可以删除并重新生成，不作为数据集或 oracle 的可信来源。

## 虚拟环境约定

本项目只在仓库本地 `.venv` 中运行。命令统一使用：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest
```

`.venv/` 不提交 Git；可复现性由 Python 版本声明和锁定依赖文件保证。

## 设计文档

- [DuckDB + QuantLib Authoring Pipeline](docs/authoring_pipeline.md)
- [金融衍生品联合模拟、Task Mutation 与 Curriculum 最终设计](docs/financial_derivatives_deterministic_orm_framework_mutation_curriculum_simulator_final.md)
- [合成期权链 IV、Greeks 与 Smile Agent Trajectory 样例](docs/examples/synthetic_derivatives_iv_greeks_smile_deterministic_orm_agent_trajectory_example.md)
