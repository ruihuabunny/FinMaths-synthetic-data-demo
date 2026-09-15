# FinMaths Authoring：Brownian Bridge OHLC 与 Keyed Lognormal Volume 迁移方案

> 状态：已实施并验收（2026-08-19）；以下正文保留原审计与执行方案  
> 落地版本：generator config `1.8.0`、generator `0.9.0`、DuckDB schema `2.6.0`  
> 验收结果：full pytest `578 passed`；disposable snapshot 已完成 create → validate → freeze → read-only reopen  
> 制品边界：未修改既有 frozen snapshot、public snapshot、task package、delivery、accepted manifest 或 checksum  
> 审计日期：2026-08-19  
> 目标仓库：`ruihuabunny/FinMaths-synthetic-data-demo`  
> 目标分支：`synthetic-BSM-agent-task`  
> 审计提交：`6372c72b827a7458c4c72f159359a942b7ff71c1`  
> 参考仓库：`ruihuabunny/equity-quant-synth-agent-task@main`  
> 参考提交：`2524bdcee97da88c0288da05c78f440743aca452`

## 1. 结论

本次改造应作为一个新的 authoring observation-law 版本实施，而不是原地修改现有 `1.6.0/1.7.0` 配置或冻结 snapshot：

1. 保持现有日频 close 的 \(\mathbb P\)-measure time-inhomogeneous GBM、P/Q dependence mapping、QuantLib BSM option pricing、rounded-state restart law 不变。
2. 将 `separate_synthetic_range-v1` OHLC heuristic 替换为：给定已发布 `open` 和已量化 `close` 的、integrated-variance clock 上的 marginal log-price Brownian bridge；在 64 个均匀 calendar-fraction 子区间上取离散 high/low。
3. 将当前 `[750000, 1250000)` keyed uniform volume 替换为按 `(snapshot_id, seed, namespace, underlying_id, date)` 定位的 mean-preserving keyed lognormal：

   \[
   V^*_{i,d}=b_i\exp\!\left(s_i Z^V_{i,d}-\frac12s_i^2\right).
   \]

4. 新增显式、私有、不可变的 intraday bridge 与 volume model contracts；Agent/public child 只得到最终 OHLCV，不得到 seed、bridge spec、volume parameters 或 stream namespace。
5. 推荐版本：generator config `1.8.0`、generator `0.9.0`、DuckDB authoring schema `2.6.0`，并创建新的 config ID、generator version、snapshot ID、bridge spec ID 和 volume spec ID。
6. 旧配置继续保留旧行为用于历史读取/回放；现有 `task_packages/deliveries/`、public snapshots、task IDs、manifests 和 checksums 不原地更新。

## 2. 当前实现审计

### 2.1 已有正确基础

当前 FinMaths authoring 已经具备本次改造所需的大部分基础：

- `UnderlyingDailyGenerator` 在 \(\mathbb P\) 下生成 close；option generator 只消费已实现的 `spot_close`，不会读取 P dependence 或 physical drift。
- piecewise-linear \(\mu_P(t)\) 与 \(\sigma_P(t)\) 已按 Actual/365 Fixed 对区间漂移和方差做精确缩约。
- 多资产 close shock 使用

  \[
  D=\operatorname{diag}(1-\lVert\lambda_i\rVert^2),\qquad
  R=\Lambda\Lambda^\top+D,
  \]

  \[
  Z_{i,d}=\lambda_i^\top\eta_d+\sqrt{D_{ii}}\,\varepsilon_{i,d}.
  \]

- 随机流由 `snapshot_id + seed + semantic key` 经 SHA-256 派生，one-shot/append 不依赖循环顺序。
- close proposal 先按 underlying minimum price increment 做 `ROUND_HALF_EVEN`；已发布 close 是下一期 restart state。
- DuckDB 写入、MERGE、revision、manifest 和 freeze 已经事务化。

### 2.2 当前需要替换的逻辑

当前 `src/synthetic_derivatives/authoring/underlying_daily_generator.py` 的主要缺口为：

- `open = previous published close`；
- `high/low` 使用一个独立 absolute Gaussian range shock 和 `0.25` heuristic multiplier；
- `volume = 750_000 + int(U * 500_000)`；
- `pricing_metadata.physical_dynamics.ohlc_model` 仍标记为 `separate_synthetic_range-v1`；
- config 中没有 bridge grid、bridge namespace、base volume、volume log standard deviation 或 volume stream contract；
- schema 只有 OHLC 排序与正值约束，没有持久化 bridge/volume law；
- pipeline quality gates 没有 bridge/volume contract tamper check，也不回放 OHLCV；
- tests 覆盖 close dependence、append invariance 和 tick precision，但没有 bridge endpoint/covariance、weekend bridge、keyed lognormal 或 private observation-contract leakage 测试。

