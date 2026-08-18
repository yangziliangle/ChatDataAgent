"""Agent 状态（LangGraph state）与输出 DTO（ChatOutcome）。

从 agent_graph 拆出，供图节点与对外门面共用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from interfaces.datasource import QueryResult


class AgentState(TypedDict):
    user_input: str
    intent: str
    force_chart: bool
    requested_chart_type: str
    sql: str
    query_result: QueryResult | None
    analysis: dict | None
    reply: str
    error: str
    active_tables: list[str]
    need_clarify: bool
    clarify_kind: str
    pending_original: str | None
    pending_clarification: str | None
    clarify_count: int
    empty_result: bool
    needs_review: bool
    suggestions: list[str]
    sql_explanation: str
    sql_preview: dict
    messages: Annotated[list[BaseMessage], add_messages]


@dataclass
class ChatOutcome:
    """一次问答的结构化输出（供 CLI/Web 层使用）。"""

    reply: str
    thread_id: str
    intent: str = ""
    sql: str = ""
    table: dict | None = None
    chart: dict | None = None
    error: str = ""
    active_tables: list[str] = field(default_factory=list)
    needs_clarify: bool = False
    clarification: str = ""
    needs_review: bool = False
    suggestions: list[str] = field(default_factory=list)
    sql_explanation: str = ""
    sql_preview: dict = field(default_factory=dict)
    tables: list = field(default_factory=list)  # 多查询结果：多个表格
    charts: list = field(default_factory=list)  # 多查询结果：多个图表

    def to_dict(self) -> dict:
        return {
            "reply": self.reply,
            "thread_id": self.thread_id,
            "intent": self.intent,
            "sql": self.sql,
            "table": self.table,
            "chart": self.chart,
            "error": self.error,
            "active_tables": self.active_tables,
            "needs_clarify": self.needs_clarify,
            "clarification": self.clarification,
            "needs_review": self.needs_review,
            "suggestions": self.suggestions,
            "sql_explanation": self.sql_explanation,
            "sql_preview": self.sql_preview,
            "tables": self.tables,
            "charts": self.charts,
        }
