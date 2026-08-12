# Analytic BSM、IV 与 Market-implied Greeks

本包集中维护 Solver 侧已经实现的三个标准库数值模块。目录组织是开发与审阅边界；它不会
自动把这些模块加入 Agent runtime allowlist，也不会改变 Trusted verifier 的独立实现要求。

## 模块职责

| 模块 | 输入与职责 | 主要公开函数 |
|:---|:---|:---|
| [`bsm.py`](bsm.py) | 给定 pricing volatility，计算 European BSM unit price 与五个 analytic Greeks | `bsm_analytic_values`、`solve_bsm_greeks`、`solve_bsm_greeks_batch` |
| [`bsm_implied_volatility.py`](bsm_implied_volatility.py) | 从 solver-visible bid/ask midpoint 解一个 scalar BSM IV inverse | `solve_bsm_implied_volatility` |
| [`bsm_market_greeks.py`](bsm_market_greeks.py) | 按公开 row order 组合 visible-price IV 与五个 unit Greeks | `solve_market_implied_root`、`solve_market_greeks_submission` |

三个模块共享 BSM price primitive，但不共享 Trusted verifier 的 QuantLib 数值实现。

## 数学合同

定价使用 USD money-market account 作为 numeraire，并在对应的风险中性测度 `Q` 下处理
ex-dividend spot。给定 valuation timestamp 的公开状态与合同参数后，常参数模型为

$$
\frac{dS_t}{S_t}=(r-q)dt+\sigma_Q dW_t^Q,
$$

其中 `r` 与 `q` 是 flat continuous 年化 rate/yield，`sigma` 是年化 `Q`-measure pricing
volatility，期限 `T` 严格使用 Actual/365 Fixed。当前产品只包含正 spot、正 strike、正期限的
cash-settled European call/put。公式是 constant-coefficient BSM 的解析结果，不包含时间步进
或近似 transition。

`bsm.py` 的 `sigma` 是任务直接给定的 `Q`-measure pricing volatility。对于 IV 与
market-implied Greeks，`sigma_IV` 则由实际 solver-visible quote 反解；二者都不是
`P`-measure physical volatility。此包不接受或读取 physical drift、physical diffusion
nodes、historical correlation、authoring latent volatility 或 private IV audit。

## 数值与单位

- 所有定价、求根与 Greek 原始计算使用 Python binary64 与标准库 `math`，操作顺序由 task
  contract 冻结。
- IV midpoint 先按 Decimal quote 语义计算，再只 cast 一次为 binary64。
- Scalar IV 先检查贴现 BSM price domain，再检查 bracket `[1e-6, 5.0]`；成功路径固定执行
  80 次 midpoint update，不 early-stop，也不使用 Newton、Brent 或 fallback。
- Delta 按每 1 spot unit；Gamma 按每 1 squared spot unit；Vega 按 volatility 上升 1
  percentage point；Theta 按 valuation time 前进 1 calendar day且 expiry 固定；Rho 按
  continuous rate 上升 1 percentage point。
- Vega、Theta 与 Rho 在 analytic evaluation 层按合同分别缩放为每 1 vol point、每 calendar
  day 与每 1 percentage point rate；最终输出层只执行 Decimal canonicalization 和 8 位
  `ROUND_HALF_EVEN` 舍入。`d1`、`d2` 仅是瞬时局部变量，不进入 schema、配置或输出。

输入、单位、状态与序列化合同分别位于
[`tasks/bsm_greeks.py`](../../tasks/bsm_greeks.py)、
[`tasks/bsm_implied_volatility.py`](../../tasks/bsm_implied_volatility.py) 和
[`tasks/bsm_market_greeks.py`](../../tasks/bsm_market_greeks.py)。Solver 与 verifier 只能共享
这些合同，不得共享实际 pricing、root 或 Greek implementation。

## 权限与发布边界

- Solver 实现不得导入 QuantLib、py_vollib、mibian、rateslib、SciPy pricing API 或
  `synthetic_derivatives.verifier`。
- Trusted verifier 继续位于 [`verifier/`](../../verifier)，使用 pinned QuantLib 独立复算；
  verifier 不得导入本包的数值实现。
- Golden task 的 train/dev observable reference source 是 package 内受 source/runtime policy
  审计的 standalone artifact，不是把整个 repo 包直接暴露给 Agent；evaluation runtime
  只挂载 `public/`，不可读取该 reference artifact。
- Authoring/package QA 可以调用本包检查候选 rows，但 canonical truth 仍由独立 verifier 从
  public inputs 重算。

新子包与 `synthetic_derivatives.solver` façade 都继续导出既有的六个公共函数。三个旧的
module dotted paths 已随目录迁移移除，没有兼容 shim；调用方应使用本包路径或
`synthetic_derivatives.solver` 的函数级 façade。

## 测试映射

| 测试 | 覆盖 |
|:---|:---|
| [`test_bsm_solver.py`](../../../../tests/unit/test_bsm_solver.py) | stdlib-only import boundary、解析价格/Greeks 不变量、单位与 canonical row order |
| [`test_bsm_implied_volatility.py`](../../../../tests/unit/test_bsm_implied_volatility.py) | Decimal midpoint、price bounds、固定 bracket/80 步 schedule、status 与失败行为 |
| [`test_bsm_greeks_verifier.py`](../../../../tests/integration/test_bsm_greeks_verifier.py) | Solver 与 pinned QuantLib analytic engine 的 canonical exact equality |
| [`test_bsm_iv_verifier.py`](../../../../tests/integration/test_bsm_iv_verifier.py) | Solver 与独立 QuantLib IV reference 的 canonical exact equality |
| [`tests/packaging_analytic_and_implied_greeks_iv/`](../../../../tests/packaging_analytic_and_implied_greeks_iv/README.md) | D4 market-implied Greeks、runtime isolation、negative submissions 与 release views |

从仓库根目录运行相关回归：

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_bsm_solver.py \
  tests/unit/test_bsm_implied_volatility.py \
  tests/integration/test_bsm_greeks_verifier.py \
  tests/integration/test_bsm_iv_verifier.py
```