### 2.3 从 equity repo 采用与保留的部分

| 方面 | 采用 equity repo 的设计 | FinMaths 必须保留的设计 |
|:---|:---|:---|
| OHLC | integrated-variance log-price Brownian bridge；64 steps；离散 grid extrema；量化 endpoint | QuantLib close transition；underlying tick；published close restart state |
| Volume | `base_volume * exp(sZ - 0.5s²)`；按 instrument/date keyed；int64 boundary | FinMaths 的 `_derived_seed/_gaussian` 与 pinned QuantLib RNG |
| Dependence | close endpoint 继续相关；bridge conditional residual 为 per-underlying marginal | P/Q dependence pair、Girsanov drift-only mapping、common-Q context |
| Persistence | 私有 bridge/volume model rows；tamper check；replay gate | FinMaths 的 `market`/`solver_visible`、revision、manifest 和 frozen snapshot lifecycle |
| 不采用 | equity-only event/availability schema、custom SHA uniform Box–Muller、全量 equity simulator dependency | 不引入跨仓库 import/submodule；不改变 option generator 数值边界 |

参考实现只用于重表达数学与测试模式；FinMaths 不应依赖或导入 `equity_quant`。

## 3. 完整数学合同

### 3.1 测度、状态、时间与单位

对 underlying \(i\)：

- 历史 underlying path 与 OHLCV observation law 均在物理测度 \(\mathbb P\) 下。
- European option pricing 仍在声明的 \(\mathbb Q\) 与 money-market numeraire 下。
- \(S_i(t)>0\) 是 synthetic ex-dividend spot，单位为 `currency / underlying unit`。
- \(\mu_i(t)\) 单位为 year\(^{-1}\)，\(\sigma_i(t)\) 单位为 year\(^{-1/2}\)。
- 时间轴为 calendar-day offset，day count 为 Actual/365 Fixed。
- observation dates 使用 `WeekendsOnly`；相邻 observation 之间的完整 calendar interval 进入积分。
- `open`、`high`、`low`、`close` 和 `adjusted_close` 使用 underlying minimum price increment；内部数值使用 binary64。
- volume 单位为 underlying units per published daily observation，存储为非负 signed BIGINT。
- 当前 Markov restart state 仍只有全体已发布、已量化 close vector；bridge path 不跨日保存为 latent state。

### 3.2 Close endpoint law 保持不变

令相邻 observation offsets 为 \(a<b\)，定义

\[
M_i(a,b)=\frac1{365}\int_a^b
\left(\mu_i(u)-\frac12\sigma_i(u)^2\right)\,du,
\]

\[
A_i(a,b)=\frac1{365}\int_a^b\sigma_i(u)^2\,du.
\]

未量化 close proposal 为

\[
\log\frac{S^*_{i,b}}{S_{i,a}}
=M_i(a,b)+\sqrt{A_i(a,b)}\,Z^{\mathrm{close}}_{i,b},
\]

其中 cross-sectional close shock 继续满足

\[
Z^{\mathrm{close}}_b=\Lambda\eta_b+D^{1/2}\varepsilon_b,
\qquad \operatorname{Cov}(Z^{\mathrm{close}}_b)=R.
\]

已发布 close 为

\[
C_{i,b}=Q_{\mathrm{tick}}(S^*_{i,b}),
\]

且下一期从 \(C_{i,b}\) 重启。Brownian bridge 不重新生成或修改 close shock。

### 3.3 Piecewise-linear 精确积分

若某一子区间宽度为 \(h\) calendar days，线性函数端点为 \(f_L,f_R\)，则

\[
\int f(u)\,du=h\frac{f_L+f_R}{2},
\]

\[
\int f(u)^2\,du
=h\frac{f_L^2+f_Lf_R+f_R^2}{3}.
\]

跨 node 时必须按所有 node boundary 分段求和；node 外使用 frozen flat extrapolation。不得用 endpoint volatility、算术平均 volatility 或采样近似替代 integrated variance。

### 3.4 Brownian bridge grid contract

对每个非初始 observation interval：

- `open = previous published close`；继续采用 no-gap convention。
- `close = current published quantized close`。
- 固定 `steps = m = 64`。
- grid 为 uniform calendar fraction：

  \[
  u_j=a+\frac jm(b-a),\qquad j=0,\ldots,m.
  \]

- 定义 partial integrated log drift 与 variance：

  \[
  M_{i,j}=M_i(a,u_j),\qquad A_{i,j}=A_i(a,u_j).
  \]

