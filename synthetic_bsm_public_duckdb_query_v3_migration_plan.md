# Synthetic BSM Agent Task：Public DuckDB Query v3 迁移方案

## 0. 文档目的

本文用于指导 `synthetic-BSM-agent-task` 分支从当前的静态 JSON trusted-tool 协议迁移到真正的只读 DuckDB 查询协议。

本次迁移的核心目标是：

1. 让 LLM 通过 trusted SQL tool 自己探索公开 DuckDB 的 schema、relation、column 和 join 关系；
2. 让每个 task 的 `task.duckdb` 成为唯一 solver-visible 数据源；
3. 在新版本中移除重复的 `trusted_tools/payloads/options.json` 和 `underlyings.json`；
4. Prompt 不再直接公布数据库结构，但继续完整公布金融数学、单位、数值方法和输出语义；
5. 保留严格的 public/private boundary、sandbox、pytest verifier 和可重放 reference trajectory；
6. 不重新生成 underlying/option market data，仅使用已经冻结的 source packages 重新 packaging。

这是一项 breaking protocol migration。不得原地篡改已经冻结的 v2 deliveries。

---

## 1. 当前实现状态

当前两类 delivery 均采用相同的静态 payload 模式：

- `task_packages/deliveries/bsm_market_implied_greeks_v1/20260813_prompt_v2_100/`
- `task_packages/deliveries/bsm_market_implied_metric_suite_v1/20260814_metric_6x4_unique_db/`

每个 task 当前同时包含：

```text
task.duckdb
trusted_tools/toolset.json
trusted_tools/payloads/options.json
trusted_tools/payloads/underlyings.json
```

其中：

- `task.duckdb` 已经是 public-only child database；
- `options.json` 和 `underlyings.json` 是从该 DuckDB 再次导出的静态副本；
- `toolset.json` 当前使用 `static-json-query-schema-submit-v2`；
- `query_greeks_underlying_market_v2` 和 `query_greeks_option_quotes_v2` 实际读取 JSON，并不执行 DuckDB SQL；
- Prompt 明确禁止 raw database connection，因此当前任务不是 database-exploration task，而是 static-payload tool task。

当前 `task.duckdb` 已经只允许以下公开 relations：

```text
metadata.public_task
solver_visible.underlying_market_inputs
solver_visible.option_quote_inputs
```

当前数据库安全验证还明确禁止 `market` authoring schema，并检查 relation allowlist、column contract 和 private leakage。因此，本次迁移不需要再额外复制一个 `public.duckdb`。

---

## 2. 冻结的架构决策

### 2.1 唯一数据源

新版本中：

- `task.duckdb` 是唯一 public market-input source of truth；
- `options.json` 和 `underlyings.json` 不再出现在新生成的 per-task delivery；
- verifier 仍可读取同一个 `task.duckdb`，再结合 private `oracle_config.json` 重建标准答案；
- 不新增含重复 market rows 的 `public.duckdb`；
- 不建立公开 answer table、IV table 或 Greeks table。

这里的“public”指数据库内容不含 private answer 或 authoring truth，并不意味着把数据库文件路径直接暴露给 solver。

### 2.2 访问边界

推荐继续把 `task.duckdb` 标记为：

```text
tool_host_only
```

LLM 不允许：

- `import duckdb` 后直接打开文件；
- 读取 task root 中的数据库文件路径；
- 读取 verifier、reference、audit、parent 或 generator artifacts。

LLM 只能通过新的 trusted SQL tool 查询该数据库。

### 2.3 Prompt 原则

新 Prompt 应遵循：

> 隐藏数据库结构，但不隐藏金融语义和验收语义。

Prompt 不再直接提供：

- schema 名称；
- table 名称；
- column 列表；
- underlying/option join key；
- underlying 和 option 的固定 row counts；
- 固定 SQL 查询语句；
- “先查询 underlying，再查询 option”的规定路径；
- 依赖某次 SQL 返回顺序的 submission order。

Prompt 必须继续提供：

- target metric；
- BSM / European option / pricing-measure 约定；
- bid/ask midpoint 规则；
- rate、dividend yield、day-count 约定；
- IV inversion 或 Greek calculation 的指定方法；
- vega、theta、rho 等单位；
- numeric dtype、precision 和 canonicalization；
- allowed/forbidden imports 和 operations；
- trusted SQL tool 名称及 query budget；
- submission schema、完整性要求和 submit tool；
- 禁止调用现成 pricing/IV/Greeks package。

