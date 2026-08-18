"""NL2SQL 生成器：基于表结构 + 用户问题生成只读 SQL，并做安全校验。

- 两段式：上层先选表，这里只把选中的表结构拼给 DeepSeek 生成只读 SQL
- 兜底：生成后检查 FROM/JOIN 引用，若引用了未提供结构的现存表则补表重试一次
- 只读校验：拦截非 SELECT 语句，防止破坏性 SQL
"""
from __future__ import annotations

import re

from core.clarify import extract_clarify
from core.llm import invoke_text
from core.runtime import get_strictness
from core.table_registry import TableRegistry


class SQLValidationError(ValueError):
    pass


class NeedClarificationError(ValueError):
    """LLM 认为问题不明确、需要用户澄清（刻意不继承 SQLValidationError）。"""

    def __init__(self, message: str, clarify_question: str = "") -> None:
        super().__init__(message)
        self.clarify_question = clarify_question or message


# 只读白名单：仅允许 SELECT
_READONLY_RE = re.compile(r"^\s*select\b", re.IGNORECASE)
# 危险操作关键词（即使被 SELECT 包裹也拦截）
_DANGER_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|replace|merge|rename|"
    r"exec|execute|call|load|outfile|dumpfile|sleep)\b",
    re.IGNORECASE,
)
# 多语句分割（防止用分号注入第二条语句）
_STMT_SPLIT_RE = re.compile(r";")
# 粗提取 FROM/JOIN 引用的表名
_REF_TABLE_RE = re.compile(
    r"\b(?:from|join)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?", re.IGNORECASE
)
# 生成 SQL 后若引用未提供结构的现存表，最多补表重试次数
MAX_SQL_RETRY = 1


def build_schema_context(
    schemas: dict[str, list[dict]], comments: dict[str, str] | None = None
) -> str:
    """把表结构拼成文本，作为 LLM 生成 SQL 的上下文。"""
    comments = comments or {}
    lines = []
    for table, cols in schemas.items():
        if comments.get(table):
            lines.append(f"-- 表注释：{comments[table]}")
        col_desc = ", ".join(f"{c['name']}({c['type']},{c['comment']})" for c in cols)
        lines.append(f"表 {table}: {col_desc}")
    return "\n".join(lines)


def extract_sql(text: str) -> str:
    """从 LLM 输出中提取 SQL（兼容 markdown 代码块）。"""
    text = (text or "").strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _clarify_rule(strictness: str) -> str:
    """按严谨度返回 SQL 生成 prompt 的"澄清规则"指令。"""
    if strictness == "strict":
        return (
            "1. 如果用户问题在统计口径、统计范围、分组维度或目标表上存在不明确，无法唯一确定一条 SQL，"
            "请先输出一行以 CLARIFY: 开头的简体中文澄清问题向用户确认，此时不要输出任何 SQL；"
            "否则输出 SQL。\n"
        )
    return (
        "1. 请生成一条合理的 SQL：统计口径/范围不明确时采用常见默认（如按全部数据统计、"
        "按主要维度分组、数值列用 SUM/AVG/COUNT），可自由使用函数、聚合与 JOIN。"
        "仅当确实无法确定目标表时，才只输出一行以 CLARIFY: 开头的简体中文澄清问题，此时不要输出任何 SQL。\n"
    )


def _referenced_tables(sql: str) -> list[str]:
    """粗提取 SQL 中 FROM/JOIN 引用的表名（去重）。"""
    if not sql:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _REF_TABLE_RE.finditer(sql):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _check_table_permission(sql: str, all_tables: list[str]) -> None:
    """表权限白名单：nl2sql.allow_tables 非空时，引用的现存表必须都在白名单内。"""
    try:
        from core.config import settings

        allow = settings().get("nl2sql", {}).get("allow_tables", []) or []
    except Exception:  # noqa: BLE001
        allow = []
    if not allow:
        return
    allowed = set(allow)
    denied = [t for t in _referenced_tables(sql) if t in all_tables and t not in allowed]
    if denied:
        raise SQLValidationError(f"无权查询表：{'、'.join(denied)}")