- 因为 \(\sigma_i(t)>0\)，必须有 \(A_{i,m}>0\)，且 variance clock 单调非减。

### 3.5 Keyed bridge draw 与条件路径

对每个 \((i,b,j)\) 生成独立标准正态：

\[
\xi^{B}_{i,b,j}
=G\!\left(\operatorname{key}(\text{snapshot},\text{seed},
\text{bridge namespace},\text{bridge spec},b,i,j)\right).
\]

使用 variance-clock increments 构造未条件化 Brownian motion：

\[
\Delta W_{i,j}=\sqrt{A_{i,j}-A_{i,j-1}}\,\xi^B_{i,b,j},
\qquad
W_{i,j}=\sum_{k=1}^j\Delta W_{i,k}.
\]

Brownian bridge residual 为

\[
B_{i,j}=W_{i,j}-\frac{A_{i,j}}{A_{i,m}}W_{i,m}.
\]

令

\[
x_{i,0}=\log O_{i,b},\qquad x_{i,m}=\log C_{i,b}.
\]

条件 log path 为

\[
X_{i,j}
=x_{i,0}+M_{i,j}
+\frac{A_{i,j}}{A_{i,m}}
\left[x_{i,m}-x_{i,0}-M_{i,m}\right]
+B_{i,j}.
\]

该构造满足：

\[
X_{i,0}=x_{i,0},\qquad X_{i,m}=x_{i,m},
\]

\[
\operatorname{Cov}(B_{i,j},B_{i,k})
=A_{i,\min(j,k)}-
\frac{A_{i,j}A_{i,k}}{A_{i,m}}.
\]

因此它是在 integrated-variance clock 上、给定已发布 open/close 的精确 marginal Gaussian bridge；“精确”指本方案主动把已量化 close 当作点值 endpoint 后的 grid-point joint law。它不是“观察到一个 rounding bucket 后，对隐藏未量化 close 做积分”的条件分布，也不代表 continuous-time extrema 被精确抽样。

### 3.6 Price canonicalization 与 OHLC

对 interior grid points：

\[
P_{i,j}=Q_{\mathrm{tick}}(e^{X_{i,j}}),\qquad 1\le j<m.
\]

endpoint 不重新计算：

\[
P_{i,0}=O_{i,b},\qquad P_{i,m}=C_{i,b}.
\]

最终 bar 为

\[
H_{i,b}=\max_{0\le j\le m}P_{i,j},\qquad
L_{i,b}=\min_{0\le j\le m}P_{i,j}.
\]

量化 checkpoint 必须冻结为“先量化每个 grid price，再取离散 extrema”。不得在实现中改成“先取 raw extrema，再量化”，两者并非同一合同。

初始日期没有虚构前一日 transition：

\[
O_{i,0}=H_{i,0}=L_{i,0}=C_{i,0}=Q_{\mathrm{tick}}(S_{i,0}).
\]

`adjusted_close = close`、`dividend = 0`、`corporate_action = "none"` 保持不变。

### 3.7 Bridge 的跨资产边界

目标 v1 bridge 与 equity reference 一致：

- correlated P close endpoints 保留现有 \(R\)；
- 给定各自 open/close 后，bridge residual 按 underlying 分开 keyed；
- 不声明跨资产 intraday extrema correlation；
- bridge residual 不进入 `underlying_dependence_specs` 的 P/Q covariance mapping；
- 不允许用这些 high/low 推断 basket/spread intraday joint path。

如果未来任务需要同步 intraday multi-asset paths，必须新增 joint bridge driver block、共同 subgrid、完整 conditional covariance 与新 model/observation identity，不能静默沿用当前 marginal bridge。

### 3.8 Keyed lognormal volume contract

每个 underlying 必须显式提供：

- `base_volume = b_i`：正整数；含义是 round/clip 之前的期望 daily volume；
- `volume_log_stddev = s_i`：dimensionless log standard deviation。

keyed standard normal 为

\[
Z^V_{i,d}
=G\!\left(\operatorname{key}(\text{snapshot},\text{seed},
\text{volume namespace},\text{volume spec},d,i)\right).
\]

raw volume 为

\[
V^*_{i,d}=b_i\exp\!\left(s_iZ^V_{i,d}-\frac12s_i^2\right).
\]

mean correction `-0.5*s_i^2` 使得

\[
\mathbb E[V^*_{i,d}]=b_i,
\]

\[
\operatorname{Var}(V^*_{i,d})
=b_i^2\left(e^{s_i^2}-1\right),
\qquad
\operatorname{median}(V^*_{i,d})=b_i e^{-s_i^2/2}.
\]

published volume 固定为