---

## 3. 目标 per-task 目录结构

新版本的 task leaf 应为：

```text
<task_root>/
├── task.duckdb
├── delivery_manifest.json
├── source_manifest.json
├── evaluation_view/
│   ├── manifest.json
│   └── public/
│       ├── prompt.md
│       ├── runtime_contract.json
│       └── submission.schema.json
├── trusted_tools/
│   └── toolset.json
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

新版本不得出现：

```text
trusted_tools/payloads/
trusted_tools/payloads/options.json
trusted_tools/payloads/underlyings.json
```

历史 v2 delivery 中的这些文件继续保留，用于旧版本重放和 provenance，不做原地删除。

---

## 4. Trusted SQL Tool v3 契约

### 4.1 Tool 集合

建议将两个固定 query tools：

```text
query_greeks_underlying_market_v2
query_greeks_option_quotes_v2
```

替换为：

```text
query_public_duckdb_v3
```

Submission tool 建议同步升级为：

```text
submit_greeks_submission_v3
```

如果最终决定完全不修改 output JSON contract，可以保留旧 submit tool 名称；但 toolset、host protocol 和 runtime contract 仍必须升级版本。不要在实现阶段静默决定，需在 contract freeze 时选定一种方案。

### 4.2 Query arguments

建议 arguments schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["sql"],
  "properties": {
    "sql": {
      "type": "string",
      "minLength": 1,
      "maxLength": 20000
    }
  }
}
```

一次 tool call 只允许一条 SQL statement。

### 4.3 Query response

建议统一返回：

```json
{
  "columns": ["column_a", "column_b"],
  "rows": [
    ["value_a", "value_b"]
  ],
  "row_count": 1,
  "truncated": false
}
```

序列化约定必须冻结：

- DuckDB `DATE`：ISO `YYYY-MM-DD` string；
- DuckDB `DECIMAL`：exact decimal string，避免丢失 bid/ask midpoint 精度；
- DuckDB `DOUBLE`：JSON number；
- DuckDB `VARCHAR`：JSON string；
- `NULL`：JSON null；
- column order：与 query result 一致；
- row order：与 SQL result 一致，但没有 `ORDER BY` 时不提供稳定性保证；
- 单次最大返回 rows：至少覆盖当前 160 option rows；
- 单次最大返回 bytes：写入 runtime resource budget；
- 超限时必须明确返回 `truncated: true`，不得静默截断。

为了防止模型依赖隐式数据库顺序，Prompt 和 reference solver 中需要在顺序重要时显式使用 `ORDER BY`。

### 4.4 Query budget

当前 `trusted_query_calls = 2` 不再适用。

建议 baseline：

```text
maximum query calls: 10
minimum required query calls: 1
submission calls: exactly 1
```

Schema inspection 也计入 query budget。

不要强制固定 query schedule。模型不需要为了满足协议机械执行 `SHOW TABLES` 或 `DESCRIBE`；只要在 budget 内成功获得所需公开数据即可。

### 4.5 允许的 SQL

允许：

- 单条只读 `SELECT`；
- `WITH ... SELECT`；
- `SHOW TABLES`；
- `DESCRIBE <relation>`；
- 查询允许的 `information_schema` metadata；
- 对 public allowlist relations 做 projection、filter、aggregation、join、sorting。

允许访问的 schema 必须限制为：

```text
metadata
solver_visible
information_schema
```

即使 LLM 不知道这些名称，tool host 仍在内部使用 allowlist 校验。

### 4.6 拒绝的 SQL 和能力

必须拒绝：

- 多 statement SQL；
- `ATTACH` / `DETACH`；
- `COPY` / `EXPORT` / `IMPORT`；
- `INSTALL` / `LOAD`；
- `CREATE` / `DROP` / `ALTER`；
- `INSERT` / `UPDATE` / `DELETE` / `MERGE`；
- `SET` / `RESET` / unsafe `PRAGMA`；
- secrets、extension、remote database 功能；
- filesystem/network table functions；
- `read_csv*`、`read_json*`、`read_parquet*` 等外部读取函数；
- 对非 allowlist catalog/schema/relation 的访问；
- verifier/private/reference path 或内容访问。

安全实现不得只依赖简单 substring/regex blocklist。至少需要：

