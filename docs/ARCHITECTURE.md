# ChatDataAgent 架构文档

基于 LangGraph + DeepSeek + MySQL 的自然语言数据问数 Agent：中文提问 → 自动选表并生成 SQL 查询 → 返回表格 + 图表。多轮对话、模糊澄清、表语义 embedding 预筛。

## 1. 分层总览

```
┌─ 入口层 ──────────────┐
│  main.py (CLI)        │   web/app.py (Flask :5003) + index.html
└──────────┬────────────┘
           ▼ chat(text, thread_id)
┌─ 编排层 (core) ───────┐
│  agent_graph.py 图构建/门面   graph_nodes.py 节点/路由
│  agent_state.py 状态/DTO     sql_gen.py  NL2SQL+校验
│  table_selector.py 选表      table_semantic.py 语义/embedding
│  table_registry.py 表注册表   table_semantic → recall
│  intent.py 意图识别  clarify.py 澄清  analyzer.py 分析
└──────────┬────────────┘
           ▼ get_datasource()
┌─ 契约/实现层 ─────────┐
│  interfaces/datasource.py (抽象)  ←  impl/real/datasource.py (MySQL)
└──────────────────────┘
```

依赖方向：`interfaces`（契约）→ `impl`（实现）；`core` 内 `constants/llm/intent/config` 为低层，`table_*`/`sql_gen`/`clarify`/`analyzer` 为业务层，`graph_nodes` 聚合，`agent_graph` 仅做图构建与门面。**无循环依赖**（低层模块不反向依赖高层）。

## 2. 模块清单（重构后）

| 模块 | 行数 | 职责 | 关键符号 |
|---|---|---|---|
| `core/constants.py` | 5 | 跨模块共享常量 | `MAX_FOLLOWUP_LEN=20` |
| `core/config.py` | 24 | 加载 settings.json + .env | `settings()`、`DEEPSEEK_*` |
| `core/llm.py` | 35 | LLM 工厂 + 文本助手 | `get_llm()`、`invoke_text()` |
| `core/intent.py` | 47 | 关键词意图识别 | `detect_intent()` |
| `core/agent_state.py` | 52 | 状态 TypedDict + 输出 DTO | `AgentState`、`ChatOutcome` |
| `core/graph_nodes.py` | ~240 | 11 节点 + 3 路由 + 回复生成 | `_intent_node`…`_route_after_sql` |
| `core/agent_graph.py` | ~95 | 图构建 + checkpointer + chat 门面 | `build_graph()`、`chat()` |
| `core/table_registry.py` | 117 | 表元数据缓存 + 关键词粗筛 | `TableRegistry`、`CANDIDATE_LIMIT` |
| `core/table_selector.py` | ~180 | 三规则选表 + LLM + embedding 混合 | `TableSelector`、`Decision`、`SelectionResult` |
| `core/table_semantic.py` | ~310 | LLM 摘要 + 本地 embedding + 内存检索 + 预热 | `recall()`、`ensure_summaries()`、`warm_up()` |
| `core/sql_gen.py` | 136 | NL2SQL 生成 + 只读校验 + 补表重试 | `SQLGenerator`、`SQLValidationError`、`NeedClarificationError` |
| `core/clarify.py` | 30 | 澄清纯逻辑 | `extract_clarify()`、`merge_policy()`、`merge_question()` |
| `core/analyzer.py` | 77 | 结果整理为表格 + ECharts 图表 | `Analyzer` |
| `interfaces/datasource.py` | 30 | 数据源抽象 | `DataSource`(ABC)、`QueryResult` |
| `interfaces/__init__.py` | 11 | 统一出口 | `get_datasource()` |
| `impl/real/datasource.py` | 109 | MySQL 实现（pymysql + information_schema） | `MySQLDataSource` |

## 3. 核心调用链

```
chat(text, thread_id)  →  _graph.invoke(state, config)   [LangGraph]
intent_node      _intent_node    意图识别 + 澄清上下文合并/复位（need_clarify/empty_result）
                 └ _route_after_intent → chat / meta / deny / table_node
table_node       _table_node     TableSelector.decide(question, active_tables)
                 ├ 显式命中(关键词)  → select_topk (LLM 精挑 top-3)
                 ├ 兜底             → 混合策略: embedding 召回 → 高置信直用 / 低置信 LLM
                 └ _route_after_table → clarify_node / sql_node
sql_node         _sql_node       SQLGenerator.generate → 只读校验
                 │  LLM 输出 CLARIFY: → NeedClarificationError → 澄清分支
                 │  引用未提供表     → 补表重试
                 └ _route_after_sql → clarify_node / execute_node
execute_node     _execute_node   get_datasource().execute_query(SQL)
analyze_node     _analyze_node   Analyzer → {columns, rows, chart}
generate_node    _generate_node  LLM 生成中文回复 + 空结果提示
clarify_node     _clarify_node   表不确定 → 附表清单反问；LLM 标疑 → 复述问题；超限终止
```

## 4. 关键状态字段（AgentState）

- `active_tables`：当前会话使用的表（跨轮继承/切换）
- `pending_original` / `pending_clarification` / `clarify_count` / `clarify_kind`：澄清机制——本轮反问、下轮合并原问题继续；`clarify_count` 封顶防死循环
- `need_clarify` / `empty_result`：瞬时信号，每轮由 `_intent_node` 复位
- `messages`：对话历史（`add_messages` reducer）

## 5. 数据流与降级

- **选表**：关键词粗筛（表名/别名/表注释 bigram）→ LLM 精挑；表注释为空时由 `table_semantic` 的 LLM 摘要 + embedding 召回兜底
- **降级链**：模型不可用 → `recall` 返回 None → 走原 LLM 精挑；LLM 无 Key → 图入口抛错
- **只读安全**：意图层写词拦截 → `sql_gen.validate` SELECT 白名单 + 危险词 + 多语句拒绝

## 6. 配置（config/settings.json）

| 配置段 | 说明 |
|---|---|
| `datasource.mysql` | host/port/user/password/database，`enabled` 开关，`table_aliases` 可选 |
| `nl2sql.embedding` | `enabled`、`backend`(fastembed/st)、`model`、`threshold`(0.55)、`top_k`(5)、`summary_enabled`、`warmup_on_start` |
| `.env` | `DEEPSEEK_API_KEY`/`BASE_URL`/`MODEL`（必填） |

## 7. 测试覆盖矩阵

| 测试文件 | 覆盖 | 前提 |
|---|---|---|
| `tests/test_dialogs.py`（18 条） | 只读校验、真实 MySQL、图表、端到端、多轮继承 | 真实 MySQL + DeepSeek |

## 8. 已知建议（未实施）

- `table_semantic.py`（~310 行）仍偏大，可再拆为 `semantic_model` / `semantic_summary` / `semantic_retrieval`
- 配置读取分散在各模块 `_cfg`，可收敛为 `core/config.py` 的类型化 getter
- 测试是独立脚本（非 pytest），可迁移到 pytest + conftest 复用测试桩
- `intent` 单字词 `"图"` / `"最"` 判定宽泛，属规则精度隐患
- `interfaces.get_datasource()` 每次 new 实例，可考虑进程级单例避免连接堆积