\[
V_{i,d}=\operatorname{clip}_{[0,2^{63}-1]}
\left(\operatorname{ROUND\_HALF\_EVEN}(V^*_{i,d})\right).
\]

数值 identity 固定为：先在 binary64 log domain 检查 int64 boundary；安全时调用 `math.exp`，再对有限 binary64 raw volume 使用 Python `round(raw)` 得到 ties-to-even integer。不得在实现中改成 `int(raw)`、floor、随机舍入或先转 `Decimal`。该顺序避免极端参数下 binary64 overflow。初始日期也生成 keyed lognormal volume；它不是 bridge transition 的一部分。

volume stream 与 close factor、close idiosyncratic、bridge increments、option spread noise 使用不同 namespace。v1 不声明 return-volume、volatility-volume 或 cross-asset volume correlation。

### 3.9 RNG key 与 draw-order contract

FinMaths 应继续使用 `QuantLibGeneratorBase._derived_seed/_gaussian`，不切换到 equity repo 的 custom Box–Muller。推荐 key parts：

| 用途 | Key parts（`snapshot_id` 与 `seed` 由 base class 自动前置） |
|:---|:---|
| close common factor | 现有 `underlying-simulation, P, dependence_spec_id, factor, factor_index, date` |
| close idiosyncratic | 现有 `underlying-simulation, P, dependence_spec_id, idiosyncratic, underlying_id, date` |
| bridge increment | `bridge.stream_namespace, bridge_spec_id, increment, date, underlying_id, step` |
| volume | `volume.stream_namespace, volume_spec_id, date, underlying_id` |

要求：

- 每个 bridge step 直接 keyed，不允许使用一个长 stateful RNG 顺序消费；
- row order、underlying order、one-shot/append batch boundary 不得改变任何 draw；
- bridge/volume 新 draw 不得改变 close 或 option-noise key；
- config `1.8.0` 的 RNG identity 应升级，例如：  
  `QuantLib.BoxMullerMersenneTwisterGaussianRng/semantic-keyed-authoring-streams-sha256-v2`；
- 旧 config 继续要求并保留旧 RNG label 与旧输出。

## 4. Generator config `1.8.0` 合同

`1.8.0` 应建立在 `1.7.0` 的完整 P/Q dependence pair 和 common-Q contract 之上。不能把新字段塞进旧 `1.6.0/1.7.0` config identity。

推荐配置片段如下；未展示的 option-chain、q-pricing 和 P/Q dependence 字段继续按 `1.7.0` 要求存在：

```json
{
  "schema_version": "1.8.0",
  "generator_config_id": "quantlib-tdgbm-bsm-brownian-bridge-volume-v1",
  "generator_version": "0.9.0",
  "snapshot_id": "DERIVATIVES-TDGBM-BSM-BB-KEYED-VOLUME-v1",
  "rng": "QuantLib.BoxMullerMersenneTwisterGaussianRng/semantic-keyed-authoring-streams-sha256-v2",
  "intraday_bridge": {
    "bridge_spec_id": "TDGBM-LOG-PRICE-BROWNIAN-BRIDGE-64-v1",
    "method": "log_price_brownian_bridge",
    "steps": 64,
    "grid": "uniform_calendar_fraction",
    "variance_clock": "integrated_variance",
    "endpoint_policy": "published_quantized_open_close",
    "extrema_policy": "quantized_discrete_grid_only",
    "cross_asset_policy": "marginal_independent_given_close_endpoints",
    "stream_namespace": "underlying-intraday-brownian-bridge-v1"
  },
  "volume_model": {
    "volume_spec_id": "KEYED-MEAN-PRESERVING-LOGNORMAL-VOLUME-v1",
    "measure": "P",
    "method": "keyed_mean_preserving_lognormal",
    "rounding": "ROUND_HALF_EVEN_INTEGER",
    "overflow_policy": "clip_signed_int64",
    "dependence_policy": "independent_by_underlying_date_and_from_price_streams",
    "stream_namespace": "underlying-volume-keyed-lognormal-v1"
  },
  "underlyings": [
    {
      "underlying_id": "SYNTH-METAL-ALUMINUM",
      "initial_spot": 100.0,
      "physical_drift": {"type": "piecewise_linear", "nodes": [], "extrapolation": "flat"},
      "physical_volatility": {"type": "piecewise_linear", "nodes": [], "extrapolation": "flat"},
      "risk_free_rate": 0.03,
      "dividend_yield": 0.01,
      "base_volume": 1000000,
      "volume_log_stddev": 0.1435941754
    }
  ]
}
```

上例的 `nodes: []` 仅表示省略原有节点，正式配置仍必须提供合法节点。