1. 单 statement validation；
2. statement type allowlist；
3. referenced relation/schema allowlist；
4. function/table-function allowlist 或明确 external-access deny；
5. read-only DuckDB connection；
6. 无网络、无外部文件的隔离 tool-host runtime；
7. query timeout、row limit、byte limit 和 memory limit。

read-only connection 本身不足以阻止通过 table function 读取其他文件，因此必须同时保留 runtime isolation 和 SQL validation。

---

## 5. Prompt v3 设计

### 5.1 推荐结构

每个 metric prompt 使用统一模板：

```text
1. Task objective
2. Public data access
3. Model and numerical conventions
4. Target output and units
5. Allowed and forbidden resources
6. Output and submission
```

其中只有以下部分随 metric target 改变：

- task title；
- target field；
- method ID / required numerical method；
- metric-specific units；
- metric-specific output row fields。

数据库访问段应在六个 target 间保持一致。

### 5.2 推荐的公共数据段

```markdown
## Public data access

All public inputs required to solve this task are stored in a read-only
DuckDB dataset.

Use `query_public_duckdb_v3` to inspect the available schemas, relations,
columns, keys, and public data, and then retrieve the inputs required for
the calculation.

You may make at most 10 read-only database queries. Database inspection
queries count toward this limit.

Use only information obtained from the public database and this task
contract. Do not access the database file directly or access verifier,
reference, private, audit, parent, or generator artifacts.
```

不要在该段增加：

- 实际 schema/table 名；
- field names；
- join keys；
- row counts；
- 完整 SQL examples。

### 5.3 结果完整性和顺序

删除当前 Prompt 中类似：

```text
Preserve the option-query row order in the submission.
```

原因：自由 SQL query 的返回顺序由模型查询决定，不存在唯一的“option-query row order”。

改为：

```markdown
Return exactly one result for every public option row.
Each public `row_id` must appear exactly once.
Row order is not semantically significant.
```

Private verifier 必须按 `row_id` key-align 后再校验，不得直接比较未对齐的 row list。

如果业务方必须要求 canonical order，则明确要求 `row_id` ascending，并在 public schema、reference solver 和 verifier 三处保持一致；不得依赖 DuckDB 的隐式返回顺序。

### 5.4 必须保留的数学语义

虽然 Prompt 不再介绍数据库结构，但以下内容不能要求 LLM 从 schema 猜测：

- European BSM model；
- pricing measure / money-market numeraire 约定；
- rate 和 dividend yield 是 annualized continuously compounded decimals；
- `Actual365Fixed`；
- observed option price 的 midpoint 规则；
- midpoint 的 Decimal-to-binary64 转换规则；
- IV bracket、iteration count、root finder；
- output canonicalization 和 `ROUND_HALF_EVEN`；
- delta/gamma/vega/theta/rho 的单位和符号约定；
- one unit option，不乘 contract multiplier；
- 禁止使用 hidden P-measure drift/diffusion 或 generator latent volatility。

数据库探索难度应来自 schema discovery、SQL retrieval 和 join，不应来自猜测金融单位。

### 5.5 继续禁止公式泄漏

Prompt、runtime contract、submission schema、toolset、数据库 metadata 均不得包含：

- BSM pricing formula；
- `d1` / `d2` 公式；
- Greeks 公式；
- IV 标准答案；
- answer-producing macro/UDF；
- QuantLib trajectory 或 oracle implementation details。

---

## 6. Public submission schema 调整

当前 metric task 的 `submission.schema.json` 体积约为 47 KB，需要检查其是否使用 `prefixItems`、row-level `const` 或类似机制枚举全部 `row_id` 和固定顺序。

如果存在这种设计，新版本应改成结构性 schema：

- top-level required fields；
- target-specific method/status fields；
- `rows` 为 array；
- item schema 只规定 `row_id` 和目标 metric 字段；
- `additionalProperties: false`；
- numeric canonical string pattern；
- 不枚举全部 row IDs；
- 不通过 schema 泄漏数据库 row count；
- 不通过 `prefixItems` 强制数据库顺序。

以下动态语义交给 private verifier：

- exact expected row-id set；
- no missing rows；
- no duplicate row IDs；
- no extra row IDs；
- 每个 row 与对应 public option input 的数值结果；
- task identity 与 database identity 一致。

如果保留 top-level `task_id: const`，应明确它只是 submission binding，不是数据输入；不可同时在 schema 中嵌入全部 option row identities。

---

## 7. 代码修改范围