class SQLGenerator:
    def __init__(self, registry: TableRegistry | None = None) -> None:
        self._registry = registry

    def _get_registry(self) -> TableRegistry:
        if self._registry is None:
            self._registry = TableRegistry()
        return self._registry

    def generate(self, question: str, tables: list[str] | None = None) -> str:
        """生成一条只读 SQL。

        tables=None 时回退全表（向后兼容）；否则只基于给定表结构生成，
        生成后若引用未提供结构的现存表则补表重试一次（MAX_SQL_RETRY）。
        """
        registry = self._get_registry()
        all_tables = registry.tables()
        table_list = tables if tables else all_tables
        schemas: dict[str, list[dict]] = {}
        comments: dict[str, str] = {}
        for t in table_list:
            cols = registry.get_schema(t)
            if cols:
                schemas[t] = cols
                meta = registry.get_meta(t)
                comments[t] = meta.get(t, {}).get("comment", "")

        for _ in range(MAX_SQL_RETRY + 1):
            sql = self._via_llm(question, schemas, comments, all_tables)
            # 先于引用检查/validate：LLM 标疑时以 CLARIFY: 开头，不是合法 SQL
            clarify = extract_clarify(sql)
            if clarify is not None:
                raise NeedClarificationError(f"需要澄清：{clarify}", clarify)
            missing = [
                t for t in _referenced_tables(sql) if t in all_tables and t not in schemas
            ]
            if not missing:
                _check_table_permission(sql, all_tables)
                return self.validate(sql)
            for t in missing:
                cols = registry.get_schema(t)
                if cols:
                    schemas[t] = cols
                    meta = registry.get_meta(t)
                    comments[t] = meta.get(t, {}).get("comment", "")
        raise SQLValidationError(f"生成的 SQL 引用了未提供的表：{missing}")

    def _via_llm(
        self,
        question: str,
        schemas: dict[str, list[dict]],
        comments: dict[str, str],
        all_tables: list[str],
    ) -> str:
        ctx = build_schema_context(schemas, comments)
        all_names = "、".join(all_tables) if all_tables else "（空库）"
        prompt = (
            "你是数据分析 SQL 工程师。请根据给定表结构，把用户问题转成一条 MySQL SELECT 语句。\n"
            "要求：\n"
            + _clarify_rule(get_strictness())
            + "2. 只输出 SQL 本身，不要任何解释、不要 markdown 代码块。\n"
            "3. 只能使用下方「表结构」中列出的表；只读查询，禁止任何写操作。\n"
            "4. 数据库全部表名清单（仅供识别真实表名，禁止臆造未给出结构的表）："
            f"{all_names}\n\n"
            f"表结构：\n{ctx}\n\n用户问题：{question}\n\nSQL:"
        )
        return extract_sql(invoke_text(prompt))

    def validate(self, sql: str) -> str:
        """只读安全校验，返回清洗后的单条 SQL。"""
        sql = (sql or "").strip().strip(";").strip()
        if not sql:
            raise SQLValidationError("生成的 SQL 为空")
        # 多语句拆分后取第一条并整体再校验（不允许任何额外语句）
        if len(_STMT_SPLIT_RE.split(sql)) > 1:
            raise SQLValidationError("检测到多条语句，仅允许单条查询")
        if not _READONLY_RE.match(sql):
            raise SQLValidationError("仅允许只读 SELECT 查询")
        if _DANGER_RE.search(sql):
            raise SQLValidationError("检测到非只读操作，已拒绝执行")
        return sql

    def fix_sql(self, question: str, sql: str, error: str) -> str:
        """根据执行错误让 LLM 修正 SQL；修正失败/无效时返回原 SQL。"""
        if not sql or not error:
            return sql
        prompt = (
            "上一条 MySQL SELECT 执行失败。请根据错误信息修正 SQL，使其符合 MySQL 语法与 sql_mode 要求"
            "（注意 ONLY_FULL_GROUP_BY：SELECT 中的非聚合列必须出现在 GROUP BY 中）。"
            "只输出修正后的 SQL，不要任何解释。\n"
            f"用户问题：{question}\n原 SQL：{sql}\n执行错误：{error}"
        )
        try:
            fixed = extract_sql(invoke_text(prompt))
        except Exception:  # noqa: BLE001
            return sql
        return fixed or sql