### 4.1 Parser hard constraints

- `schema_version == 1.8.0` 时必须存在 `intraday_bridge` 与 `volume_model`。
- bridge fixed enums 必须逐项精确匹配上表；`steps` 为整数，范围 `[2, 4096]`，当前正式 baseline 固定为 64。
- `bridge_spec_id`、`volume_spec_id`、stream namespaces 必须为非空稳定字符串。
- 每个 underlying 必须有 `base_volume` 与 `volume_log_stddev`。
- `base_volume`：非 bool 正整数，且不超过 `2^63-1`。
- `volume_log_stddev`：有限数，推荐 v1 hard bound 为 `[0, 2.0]`。
- `physical_volatility` 仍要求所有节点严格为正。
- `1.8.0` 必须使用 P/Q `underlying_dependence_specs`，禁止 legacy `underlying_simulation`。
- config 必须把 bridge、volume 及全部 underlying parameters 纳入 immutable semantic identity；修改任一字段必须使用新的 snapshot ID。

### 4.2 迁移 baseline 参数

当前 uniform volume 的理论 mean 为 `1,000,000`，standard deviation 为

\[
\frac{500000}{\sqrt{12}}\approx 144337.5673.
\]

若第一版希望只改变分布形状并大致保留前两阶矩，可设置

\[
b_i=1{,}000{,}000,
\qquad
s_i=\sqrt{\log(1+\mathrm{CV}^2)}
\approx 0.1435941754.
\]

建议先对全部 22 个 underlyings 显式写入这组参数，完成机制迁移；后续若要引入 asset-specific liquidity heterogeneity，应在上游 authoring config builder 中 sampled-and-frozen，并创建新 config/snapshot identity，不能在 daily generator 内临时抽 base volume。

## 5. DuckDB 与可见性合同

### 5.1 Schema `2.6.0`

保留 `market.underlying_daily` 的 public column layout，不增加 bridge/volume parameter columns。新增两个 private parent tables：

```sql
CREATE TABLE market.intraday_bridge_specs (
    snapshot_id VARCHAR NOT NULL,
    bridge_spec_id VARCHAR NOT NULL,
    method VARCHAR NOT NULL,
    steps INTEGER NOT NULL,
    grid VARCHAR NOT NULL,
    variance_clock VARCHAR NOT NULL,
    endpoint_policy VARCHAR NOT NULL,
    extrema_policy VARCHAR NOT NULL,
    cross_asset_policy VARCHAR NOT NULL,
    stream_namespace VARCHAR NOT NULL,
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, bridge_spec_id)
);

CREATE TABLE market.underlying_volume_models (
    snapshot_id VARCHAR NOT NULL,
    volume_spec_id VARCHAR NOT NULL,
    underlying_id VARCHAR NOT NULL,
    measure VARCHAR NOT NULL,
    method VARCHAR NOT NULL,
    base_volume BIGINT NOT NULL,
    volume_log_stddev DOUBLE NOT NULL,
    rounding VARCHAR NOT NULL,
    overflow_policy VARCHAR NOT NULL,
    dependence_policy VARCHAR NOT NULL,
    stream_namespace VARCHAR NOT NULL,
    generator_config_id VARCHAR NOT NULL,
    created_run_id VARCHAR NOT NULL,
    PRIMARY KEY (snapshot_id, volume_spec_id, underlying_id)
);
```

实际 DDL 还应加入与 config enums 相同的 `CHECK`、`base_volume > 0`、`volume_log_stddev BETWEEN 0 AND 2` 和 foreign-key-equivalent quality gates。

### 5.2 Revision、manifest 与 metadata

- `metadata.snapshot_revisions` 新增 `intraday_bridge_spec_count` 和 `underlying_volume_model_count`。
- private parent manifest 可记录这两个 row count，但不能写 seed、stream namespace、base volume、log stddev 或完整 bridge contract。
- `market.pricing_metadata.physical_dynamics` 将旧字符串
  `separate_synthetic_range-v1` 替换为只含 ID 的 observation reference，例如：

  ```json
  {
    "ohlc_model_id": "TDGBM-LOG-PRICE-BROWNIAN-BRIDGE-64-v1",
    "volume_model_id": "KEYED-MEAN-PRESERVING-LOGNORMAL-VOLUME-v1"
  }
  ```

- `solver_visible.pricing_metadata` 不投影 bridge/volume parameters；`solver_visible.underlying_daily` 仍只投影最终 OHLCV。
- export/public-child leakage scan 应加入：`bridge_spec_id`、`volume_spec_id`、`stream_namespace`、`base_volume`、`volume_log_stddev`、`bridge increment` 等 private markers。

