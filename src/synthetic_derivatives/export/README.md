# Public solver-database export

本包把一个已经 `FROZEN` 的 authoring DuckDB 确定性投影成新的 public child DuckDB。
两个文件是独立安全边界：child 只含任务允许的市场事实，不保存 `market` schema、run UUID、
生成 seed/RNG、authoring IV audit、latent function node heights、oracle、答案或 parent 路径。

## 数学边界

Exporter 不生成随机数，不创建新的市场 transition，也不估计价格、IV 或 Greeks。
`sampling_seed` 只用于 SHA-256 rank 的确定性行选择，不属于任何 \(\mathbb P\) 或
\(\mathbb Q\) 随机流，并且不会写入 public DB。

`solver_visible.underlying_state` 是父快照已物化的、`measure='P'` 的 published
ex-dividend spot observation。`solver_visible.pricing_context` 明确标记 `measure='Q'`，并保存
共同 risk-neutral measure、money-market numeraire 与 rate-path identity。

对抽中的 underlying 集合，Exporter 按 parent driver order 找到对应 indices，并同步截取
\(\Lambda\) 的 rows、\(D\) 的 diagonal entries 和 \(R\) 的 principal submatrix：

\[
R_I=\Lambda_I\Lambda_I^\mathsf{T}+D_I.
\]

因此 child 的 P/Q dependence 仍由同一个 factor construction 精确给出；实现不做
eigenvalue clipping、nearest-correlation repair 或 Cholesky 替代。P/Q 使用新的 child
dependence identities，Q row 显式引用 child P row，并保留 drift-only
same-Brownian-covariance mapping 与共同 Q context。

## 固定 public contract

Schema `solver-market-public-duckdb-v1.0.0` 只允许以下 base tables：

- `metadata.public_task`
- `solver_visible.underlying_state`
- `solver_visible.option_contracts`
- `solver_visible.option_chain_quotes`
- `solver_visible.pricing_context`
- `solver_visible.underlying_dependence`

Exporter 固定 table/column order、DuckDB `1.5.5` logical types、price/strike
`DECIMAL(24,8)` scale、JSON sorted-key serialization、canonical row order 和 export version。
Underlying state 只保留 valuation-date `spot_close`，报价表只保留 bid/ask；OHLC range、
volume/open interest、`mid`、`settlement_price` 与 authoring-derived analytics 都不是当前任务
输入，因而不进入 child。后续 task contract 必须从 solver-visible bid/ask 计算 midpoint。

默认按 SHA-256 rank 无放回抽取 8 个 underlying。调用方必须给出一个 valuation date；若还给出
`option_ids`，child 只复制这些明确任务行，否则复制抽中资产在该日的全部 live option rows。
完整 selected underlyings、date、option IDs 和逐表行数会写入 deterministic subset manifest，
但原始 sampling seed 不会写入。

## Identity、checksum 与只读交付

- Parent 必须只含一个 `FROZEN` snapshot，并具备完整 P/Q dependence pair。
- Parent logical checksum 覆盖当前经济/模型 rows，但排除 generation run UUID 和 wall-clock
  materialization timestamps。
- Sample ID 覆盖 parent logical checksum、private selection seed、selected underlyings、variant
  与 export version；task ID 再覆盖 valuation date 和完整 option IDs。
- Public logical checksum 覆盖 allowlist 中每张表的类型化 column contract 与全部 canonical
  logical rows。它忽略 DuckDB 物理布局和文件 mtime，但任一公开逻辑值变化都会改变 checksum。
- Parent 以 `READ_ONLY` attach，完成复制后立即 detach；child 经 pre/post recursive leakage scan
  后原子发布。扫描会进入 JSON 字符串内的 nested objects/lists。
- `open_solver_database()` 只以 DuckDB read-only mode 打开已经通过安全扫描的 child。生产环境
  仍须只挂载 child，不向 Solver 挂载或传递 parent。当前 D4 Greeks package 进一步只通过
  counted trusted query adapter 暴露 task rows，不把 raw DuckDB connection 交给 Agent；生产
  部署仍须在 OS/container 层执行同一 filesystem/network policy。

最小调用方式：

```python
from datetime import date

from synthetic_derivatives.export import (
    SolverDatabaseExportContract,
    export_solver_database,
)

manifest = export_solver_database(
    "/private/frozen-parent.duckdb",
    "/public/task.duckdb",
    SolverDatabaseExportContract(
        variant_id="bsm_market_implied_greeks_v1",
        valuation_date=date(2026, 8, 3),
        sampling_seed=17,
    ),
)
```

Generic 六关系 P2 child 仍只在 `tests/integration/` 与
`tests/packaging_analytic_and_implied_greeks_iv/` 的 pytest 临时目录中创建，不作为独立长期制品提交。
其上已经完成 task-specific 二次投影：accepted
`bsm_market_implied_greeks_v1` package 提交一份独立三关系 DuckDB，包含
`metadata.public_task`、`solver_visible.greeks_task_inputs` 和
`solver_visible.greeks_task_contract`。它通过 trusted adapters 接到标准库固定 80 步 IV +
Greeks solver，并由 pinned QuantLib verifier exact-compare；详见
[`task_packages/README.md`](../../../task_packages/README.md)。当前 checked-in source artifact
仍只有一条 `ACCEPTED` golden task；Phase F 已参数化 private selector seed，并提供 verified
nine-field dataset exporter。Valuation date 仍由 frozen package contract 固定；旧 interface
有一套 Git-ignored 本地 100-task run，当前最小 prompt/interface 的完整 batch、split audit
与 `RELEASED` promotion 尚未执行。