### 7.1 `portable_tools.py`

当前文件负责 static JSON payload export、validation 和 replay。迁移建议：

1. 保留现有 v2 static JSON 逻辑用于历史 package validation；
2. 新增独立的 v3 DuckDB tool-host 实现，避免破坏旧版 replay；
3. 新版本删除：
   - `_TOOLSET_FILES` 中两个 payload paths；
   - `_query_tool(... payload_path, payload_digest ...)`；
   - `export_portable_greeks_toolset()` 的 JSON materialization；
   - `load_portable_greeks_task()` 对 payload JSON 的读取；
   - JSON-to-DuckDB identity comparison；
4. 新版本新增：
   - database digest binding；
   - allowed schemas/relations；
   - query budget；
   - query response serialization contract；
   - restricted SQL execution和call accounting；
   - submission sink binding。

推荐将 v3 实现放入新模块，例如：

```text
duckdb_query_tools.py
```

而不是把 v2/v3 分支全部堆入同一个大文件。

### 7.2 `portable_delivery.py`

需要：

- `_TOOL_FILES` 只保留 `toolset.json`；
- `_write_trusted_tools()` 改成写入 DuckDB-query toolset；
- 删除 JSON payload export；
- 删除 payload 与 `task.duckdb` 的逐行 equality validation；
- expected file tree 不再包含 `trusted_tools/payloads/*`；
- artifact visibility 和 artifact digest maps 同步更新；
- data identity 改由：
  - `task.duckdb` file digest；
  - logical checksum；
  - market content digest；
  - database schema version；
  共同约束。

### 7.3 `portable_metric_suite.py`

对六个 targets 做与 `portable_delivery.py` 相同的改造：

- `delta`
- `gamma`
- `iv`
- `rho_1pct`
- `theta_1calendar_day`
- `vega_1volpt`

继续保留：

- 每个 target × source market assignment 的独立 task ID；
- 独立 metric-projected DuckDB；
- metric-specific prompt/runtime/schema；
- metric-specific verifier oracle config。

不得为了去重而让六个 task 共享一个可写数据库文件或共享 mutable task identity。

### 7.4 `database.py`

现有 public child database allowlist 和 leakage validation 应保留。

仅在需要时新增：

- v3 query-result canonical serializer；
- allowed relation registry；
- query host 的 public-schema introspection helpers；
- query execution limits。

不要在数据库里新增 answer-producing view、macro 或 UDF。

### 7.5 `prompt_renderer.py`

需要：

- 新增统一的 DB-access block；
- 删除 table、field、join-key 和固定 row-count 描述；
- 删除两个旧 query tool 的 exactly-once 指令；
- 替换为 v3 SQL query budget；
- 删除 preserve-query-order；
- 保留 metric-specific mathematical semantics；
- 保留 no-formula-leakage 要求。

建议将 renderer 拆成：

```text
render_task_objective(spec)
render_public_database_access()
render_numerical_conventions(spec)
render_target_output(spec)
render_runtime_restrictions()
render_submission_contract(spec)
```

### 7.6 Runtime contract / capability files

需要更新：

- trusted tool names；
- trusted query call budget；
- host protocol version；
- toolset schema version；
- submission tool version（如决定升级）；
- database query result byte/row limits；
- denied operation wording。

仍然保留：

```text
raw_database_connection
undeclared_filesystem_access
network_access
process_spawn
dynamic_import
dynamic_package_installation
verifier_reference_or_private_access
```

Prompt 中允许“通过 trusted tool 查询 DuckDB”不等于允许 solver 自己 `import duckdb`。

### 7.7 Reference solver 与 trajectory

更新：

- `llm_solutions/run_bsm_market_implied_greeks.py`；
- reference tool adapter；
- trajectory event schema（如 tool name 被写入 schema）；
- replay tests；
- 所有六个 metric 的 reference trajectories。

Reference trajectory 推荐展示：

1. schema discovery；
2. relation/column inspection；
3. market-input retrieval；
4. data join；
5. IV inversion / metric calculation；
6. submission。

但 runtime verifier 不强制其他 agent 完全复制该 query sequence。

### 7.8 Verifier

数值 verifier 的核心算法不需要因 DB-query migration 改变。

必须确认：