### 5.3 Schema migration boundary

- 冻结的 `2.0`--`2.5` snapshot 永不原地迁移或改写。
- mutable `2.5` 数据库可以结构性添加空的新表，但已有 `underlying_daily` path 不允许补挂新的 observation law。
- 一个已有 path 若没有 bridge/volume spec，不能在同一 snapshot ID 下新增 spec；反之亦不能删除或修改。
- 正式 `1.8.0` materialization 一律使用新数据库、新 snapshot ID。
- legacy configs 的 quality gates 按其旧 observation law 执行；`1.8.0` 才要求 `1 bridge row + N volume rows`。

## 6. 文件级执行方案

| 文件/目录 | 修改内容 |
|:---|:---|
| `src/synthetic_derivatives/authoring/config.py` | 新增 `IntradayBridgeConfig`、`VolumeModelConfig`；扩展 `UnderlyingConfig`；解析/校验 `1.8.0`；更新 RNG identity；将新字段纳入 immutable semantic comparison |
| `src/synthetic_derivatives/authoring/generator_common.py` | 保留现有 keyed QuantLib Gaussian；可新增安全的 `keyed_mean_preserving_lognormal_int64()` 纯 helper；不得改变旧 key serialization |
| `src/synthetic_derivatives/authoring/underlying_daily_generator.py` | 新增 partial integrated log moments、bridge grid 生成、bridge/volume spec rows；`1.8.0` 使用 bridge/lognormal，legacy 版本保留旧 heuristic/uniform |
| `src/synthetic_derivatives/authoring/schema.py` | 升级 `2.6.0`；新增两张 private tables、TableSpecs、revision counts 与 additive DRAFT migration；solver-visible columns 保持不变 |
| `src/synthetic_derivatives/authoring/pipeline.py` | 先 MERGE observation specs，再生成 daily rows；新增 immutable compatibility、count、exact replay、tamper 与 leakage gates |
| `src/synthetic_derivatives/authoring/cli.py` | default 指向新的 `/tmp` 数据库与 `1.8.0` config；推荐增加只读 `validate` 命令 |
| `authoring/templates/` | 新建 `1.8.0` 最小模板；旧模板标记 legacy，不原地改写 |
| `configs/generators/` | 新建 metals `v3` 配置；不修改 `quantlib_bsm_metals_option_chain_smoke_v2.json` |
| `src/synthetic_derivatives/authoring/README.md` | 替换旧 OHLC/volume 描述；补充 bridge 公式、volume 语义、weekend 和 cross-asset 边界 |
| `docs/authoring_pipeline.md` | 更新生成顺序、private tables、schema/version 与质量门 |
| `tests/unit/test_authoring_config.py` | bridge/volume positive、enum、steps、int64、log stddev、RNG label、legacy compatibility rejection cases |
| `tests/unit/test_underlying_simulator.py` | endpoint、grid length、OHLC invariant、midpoint variance、weekend、key partition、volume formula、append invariance |
| `tests/public/test_authoring_smoke.py` | tick alignment、new spec counts、idempotence、NOOP、freeze、new config runnable |
| `tests/public/test_snapshot_schema.py` | schema `2.6`、private table shape、solver-visible allowlist、frozen legacy snapshot remains readable |
| `tests/integration/test_tdgbm_bsm_backend_equivalence.py` | 更新 stable private tables；确认 backend dispatch 不跨越 P/Q boundary |

`option_daily_generator.py` 原则上不需要改动；若改动，仅限类型适配，不能让它读取 bridge、volume 或 P path-transition API。

`configs/model_families/tdgbm_bsm_v1.json` 与 `tdgbm_bsm_authoring_v1` backend ID 也不应原地修改：本方案把 OHLC/volume 定义为独立、显式版本化的 authoring observation contract，底层 P-close/Q-BSM model family 未改变。该 sidecar 中的 `random_ordering_id` 应解释为 model-driver ordering；新增 observation streams 由 bridge/volume spec IDs、namespaces 和 generator-config RNG identity 单独冻结。若仓库后续决定 `random_ordering_id` 必须覆盖所有 observation streams，则应新增 sidecar 版本并先扩展 registry，不得覆盖现有 v1 文件。

## 7. 推荐实现顺序

### Phase 0：冻结基线

1. 记录目标 branch SHA、当前 full-test 状态和 legacy config/snapshot identities。
2. 为新 config、generator、snapshot、bridge、volume 分配最终 IDs。
3. 明确本轮只改 trusted authoring source/config/tests/docs，不重生成 portable deliveries。

### Phase 1：Config 与纯数学 helper

