# 📊 ChatDataAgent · 数据问数 Agent

![Python](https://img.shields.io/badge/Python-3.13-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-orange)
![LLM](https://img.shields.io/badge/LLM-DeepSeek-4d6bfe)

基于 **LangGraph + DeepSeek + 真实 MySQL** 的自然语言数据问数系统——用户用中文提问即可自动完成 **选表 → 生成 SQL → 查询 → 表格 + 图表**，并支持多轮追问、SSE 流式输出、SQL 审核与工程化安全。

> ⚠️ 本项目**必须连接真实 MySQL 数据库**并配置 **DeepSeek API Key**，不提供任何模拟数据源。

## ✨ 核心能力

- 🗣️ **自然语言查数据（NL2SQL）**：提问 → DeepSeek 根据真实表结构生成 SQL → 查 MySQL，执行失败自动反馈 LLM 修正重试
- 🎯 **多级选表（表多不慌）**：关键词粗筛 → bge embedding 语义召回 → LLM 精挑；相似度达阈值直接采用、跳过 LLM 调用（成本控制）
- 🧠 **多轮对话 + 表上下文继承**：LangGraph checkpointer 持久化会话；追问自动沿用上轮表，显式提新表时切换
- 💬 **SSE 流式输出**：回复生成移出状态机，逐字推送打字机效果，前端失败自动回退非流式
- 🧭 **表语义预加载**：LLM 批量推断表用途摘要 + 本地 bge 向量检索，解决表注释为空时的模糊查询
- ⚖️ **澄清机制 + 严谨度可调**：口径模糊时反问（计数封顶防死循环）；严谨度 relaxed/strict 作为运行时参数前端可切换，严谨档自动开启 SQL 审核
- 📋 **SQL 审核（human-in-the-loop）**：生成后附大白话解读 + 数据预览，人工确认才执行
- 🔐 **安全与工程化**：只读白名单 + 表权限 + 查询审计 + PII 脱敏 + 结果缓存（重复查询 ~5.8s → 几十 ms）+ 网关限流 + MySQL 超时/行数上限
- 🚫 **无闲聊**：只支持数据查询，其他问题统一引导

## 🚀 快速开始（克隆后）

### 环境要求
- Python 3.13（conda 环境 `langchain1.2`）+ Node.js 24 + MySQL + DeepSeek API Key
- 依赖：`pip install -r requirements.txt`（含 `fastembed` 用于 embedding）

### 1. 克隆并配置
```bash
git clone <你的仓库地址>
cd ChatDataAgent

# 配置 DeepSeek Key（必须）
cp config/.env.example config/.env        # 填入 DEEPSEEK_API_KEY

# 配置 MySQL（必须）
cp config/settings.example.json config/settings.json
# 在 settings.json 的 datasource.mysql 填 host/port/user/password/database，enabled 设为 true
```

### 2. 启动（前后端分离，三个终端）
```bash
# ① Python 核心（必须）
conda activate langchain1.2
python web/app.py                        # :5003

# ② Node 网关（对外统一入口 :3000）
cd server && npm install && npm start

# ③ React 前端（开发模式 :5173）
cd ui && npm install && npm run dev
# 浏览器打开 http://localhost:5173
# 生产：cd ui && npm run build → 直接访问 http://localhost:3000
# 也可仅 CLI：python main.py
```

### 3. 提问试试
```
有哪些表？
各部门有多少员工？
员工的性别分布（饼图）
staff 是什么表
全部年龄是怎么分布的
```

## ⚙️ 可选配置

`config/settings.json`（全部可选，不配用默认）：
- `datasource.mysql.table_aliases`：表名 → 业务别名，让关键词选表更准
- `nl2sql.allow_tables`：表权限白名单（空 = 不限制，白名单外拒绝查询）
- `nl2sql.threshold`：embedding 相似度阈值（默认 0.55，≥ 阈值直用 top-3 跳过 LLM）
- `nl2sql.embedding`：`enabled`/`backend`(fastembed/st)/`model`/`summary_enabled` 等；未装 fastembed 自动降级
- `nl2sql.embedding.threshold` 与 `nl2sql.threshold` 同源（选表成本控制）
- 严谨度：前端顶栏切换 relaxed/strict（严格档 = 口径先反问 + SQL 自动审核）
- 环境变量可覆盖敏感配置：`DEEPSEEK_API_KEY`、`MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`（不落盘明文）

## 🧱 技术栈

| 组件 | 方案 |
|------|------|
| 编排 | LangGraph（状态机 + 多轮记忆 checkpointer） |
| LLM | DeepSeek（`deepseek-chat`，必须配置 Key） |
| 数据库 | MySQL（pymysql，业务数据）+ SQLite（会话/审计/摘要缓存） |
| 数据分析 | pandas（DataFrame：数值化 / 图表截取） |
| 向量检索 | FastEmbed（bge-small-zh，进程内存余弦，无向量库） |
| 网关 | Node.js + Express（:3000，限流 + SSE pipe + 静态托管） |
| 前端 | React + Vite（:5173）+ ECharts（按需引入 + 分包） |

## 📁 项目结构

```
ChatDataAgent/
├── config/            settings.json + .env（真实配置，已被 .gitignore 排除）
│                       settings.example.json / .env.example（占位示例）
├── interfaces/        ★ 数据源抽象接口 + 工厂
├── impl/real/         MySQL 真实数据源（pymysql，只读 + 防注入 + 自纠错）
├── core/
│   ├── agent_graph.py LangGraph 图构建 + 对外门面（chat / chat_stream / 多查询拆分）
│   ├── graph_nodes.py 意图 / 选表 / SQL / 执行 / 分析 / 澄清 等节点与路由
│   ├── table_selector.py 三级选表：三规则 + embedding 混合 + LLM 精挑
│   ├── table_semantic.py LLM 表摘要 + bge 向量召回 + 预热
│   ├── sql_gen.py     NL2SQL 生成 + 只读校验 + 错误自纠错
│   ├── analyzer.py    pandas 分析 + ECharts 图表数据
│   ├── clarify.py     澄清合并策略（确认词 / 封顶防死循环）
│   ├── runtime.py     严谨度等运行时参数
│   ├── cache.py       结果缓存（进程内 TTL）
│   ├── conversations.py 服务端会话（SQLite）
│   ├── audit.py       查询审计（SQLite）
│   ├── masker.py      PII 敏感列脱敏
│   └── ...
├── web/               Flask 核心 API（:5003）
├── server/            Node.js 网关（:3000）
├── ui/                React + Vite 前端
├── tests/             验收测试（真环境）
└── main.py            CLI 入口
```

更多架构说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 🔒 安全设计

- **只读 SQL 校验**：必须 SELECT、拦截危险关键词、拦截多语句注入
- **表权限白名单**：`nl2sql.allow_tables` 白名单外拒绝
- **连接会话只读**：MySQL `SET SESSION transaction_read_only=1`，数据库物理层拒绝任何写
- **写操作拒绝**："删除/修改/插入"等意图直接拒绝
- **SQL 审核（human-in-the-loop）**：严谨档生成后人工确认才执行
- **防幻觉**：元数据直接查库；选表只在真实表里挑；执行失败用真实错误反馈模型修正
- **查询审计 + PII 脱敏**：每次查询落底账；手机/身份证/邮箱等敏感列自动打码

## 🧪 测试

```bash
python tests/test_dialogs.py    # 真环境验收（需 MySQL + DeepSeek）
```

## 💡 提问示例

- 有哪些表？
- 各部门有多少员工？
- 男女比例饼状图
- 各部门人数柱状图
- 平均薪资是多少？
- 各部门有多少员工，并且按性别统计一下（多查询拆分）

## 📄 License

[MIT](LICENSE) © 2026 曹宇阳
