"""LangGraph 数据问答 Agent 状态机（图构建 + 对外门面）。

流程：用户提问 → 意图识别 → [闲聊直接答 | NL2SQL → 查库 → 分析 → 生成回复]
多轮记忆通过 checkpointer 持久化（SqliteSaver，回退内存）。
节点/路由实现在 core.graph_nodes，状态/DTO 在 core.agent_state。
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from core.agent_state import AgentState, ChatOutcome
from core.graph_nodes import (
    _analyze_node,
    _chat_node,
    _clarify_node,
    _deny_node,
    _execute_node,
    _intent_node,
    _meta_node,
    _route_after_intent,
    _route_after_sql,
    _route_after_table,
    _sql_node,
    _table_node,
    split_queries,
)
from core.intent import detect_intent

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("intent_node", _intent_node)
    g.add_node("chat_node", _chat_node)
    g.add_node("meta_node", _meta_node)
    g.add_node("deny_node", _deny_node)
    g.add_node("table_node", _table_node)
    g.add_node("sql_node", _sql_node)
    g.add_node("execute_node", _execute_node)
    g.add_node("analyze_node", _analyze_node)
    g.add_node("clarify_node", _clarify_node)

    g.add_edge(START, "intent_node")
    g.add_conditional_edges("intent_node", _route_after_intent)
    g.add_edge("chat_node", END)
    g.add_edge("meta_node", END)
    g.add_edge("deny_node", END)
    g.add_conditional_edges("table_node", _route_after_table)
    g.add_conditional_edges("sql_node", _route_after_sql)
    g.add_edge("clarify_node", END)
    g.add_edge("execute_node", "analyze_node")
    g.add_edge("analyze_node", END)
    return g.compile(checkpointer=_make_checkpointer())


def _make_checkpointer():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(STORAGE_DIR / "agent.sqlite3"), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception:  # noqa: BLE001 —— 回退内存 checkpointer
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()


_graph = build_graph()


def _new_thread_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _cache_ttl() -> int:
    try:
        from core.config import settings

        return int(settings().get("nl2sql", {}).get("cache_ttl", 300) or 0)
    except Exception:  # noqa: BLE001
        return 300


def _write_audit(question: str, outcome: ChatOutcome, duration_ms: int) -> None:
    """写一条审计记录；失败静默不阻断。"""
    try:
        from core.audit import log_audit

        log_audit(
            thread_id=outcome.thread_id,
            question=question,
            intent=outcome.intent,
            sql=outcome.sql,
            rows=len(outcome.table["rows"]) if outcome.table else 0,
            chart=(outcome.chart or {}).get("type", "") if outcome.chart else "",
            needs_clarify=outcome.needs_clarify,
            error=outcome.error,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001
        pass


def _build_outcome(question: str, out: dict, tid: str) -> ChatOutcome:
    """从图输出构建 ChatOutcome；查询链路回复由门面生成，其他分支复用节点 reply。"""
    analysis = out.get("analysis") or {}
    intent = out.get("intent", "")
    has_rows = analysis.get("rows") is not None
    common = {
        "thread_id": tid,
        "intent": intent,
        "sql": out.get("sql", ""),
        "active_tables": list(out.get("active_tables", [])),
        "needs_clarify": bool(out.get("need_clarify")),
        "clarification": out.get("pending_clarification") or "",
        "needs_review": bool(out.get("needs_review")),
        "sql_explanation": out.get("sql_explanation", ""),
        "sql_preview": out.get("sql_preview") or {},
    }
    table = (
        {"columns": analysis.get("columns", []), "rows": analysis.get("rows", [])}
        if has_rows and analysis.get("rows")
        else None
    )
    chart = analysis.get("chart")

    if out.get("error"):
        return ChatOutcome(
            reply=out.get("reply") or "查询过程中出现问题，请重试。",
            error=out.get("error", ""),
            table=table,
            **common,
        )
    if intent == "query_data" and has_rows:
        from core.graph_nodes import _generate_reply_via_llm, _split_suggestions

        text = _generate_reply_via_llm(question, analysis)
        reply, suggestions = _split_suggestions(text)
        if out.get("empty_result"):
            reply += "\n\n（提示：未查到符合条件的数据，可尝试更换关键词或表名。）"
        return ChatOutcome(
            reply=reply,
            table=table,
            chart=chart,
            tables=[table] if table else [],
            charts=[chart] if chart else [],
            suggestions=suggestions,
            **common,
        )
    # meta / chat / deny / clarify：复用节点 reply，仍透传 analysis 表格/图表
    return ChatOutcome(
        reply=out.get("reply", ""),
        table=table,
        chart=chart,
        tables=[table] if table else [],
        charts=[chart] if chart else [],
        **common,
    )


def _merge_reply_prompt(question: str, outcomes: list[ChatOutcome]) -> str:
    """构造多查询"综合回复"的 prompt。"""
    parts = []
    for i, o in enumerate(outcomes, 1):
        rows = o.table.get("rows", []) if o.table else []
        cols = o.table.get("columns", []) if o.table else []
        err = f"，错误 {o.error}" if o.error else ""
        parts.append(f"子查询{i}：列 {cols}，数据行（前10）{rows[:10]}{err}")
    return (
        "你是数据分析助手。用户一次问了多个问题，请基于下面各子查询的结果，用简体中文简洁地综合回答，"
        "分点说明每个问题的结论；有子查询失败时如实说明。不要编造。\n"
        f"用户问题：{question}\n" + "\n".join(parts)
    )


def _merge_multi(question: str, outcomes: list[ChatOutcome], tid: str) -> ChatOutcome:
    """汇总多个子查询结果为综合 ChatOutcome（多表格/图表 + 综合回复）。"""
    tables = [o.table for o in outcomes if o.table]
    charts = [o.chart for o in outcomes if o.chart]
    sqls = [o.sql for o in outcomes if o.sql]
    active = list(dict.fromkeys(t for o in outcomes for t in o.active_tables))

    from core.graph_nodes import _split_suggestions
    from core.llm import invoke_text
    from langchain_core.messages import HumanMessage

    text = invoke_text([HumanMessage(content=_merge_reply_prompt(question, outcomes))])
    reply, suggestions = _split_suggestions(text)
    return ChatOutcome(
        reply=reply,
        thread_id=tid,
        intent="query_data",
        sql="; ".join(sqls),
        table=tables[0] if tables else None,
        chart=charts[0] if charts else None,
        tables=tables,
        charts=charts,
        active_tables=active,
        suggestions=suggestions,
    )


def execute_sql(question: str, sql: str, thread_id: str | None = None) -> ChatOutcome:
    """给定 SQL 直接执行并出结果（SQL 审核通过后调用，不经过选表/重新生成）。"""
    tid = thread_id or _new_thread_id()
    state = {
        "user_input": question,
        "sql": sql,
        "error": "",
        "empty_result": False,
        "query_result": None,
        "analysis": None,
        "intent": "query_data",
        "force_chart": True,
        "requested_chart_type": "",
        "active_tables": [],
        "need_clarify": False,
        "needs_review": False,
        "pending_clarification": None,
        "clarify_count": 0,
    }
    state = {**state, **_execute_node(state)}
    if not state.get("error"):
        state = {**state, **_analyze_node(state)}
    return _build_outcome(question, state, tid)


def chat_stream(question: str, thread_id: str | None = None):
    """SSE 流式问答：yield 事件 dict（meta / table / chart / reply / done）。"""
    if not question or not question.strip():
        yield {
            "type": "done",
            "thread_id": thread_id or "",
            "reply": "请输入您的问题。",
            "sql": "",
            "suggestions": [],
            "error": "",
        }
        return
    tid = thread_id or _new_thread_id()
    q = question.strip()

    # 多查询拆分（仅 query_data）：逐个执行并推多个 table/chart，最后流式综合回复
    subs = split_queries(q) if detect_intent(q)[0] == "query_data" else [q]
    if len(subs) > 1:
        yield {"type": "meta", "active_tables": []}
        outcomes = []
        for sq in subs:
            so = _graph.invoke(
                {"user_input": sq},
                config={"configurable": {"thread_id": tid}},
            )
            o = _build_outcome(sq, so, tid)
            outcomes.append(o)
            if o.table and o.table.get("rows"):
                yield {"type": "table", "table": o.table}
            if o.chart:
                yield {"type": "chart", "chart": o.chart}
        from core.graph_nodes import _split_suggestions
        from core.llm import get_llm
        from langchain_core.messages import HumanMessage

        prompt = _merge_reply_prompt(q, outcomes)
        reply = ""
        for chunk in get_llm().stream([HumanMessage(content=prompt)]):
            piece = chunk.content if hasattr(chunk, "content") else str(chunk)
            if piece:
                reply += piece
                yield {"type": "reply", "text": piece}
        clean, suggestions = _split_suggestions(reply)
        yield {
            "type": "done",
            "thread_id": tid,
            "reply": clean,
            "sql": "; ".join(o.sql for o in outcomes if o.sql),
            "suggestions": suggestions,
            "error": "",
            "sql_explanation": "",
            "sql_preview": {},
        }
        return

    out = _graph.invoke(
        {"user_input": q},
        config={"configurable": {"thread_id": tid}},
    )
    analysis = out.get("analysis") or {}
    intent = out.get("intent", "")
    sql = out.get("sql", "")
    yield {"type": "meta", "active_tables": list(out.get("active_tables", []))}

    if analysis.get("rows") is not None:
        if analysis.get("rows"):
            yield {
                "type": "table",
                "table": {"columns": analysis.get("columns", []), "rows": analysis.get("rows", [])},
            }
        if analysis.get("chart"):
            yield {"type": "chart", "chart": analysis.get("chart")}

    if out.get("error") or not (intent == "query_data" and analysis.get("rows") is not None):
        reply = out.get("reply") or "查询过程中出现问题，请重试。"
        yield {"type": "reply", "text": reply}
        yield {
            "type": "done",
            "thread_id": tid,
            "reply": reply,
            "sql": sql,
            "suggestions": [],
            "error": out.get("error", ""),
            "sql_explanation": out.get("sql_explanation", ""),
            "sql_preview": out.get("sql_preview") or {},
        }
        return

    from core.graph_nodes import _generate_reply_prompt, _split_suggestions
    from core.llm import get_llm
    from langchain_core.messages import HumanMessage

    prompt = _generate_reply_prompt(q, analysis)
    reply = ""
    for chunk in get_llm().stream([HumanMessage(content=prompt)]):
        piece = chunk.content if hasattr(chunk, "content") else str(chunk)
        if piece:
            reply += piece
            yield {"type": "reply", "text": piece}
    clean, suggestions = _split_suggestions(reply)
    if out.get("empty_result"):
        clean += "\n\n（提示：未查到符合条件的数据，可尝试更换关键词或表名。）"
    yield {
        "type": "done",
        "thread_id": tid,
        "reply": clean,
        "sql": sql,
        "suggestions": suggestions,
        "error": "",
        "sql_explanation": out.get("sql_explanation", ""),
        "sql_preview": out.get("sql_preview") or {},
    }


def chat(text: str, thread_id: str | None = None) -> ChatOutcome:
    """便捷对话接口：结果缓存（省 LLM 成本）+ 结构化 ChatOutcome + 审计。"""
    t0 = time.time()
    if not text or not text.strip():
        return ChatOutcome(reply="请输入您的问题。", thread_id=thread_id or "")
    tid = thread_id or _new_thread_id()
    question = text.strip()

    # 结果缓存（按单轮问题键控；命中直接返回）
    key = hashlib.sha256(question.encode("utf-8")).hexdigest()
    from core.cache import get as cache_get, set as cache_set

    cached = cache_get(key)
    if cached is not None:
        cached.pop("thread_id", None)  # 使用当前会话 thread_id
        outcome = ChatOutcome(thread_id=tid, **cached)
        _write_audit(question, outcome, int((time.time() - t0) * 1000))
        return outcome

    # 多查询拆分（仅 query_data）：一句话含多个独立查询时逐个执行再汇总
    subs = split_queries(question) if detect_intent(question)[0] == "query_data" else [question]
    if len(subs) <= 1:
        out = _graph.invoke(
            {"user_input": question},
            config={"configurable": {"thread_id": tid}},
        )
        outcome = _build_outcome(question, out, tid)
        is_query_ok = (
            out.get("intent") == "query_data"
            and not out.get("error")
            and not out.get("need_clarify")
            and not out.get("needs_review")
            and out.get("sql")
        )
    else:
        outcomes = []
        for sq in subs:
            so = _graph.invoke(
                {"user_input": sq},
                config={"configurable": {"thread_id": tid}},
            )
            outcomes.append(_build_outcome(sq, so, tid))
        outcome = _merge_multi(question, outcomes, tid)
        is_query_ok = (
            outcome.intent == "query_data"
            and not outcome.error
            and not outcome.needs_clarify
            and not outcome.needs_review
            and bool(outcome.sql)
        )
    _write_audit(question, outcome, int((time.time() - t0) * 1000))

    # 仅缓存"成功的数据查询"结果（排除澄清与待审核）
    if is_query_ok:
        cache_set(key, outcome.to_dict(), _cache_ttl())
    return outcome