1. 实现 `1.8.0` closed parser 与 dataclasses。
2. 实现 `integrated_log_moments(start_offset, end_offset)`，复用现有 piecewise exact integration。
3. 实现安全 volume log-domain boundary 与 int rounding。
4. 先完成纯 unit tests，再接数据库。

### Phase 2：Underlying generator

1. close 仍按现有 QuantLib process 生成并量化。
2. 对非初始 row 调用 `intraday_bridge_prices()`。
3. 每个 interior point 量化后取 high/low。
4. 初始 row 为 degenerate OHLC；所有日期使用 keyed lognormal volume。
5. 写入 metadata 中的 observation IDs。

### Phase 3：Schema 与 pipeline

1. 增加 private spec tables 与 TableSpecs。
2. transaction 内先持久化/核验 observation contracts，再生成 rows。
3. 新增 `validate`/freeze replay：从 initial published state 重放到数据库最大日期，exact compare `open/high/low/close/adjusted_close/volume/dividend/corporate_action`，忽略 run lineage。
4. 新增 private spec tamper detection 和 solver-visible leakage allowlist。

### Phase 4：新 config 与 disposable snapshot

1. 从当前 metals baseline 复制生成新的 `1.8.0` config；补齐 P/Q pair、bridge 与每个 underlying 的 volume parameters。
2. 先写入 `/tmp`：

   ```bash
   .venv/bin/python scripts/edit_snapshot.py \
     --database /tmp/metals-tdgbm-bb-keyed-volume-v1.duckdb \
     --config configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json \
     create-smoke
   ```

3. 运行 `summary`、`validate`、`freeze`，再做 read-only reopen 和 manifest check。
4. 未经明确请求，不把新数据库加入 `snapshots/public/`，也不触发 task packaging。

### Phase 5：测试顺序

```bash
.venv/bin/python -m pytest -q tests/unit/test_authoring_config.py
.venv/bin/python -m pytest -q tests/unit/test_underlying_simulator.py
.venv/bin/python -m pytest -q tests/public/test_authoring_smoke.py
.venv/bin/python -m pytest -q tests/public/test_snapshot_schema.py
.venv/bin/python -m pytest -q tests/integration/test_tdgbm_bsm_backend_equivalence.py
.venv/bin/python -m pytest -q
git diff --check
```

## 8. 必须新增的验收测试

### 8.1 数学测试

- bridge 第一个/最后一个值 exact 等于 published open/close。
- constant volatility、flat endpoint 下，midpoint log bridge variance 近似

  \[
  A(T)\,\theta(1-\theta),\qquad \theta=0.5.
  \]

- piecewise volatility 跨 node 时，partial variance clock 与解析积分一致。
- high/low 来自 `steps + 1` 个已量化点，且包含 endpoints。
- Monday bridge 使用 Friday-to-Monday 三个 calendar days 的 \(M/A\)。
- `s=0` 时 volume exact 等于 `base_volume`。
- 固定 mocked \(Z\) 时，volume exact 等于冻结公式与 ties-to-even rounding。
- keyed volume 在 underlying/date/order 改变时按合同变或不变。

### 8.2 确定性与 append 测试

- 相同 config/seed 重放 byte-identical logical rows。
- one-shot 6 days 与 create 5 days + append 1 day 完全相等。
- 增加 bridge steps 不改变 close key 或 option quote method input；但属于新 observation identity。
- 重排 config underlyings 不改变按 business key 排序后的 keyed draws；driver order 仍由 dependence spec 决定。
- 旧 config 的 golden rows/digest 保持不变。

### 8.3 数据库与权限测试

- `1.8.0` 必须恰有一个 bridge spec、每个 underlying 恰有一个 volume model row。
- 修改 persisted bridge steps、namespace、base volume 或 log stddev 后，`validate/freeze` 必须失败。
- `solver_visible` 与 manifest 不含 private parameters、seed 或 namespaces。
- OHLC tick alignment、正值、`high >= open/close`、`low <= open/close`。
- volume 在 signed BIGINT 范围内。
- transaction failure 不留下 partial market/spec rows。
- FROZEN snapshot 拒绝 append、sync、schema migration 和 observation-law retrofit。

### 8.4 P/Q 回归测试

- physical drift 只进入 P close/bridge conditional mean，不进入 option pricing。
- volume 不进入 option price、IV、Greeks 或 dependence matrices。
- option generator 仍没有 `underlying_close_shock`、bridge 或 volume API。
- Q pricing 继续只消费 realized `spot_close`、contract 和 Q inputs。

## 9. 执行与使用边界

### 9.1 In scope

- trusted parent authoring 的 daily OHLCV observation law；
- config、generator、DuckDB private provenance、quality gates、tests 和 authoring docs；
- 新 identity 下的 disposable validation snapshot。