- verifier 继续从 `task.duckdb` 读取公开 market inputs；
- verifier 使用 private `oracle_config.json`；
- verifier 不读取已删除的 JSON payload；
- verifier 按 `row_id` 对齐；
- row order 不影响通过与否；
- exact row-id set、duplicates、missing、extra 均被拒绝；
- 当前指定的 method、dtype、precision 和 canonicalization 保持不变。

---

## 8. 版本策略

本次迁移必须升级以下 contract identity：

- host protocol；
- toolset schema version；
- runtime contract digest；
- prompt digest；
- evaluation interface digest；
- delivery schema version；
- task ID / snapshot ID；
- delivery manifest；
- suite manifest；
- reference trajectory digests。

建议新 delivery 命名：

```text
task_packages/deliveries/
└── bsm_market_implied_metric_suite_v1/
    └── 20260814_metric_6x4_db_query_v3/
```

或者，如果 package family 版本也需要明确表达 breaking change：

```text
bsm_market_implied_metric_suite_v2/
└── 20260814_metric_6x4_db_query_v1/
```

二者只能选择一种一致的版本策略，不要同时让目录 family version、tool protocol version 和 task version 表达互相冲突的信息。

历史目录：

```text
20260813_prompt_v2_100
20260814_metric_6x4_unique_db
```

继续作为 immutable legacy deliveries 保留。

---

## 9. 测试迁移计划

### 9.1 SQL tool positive tests

至少覆盖：

- 查询 `information_schema`；
- `SHOW TABLES`；
- `DESCRIBE` public relation；
- 查询 public metadata；
- 查询 8 条 underlying rows；
- 查询 160 条 option rows；
- public table join；
- filter、projection、aggregation、`ORDER BY`；
- DATE / DECIMAL / DOUBLE serialization；
- query call accounting；
- query result row/byte limits；
- submission exactly once。

### 9.2 SQL tool negative tests

至少拒绝：

- empty SQL；
- multiple statements；
- write statement；
- `ATTACH`；
- `COPY`；
- `INSTALL` / `LOAD`；
- external read functions；
- filesystem path；
- network URL；
- non-allowlist schema；
- query budget overflow；
- oversized result；
- query after submission（如果 contract 明确禁止）；
- duplicate submission。

### 9.3 Packaging tests

更新以下现有测试组：

```text
tests/packaging_analytic_and_implied_greeks_iv/
```

重点包括：

- `test_portable_delivery.py`
- `test_portable_metric_suite.py`
- `test_bsm_greeks_database_and_replay.py`
- `test_bsm_greeks_package_contract.py`
- `test_bsm_greeks_prompt_runtime_drift.py`
- `test_bsm_greeks_release_views.py`
- `test_metric_database.py`
- `test_metric_specs.py`
- negative submission tests

新增断言：

- 新 task tree 不包含 `payloads/`；
- toolset 不包含 `payload_path`；
- toolset 绑定正确的 database digest；
- public database relation allowlist 未改变；
- Prompt 不出现实际 table/schema/join-key 信息；
- Prompt 不出现 legacy query tool names；
- runtime contract 与 toolset call budget 一致；
- database market content 与迁移前一致；
- reference answer 与迁移前一致。

### 9.4 Legacy compatibility tests

保留至少一个 v2 fixture，确认：

- v2 static-json delivery 仍能通过旧 validator/replay；
- v3 validator 不误接受 v2 toolset；
- v2 validator 不误接受 v3 toolset；
- 两个协议的 schema version dispatch 明确。

不要为了 v3 简化而让历史 delivery 失去可重放性。

### 9.5 Prompt leakage tests

新 Prompt 应断言不包含：

```text
solver_visible
underlying_market_inputs
option_quote_inputs
query_greeks_underlying_market_v2
query_greeks_option_quotes_v2
```

还应检查：

- 不包含固定 8/160 row-count 指令；
- 不包含 join key 列表；
- 不包含 SQL solution；
- 不包含 formulas；
- 不包含 private/generated/latent answer fields；
- 必须包含 target、units、method、canonicalization、query tool 和 submit tool。

---

## 10. 实施顺序

### Phase 0：冻结旧版

1. 将当前 v2 delivery 标记为 legacy/immutable；
2. 保存当前 suite manifest、task manifests 和 smoke-test result；
3. 不修改现有生成目录中的单个 task 文件。

### Phase 1：冻结 v3 contracts

先确定：

- query tool name；
- submit tool name；
- max query calls；
- allowed SQL statement types；
- response serialization；
- row/byte/time/memory limits；
- row-order semantics；
- public submission schema 是否升级；
- family/task/toolset versioning strategy。

