"""LangGraph 图节点与路由（纯业务逻辑，可离线测试）。

从 agent_graph 拆出；图构建与对外门面在 core/agent_graph.py。
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from langchain_core.messages import HumanMessage

from core.agent_state import AgentState
from core.analyzer import Analyzer
from core.clarify import MAX_CLARIFY, merge_policy, merge_question
from core.intent import detect_intent
from core.llm import invoke_text
from core.masker import mask_analysis
from core.runtime import get_strictness
from core.sql_gen import NeedClarificationError, SQLGenerator, SQLValidationError
from core.table_registry import TableRegistry
from core.table_selector import TableSelector
from interfaces import get_datasource

logger = logging.getLogger(__name__)


# ==================== 节点 ====================

def _intent_node(state: AgentState) -> dict:
    """意图识别（含澄清上下文的合并/清除）。

    每轮入口：复位瞬时信号 need_clarify/empty_result；若有上一轮的待澄清上下文，
    按其回答判定 merge（并入原问题继续）或 reset（切新话题、清 pending、归零计数）。
    """
    text = state["user_input"]
    pending = state.get("pending_original")

    effective = text
    updates: dict = {
        "user_input": effective,
        "intent": "",
        "force_chart": False,
        "requested_chart_type": "",
        "need_clarify": False,
        "empty_result": False,
        "sql": "",
        "query_result": None,
        "analysis": None,
        "reply": "",
        "error": "",
        "messages": [HumanMessage(content=text)],
    }
    if pending:
        if merge_policy(pending, text) == "merge":
            effective = merge_question(pending, text)
            updates["user_input"] = effective
        else:
            updates["user_input"] = text
            updates["clarify_count"] = 0
        updates["pending_original"] = None
        updates["pending_clarification"] = None

    intent, force_chart, chart_type = detect_intent(effective)
    # "表名 + 是什么/干嘛/做什么" → 视为单表用途查询，走 meta_node 的 AI 推测（优先于 chat/meta 判定）
    if _resolve_table(effective):
        intent = "meta"
    updates["intent"] = intent
    updates["force_chart"] = force_chart
    updates["requested_chart_type"] = chart_type or ""
    return updates


def _table_node(state: AgentState) -> dict:
    """选表：三规则判定 + LLM 精挑，决定本轮使用的表（active_tables）。

    选表失败/拿不准时不再澄清，交由 sql_node 全表兜底生成（查询尽量执行）；异常不阻断。
    """
    try:
        result = TableSelector().decide(
            state["user_input"], state.get("active_tables", [])
        )
        return {"active_tables": result.tables}
    except Exception as e:  # noqa: BLE001 —— 选表失败不阻断，走全表兜底
        logger.warning("选表失败: %s", e)
        return {}


def _review_mode() -> bool:
    """审核模式：配置 review_mode=true 强制开启，或当前严谨度为 strict 时自动开启。"""
    try:
        from core.config import settings

        forced = bool(settings().get("nl2sql", {}).get("review_mode", False))
    except Exception:  # noqa: BLE001
        forced = False
    return forced or get_strictness() == "strict"


_LIMIT_RE = re.compile(r"\blimit\s+\d+\b", re.IGNORECASE)

# SQL 执行失败时最多重试次数（原始 1 次 + 修正重试 N-1 次）
MAX_EXECUTE_RETRY = 2


def _explain_sql(question: str, sql: str) -> str:
    """用一句大白话向不懂 SQL 的用户解释查询含义。"""
    prompt = (
        "你是数据分析助手。请用一句简体中文、面向不懂 SQL 的用户，解释下面这条 SQL 查询会返回什么数据"
        "（说明统计口径、分组维度、筛选条件即可）。不要提 SQL 术语，不要输出多余内容。\n"
        f"用户问题：{question}\nSQL：{sql}"
    )
    try:
        return invoke_text(prompt).strip()
    except Exception:  # noqa: BLE001
        return ""


def _limit_sql(sql: str, n: int) -> str:
    """给 SQL 追加 LIMIT n（若已有限制则不重复）。"""
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        return s
    if _LIMIT_RE.search(s):
        return s
    return f"{s} LIMIT {n}"


def _preview_sql(sql: str, limit: int = 5) -> dict:
    """执行预览：取前 limit 行样本数据；失败返回空结构不阻断。"""
    preview_sql = _limit_sql(sql, limit)
    if not preview_sql:
        return {"columns": [], "rows": [], "row_count": 0}
    try:
        result = get_datasource().execute_query(preview_sql)
        return {"columns": result.columns, "rows": result.rows, "row_count": result.row_count}
    except Exception:  # noqa: BLE001
        return {"columns": [], "rows": [], "row_count": 0}


def _sql_node(state: AgentState) -> dict:
    try:
        sql = SQLGenerator().generate(
            state["user_input"], tables=state.get("active_tables") or None
        )
        if _review_mode():
            # 审核模式：生成后不执行，附大白话解读 + 数据预览
            explanation = _explain_sql(state["user_input"], sql)
            preview = _preview_sql(sql)
            return {
                "sql": sql,
                "needs_review": True,
                "error": "",
                "sql_explanation": explanation,
                "sql_preview": preview,
                "reply": "已生成 SQL，请审核后执行。",
            }
        return {"sql": sql, "error": ""}
    except NeedClarificationError as e:
        # 必须先于 SQLValidationError 捕获
        return {
            "need_clarify": True,
            "clarify_kind": "llm",
            "pending_original": state["user_input"],
            "pending_clarification": e.clarify_question,
            "sql": "",
            "error": "",
        }
    except SQLValidationError as e:
        return {"sql": "", "error": str(e), "reply": f"抱歉，未能生成安全的查询：{e}"}


def _execute_node(state: AgentState) -> dict:
    if state.get("error") or state.get("needs_review"):
        return {}
    ds = get_datasource()
    sql = state["sql"]
    question = state["user_input"]
    last_error = ""
    for _ in range(MAX_EXECUTE_RETRY):
        try:
            result = ds.execute_query(sql)
            return {"query_result": result, "empty_result": not result.rows, "sql": sql}
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            logger.warning("查询执行失败，尝试修正重试: %s", e)
            fixed = SQLGenerator().fix_sql(question, sql, last_error)
            if not fixed or fixed == sql:
                break
            sql = fixed
    return {
        "query_result": None,
        "error": last_error,
        "reply": f"查询执行失败：{last_error}",
        "sql": sql,
    }


def _analyze_node(state: AgentState) -> dict:
    if state.get("error") or not state.get("query_result"):
        return {}
    an = Analyzer().analyze(
        state["query_result"],
        state["user_input"],
        state.get("force_chart", False),
        state.get("requested_chart_type") or None,
    )
    return {"analysis": mask_analysis(an)}


def _chat_node(state: AgentState) -> dict:
    """非数据查询问题：不闲聊，统一返回能力引导提示。"""
    return {
        "reply": (
            "我是数据查询分析助手，只支持数据相关的问题，无法闲聊。\n"
            "你可以这样问我：\n"
            "· 有哪些表？\n"
            "· 各部门有多少员工？\n"
            "· 平均薪资是多少？\n"
            "· 员工的性别分布"
        )
    }


_TABLE_PROBE_WORDS = ("什么表", "是什么", "干嘛", "干什么", "用途", "啥表", "哪张表", "介绍", "作用", "做什么", "干嘛的", "干什么的")


def _resolve_table(text: str, registry: TableRegistry | None = None) -> str | None:
    """解析问题是否指向唯一一张具体表（需含表说明词才触发）。

    优先表名/别名直接命中（高置信，如"staff是什么"）；否则用关键词/注释 bigram 兜底，
    均要求唯一命中才返回。
    """
    t = text or ""
    if not any(w in t for w in _TABLE_PROBE_WORDS):
        return None
    registry = registry or TableRegistry()
    meta = registry.get_meta(None)
    direct = [
        name
        for name, m in meta.items()
        if name in t or any(a and a in t for a in m.get("aliases", []) or [])
    ]
    if len(direct) == 1:
        return direct[0]
    hits = registry.match(t)
    return hits[0] if len(hits) == 1 else None


def _table_purpose_via_llm(table: str, cols: list[dict]) -> str:
    """用 LLM 根据表名与字段推测该表用途。"""
    schema = ", ".join(
        f"{c.get('name')}({c.get('type')}{',' + c.get('comment') if c.get('comment') else ''})"
        for c in cols[:20]
    )
    prompt = (
        "你是数据库表语义分析专家。请根据表名与字段，用 1~2 句简体中文说明这张表存储什么数据、"
        "用于什么业务场景。只依据给定字段推断，不要编造额外信息。\n"
        f"表名：{table}\n字段：{schema}"
    )
    try:
        return invoke_text(prompt)
    except Exception:  # noqa: BLE001
        return "（无法自动推断，请查看下方字段说明）"


def _describe_table(table: str) -> dict:
    """针对单张表：AI 推测用途 + 字段说明。"""
    ds = get_datasource()
    try:
        cols = ds.get_schema(table)
    except Exception:  # noqa: BLE001
        cols = []
    desc = _table_purpose_via_llm(table, cols)
    rows = [[c.get("name", ""), c.get("type", ""), c.get("comment") or ""] for c in cols]
    display = {"columns": ["字段", "类型", "说明"], "rows": rows}
    reply = f"**{table}** 表：{desc}\n\n字段说明见下方表格。"
    return {
        "reply": reply,
        "analysis": {"columns": display["columns"], "rows": display["rows"], "chart": None},
    }


def _meta_node(state: AgentState) -> dict:
    """元数据查询：问具体某张表时返回 AI 推测用途；否则返回表清单与表结构。"""
    ds = get_datasource()
    try:
        target = _resolve_table(state["user_input"])
        if target:
            return _describe_table(target)
        tables = ds.get_tables()
        if not tables:
            return {"reply": "当前数据库中没有表。", "analysis": None}
        rows = []
        for t in tables:
            try:
                cols = ds.get_schema(t)
            except Exception:  # noqa: BLE001
                cols = []
            col_desc = "、".join(f"{c.get('name')}({c.get('type')})" for c in cols)
            rows.append([t, len(cols), col_desc])
        table = {"columns": ["表名", "字段数", "字段(类型)"], "rows": rows}
        reply = f"数据库中共有 **{len(tables)} 张表**：\n" + "\n".join(f"· {t}" for t in tables) + "\n\n详细字段见下方表格。"
        return {
            "reply": reply,
            "analysis": {"columns": table["columns"], "rows": table["rows"], "chart": None},
        }
    except Exception as e:  # noqa: BLE001
        return {"reply": f"查询表信息失败：{e}", "analysis": None}


_SUGGEST_RE = re.compile(r"(?:\n\s*)?建议[:：]\s*(.+)$", re.DOTALL)


def _split_suggestions(text: str) -> tuple[str, list[str]]:
    """从 LLM 回复中分离主回复与追问建议（以"建议："段结尾）。最多取 3 条。"""
    t = (text or "").strip()
    m = _SUGGEST_RE.search(t)
    if not m:
        return t, []
    reply = t[: m.start()].strip()
    raw = m.group(1).strip()
    parts = [p.strip() for p in re.split(r"[；;、\n]+", raw) if p.strip()]
    return reply, parts[:3]


def split_queries(question: str, max_split: int = 3) -> list[str]:
    """判断是否含多个独立查询并拆分；无则返回 [question]。"""
    q = (question or "").strip()
    if not q:
        return [q]
    prompt = (
        "判断下面这个数据查询问题是否包含多个独立的查询请求。\n"
        "如果包含多个（如用'并且/同时/分别/再统计/还要'等连接了多个查询），把它们拆成多个独立子问题，"
        "每个子问题必须是一个完整、可单独执行的数据问题（保留主语、维度、口径，不省略）。\n"
        "如果只包含一个查询，只输出一行：ORIGINAL\n"
        "输出规则：每个子问题占一行；不要序号、不要解释、不要 markdown；最多输出 3 行。\n"
        f"问题：{q}"
    )
    try:
        content = invoke_text(prompt)
    except Exception:  # noqa: BLE001
        return [q]
    lines = []
    for raw in (content or "").splitlines():
        line = raw.strip().lstrip("-_•*0123456789.、)） ").strip()
        if not line or line.upper() == "ORIGINAL":
            continue
        if len(line) >= 2:
            lines.append(line)
    if len(lines) <= 1:
        return [q]
    return lines[:max_split]


def _generate_reply_prompt(question: str, analysis: dict) -> str:
    """构造"数据分析回复"的 prompt（供整段与流式生成共用）。"""
    rows = analysis.get("rows", [])
    columns = analysis.get("columns", [])
    chart = analysis.get("chart")
    table_desc = f"表格列：{columns}\n数据行（前20）：{rows[:20]}" if rows else "无数据"
    chart_desc = f"图表类型：{chart['type']}" if chart else ""
    return (
        "你是数据分析助手。请根据查询到的数据，用简体中文、简洁地回答用户问题，"
        "指出关键数字与结论；不要编造数据。\n"
        "回复末尾另起一行以「建议：」开头，给出 2~3 条针对当前结果可继续查询的建议"
        "（如按某维度钻取、换统计口径），用分号分隔；没有合适建议则省略该行。\n"
        f"用户问题：{question}\n{table_desc}\n{chart_desc}"
    )


def _generate_reply_via_llm(question: str, analysis: dict) -> str:
    return invoke_text([HumanMessage(content=_generate_reply_prompt(question, analysis))])


def _deny_node(state: AgentState) -> dict:
    """写操作明确拒绝：系统只提供只读查询。"""
    return {
        "reply": (
            "抱歉，我只能提供数据**查询**（只读）服务，"
            "不支持删除、修改、插入等写操作。\n"
            "你可以问我查询类问题，例如：\n"
            "· 有哪些表？\n"
            "· 各部门有多少员工？\n"
            "· 平均薪资是多少？"
        )
    }


def _clarify_node(state: AgentState) -> dict:
    """模糊查询：向用户反问澄清；连续超过上限则停止并引导换问法。

    不清除 pending（保留到下一轮供合并），不更新 active_tables（合并轮重新选表）。
    """
    count = state.get("clarify_count", 0) + 1
    if count > MAX_CLARIFY:
        # 封顶：清空待澄清上下文并归零，避免后续短句提问被反复阻塞
        return {
            "reply": "我仍缺少足够的信息来确定查询条件，建议换一个更具体的问法。",
            "clarify_count": 0,
            "pending_original": None,
            "pending_clarification": None,
        }
    kind = state.get("clarify_kind", "llm")
    if kind == "table":
        try:
            tables = TableRegistry().tables()
        except Exception:  # noqa: BLE001
            tables = []
        if tables:
            reply = (
                "你的问题描述得不够具体，我没法确定你想查哪张表。\n"
                f"当前可用表：{'、'.join(tables)}\n"
                "例如：告诉我「查销售订单表的销售额」即可。"
            )
        else:
            reply = "当前数据库中没有可用的表，请检查数据源配置。"
    else:
        q = state.get("pending_clarification") or "请补充说明统计口径或范围。"
        reply = f"{q}\n（补充信息后我会继续为你查询。）"
    return {"reply": reply, "clarify_count": count}


# ==================== 路由 ====================

def _route_after_intent(
    state: AgentState,
) -> Literal["chat_node", "meta_node", "table_node", "deny_node"]:
    intent = state["intent"]
    if intent == "deny":
        return "deny_node"
    if intent == "chat":
        return "chat_node"
    if intent == "meta":
        return "meta_node"
    return "table_node"


def _route_after_table(
    state: AgentState,
) -> Literal["clarify_node", "sql_node"]:
    return "clarify_node" if state.get("need_clarify") else "sql_node"


def _route_after_sql(
    state: AgentState,
) -> Literal["clarify_node", "execute_node"]:
    return "clarify_node" if state.get("need_clarify") else "execute_node"