### 9.2 Out of scope

- 不改变 close 的 P transition 或 P/Q covariance mapping；
- 不改变 BSM Q pricing、bid/ask noise、IV、Greeks、solver 或 verifier 数值合同；
- 不模拟 overnight gap、auction、intraday timestamps、market microstructure、halts、price limits、splits、dividends或其他 corporate actions；
- 不生成连续时间 exact maximum/minimum；
- 不声明 cross-asset intraday bridge/extrema dependence；
- 不建立 price-volume dependence 或 cross-asset volume dependence；
- 不把 high/low 当作 barrier-option continuous monitoring truth；
- 不原地改动 frozen snapshot、accepted task package 或 delivery；
- 不自动重打 100-task/24-task packages。

### 9.3 Weekend/no-gap 语义

当前推荐方案严格复用 equity reference：Monday bar 的 bridge 从 Friday published close 跨到 Monday published close，并对完整三天 calendar interval 积分。它是“相邻 observation interval 的 latent bridge extrema”，不是只覆盖 Monday exchange session 的 traded high/low。

如果需要真实 session OHLC，必须拆分 overnight gap 与 session bridge，并引入 session calendar/clock；那是新合同，不应混入本次 v1.8 迁移。

### 9.4 Public/private 边界

| 层 | 可见内容 | 禁止内容 |
|:---|:---|:---|
| Trusted authoring | seed、P/Q specs、bridge/volume configs、run lineage、full parent DB | 无 |
| Export builder | frozen parent 的读取权限；只做 allowlisted projection | 不把 private specs 复制到 public child |
| Agent/solver | 任务需要的 OHLCV/quotes 与公开 contract | seed、bridge steps/namespace、base volume/log stddev、P factors、canonical answers |
| Trusted verifier | 任务所需 public inputs 与独立 method contract | 不把 authoring hidden parameters当作 submission answer |

## 10. 版本与制品策略

推荐 identity 变化：

| 对象 | 当前 | 目标 |
|:---|:---|:---|
| Generator config schema | latest `1.7.0` | `1.8.0` |
| Generator version | current metals `0.8.0` | `0.9.0` |
| DuckDB schema | `2.5.0` | `2.6.0` |
| Observation law | heuristic range + uniform volume | marginal log bridge + keyed mean-preserving lognormal |
| Snapshot/config IDs | 现有 IDs | 全部新 IDs |
| Frozen deliveries | 不变 | 只有用户另行要求时才生成新 release identity |

注意：FinMaths RNG key 包含 `snapshot_id`。因此新 snapshot ID 会使 close draws 也发生 byte-level 变化，即使 close 的数学 law 与 key structure 没变。验收应保证“算法/分布/stream partition 不受 bridge/volume 反向影响”，不应要求新旧 snapshot close bytes 相同。若未来需要严格 ablation-preserved close path，应单独设计并版本化 `stochastic_root_id`；本次不引入该兼容层。

## 11. Definition of Done

只有同时满足以下条件才可宣布改造完成：

- `1.8.0` config 对 bridge、volume、P/Q、units、dtype、RNG 和 canonicalization 无未声明自由度；
- close、bridge、volume 数学公式与实际代码逐项一致；
- one-shot/append/replay exact 一致；
- private bridge/volume specs 可检测 tampering，且不泄漏到 public/Agent views；
- legacy configs 和 frozen artifacts 保持可读且 byte identity 不被原地改变；
- option pricing、IV/Greeks 与 P/Q permission boundary 回归通过；
- disposable snapshot 完成 create → validate → freeze → read-only reopen；
- 相关 targeted tests、full pytest 和 `git diff --check` 全部通过；
- 未经明确授权，不提交新 public DB、不重生成 task deliveries、不更新 accepted manifests/checksums。

## 12. 参考路径

- FinMaths authoring implementation：  
  <https://github.com/ruihuabunny/FinMaths-synthetic-data-demo/tree/synthetic-BSM-agent-task/src/synthetic_derivatives/authoring>
- FinMaths current underlying generator：  
  <https://github.com/ruihuabunny/FinMaths-synthetic-data-demo/blob/synthetic-BSM-agent-task/src/synthetic_derivatives/authoring/underlying_daily_generator.py>
- Equity Brownian bridge/keyed lognormal reference：  
  <https://github.com/ruihuabunny/equity-quant-synth-agent-task/blob/main/src/equity_quant/authoring/simulator.py>
- Equity mathematical contract：  
  <https://github.com/ruihuabunny/equity-quant-synth-agent-task/blob/main/docs/stock_data_simulator.md>