以上 contract 未冻结前，不开始批量 delivery regeneration。

### Phase 2：实现 tool host

1. 新增 v3 restricted SQL executor；
2. 实现 schema/relation/function allowlist；
3. 实现 deterministic response serialization；
4. 实现 resource accounting；
5. 完成 positive/negative unit tests。

### Phase 3：改 packaging

1. 修改 toolset generation；
2. 修改 portable delivery allowlist；
3. 修改 metric suite leaf generation；
4. 修改 manifest/digest generation；
5. 删除新版本 JSON payload materialization；
6. 保留 legacy validator dispatch。

### Phase 4：改 Prompt、schema 和 reference solver

1. 更新 Prompt renderer；
2. 更新 runtime contract；
3. 简化 public submission schema；
4. 更新 reference solver 和 trajectory；
5. 增加 prompt leakage/drift tests。

### Phase 5：本地 smoke

至少运行：

- 一个 IV task；
- 一个 Greek task；
- SQL negative/security suite；
- portable replay；
- verifier exactness tests。

更稳妥的 smoke 是六个 targets 各选一个 task。

### Phase 6：全量重新 packaging

从现有 frozen source packages / run summary 重新生成：

```text
6 metrics × 4 source assignments = 24 tasks
```

本阶段不重新运行 underlying/option market generator。

### Phase 7：最终验收

1. 校验 suite manifest；
2. 校验全部 24 个 leaf manifests；
3. 校验每个 task database digest/logical checksum；
4. 校验没有 JSON payload；
5. 校验无 private leakage；
6. 校验全部 reference trajectories 可重放；
7. 校验全部 pytest；
8. 对至少一个真实 LLM rollout 检查 schema discovery 行为。

---

## 11. Definition of Done

只有同时满足以下条件，本次迁移才算完成：

- [ ] v2 deliveries 未被原地修改；
- [ ] v3 每个 task 只有一个 market-input source of truth：`task.duckdb`；
- [ ] v3 不包含 `options.json`；
- [ ] v3 不包含 `underlyings.json`；
- [ ] v3 toolset 不包含 `payload_path`；
- [ ] LLM 能通过 trusted SQL tool 查询 schema 和 public rows；
- [ ] LLM 不能直接打开 DuckDB 文件；
- [ ] SQL write/external/private access 均被拒绝；
- [ ] Prompt 不公开 table、column、join key 和 row counts；
- [ ] Prompt 完整公开数学模型、数值方法、单位和输出语义；
- [ ] submission schema 不枚举全部 database row identities；
- [ ] verifier 按 `row_id` 对齐，与 row order 无关；
- [ ] v3 reference output 与同一 market content 的 v2 output 一致；
- [ ] 六个 metric targets 均通过 smoke/replay/verifier；
- [ ] 全部 24 个新 task 通过 package validation；
- [ ] legacy v2 replay 仍然可用；
- [ ] 无 answer、formula、latent parameter 或 private artifact leakage。

---

## 12. Codex 执行边界

交给 Codex 实施时，应明确：

1. 只在 `synthetic-BSM-agent-task` 分支工作；
2. 不手工编辑 24/100 个 generated task leaf；
3. 先修改 source packaging/runtime/tests，再统一 regeneration；
4. 不重新生成 frozen market data；
5. 不删除或覆盖历史 v2 deliveries；
6. 不把公式或答案写入 Prompt、JSON schema、DuckDB metadata 或 toolset；
7. 不通过简单 regex-only SQL filter 声称完成安全隔离；
8. 不改变 metric units、method IDs 或 numeric canonicalization，除非单独提出 breaking-contract 变更；
9. 如果 submit tool version、row-order contract 或 public schema version 尚未决定，先停下来询问，不得自行猜测；
10. 完成后先提交文件变更清单、测试结果和 regenerated delivery summary，再决定是否 commit/push。

---

## 13. 预期最终结果

迁移完成后，这批任务将从：

```text
固定两个 JSON payload query
+ 手搓 BSM/IV/Greeks
```

升级为：

```text
DuckDB schema discovery
+ SQL retrieval/join
+ 手搓 BSM/IV/Greeks
+ strict structured submission
+ private hard verifier
```

这样新增的难度来自真实 agent 能力：数据库探索、查询规划和数据连接，而不是来自模糊的金融单位或未声明的数学约定。
