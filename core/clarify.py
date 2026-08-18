"""模糊查询的澄清逻辑：LLM 标疑解析、澄清合并策略。

纯逻辑模块，不依赖 agent_graph，可离线单测。
"""
from __future__ import annotations

import re

from core.constants import MAX_FOLLOWUP_LEN
from core.intent import detect_intent

# 连续澄清上限，超过则停止反问（防死循环）
MAX_CLARIFY = 2

_CLARIFY_RE = re.compile(r"^\s*CLARIFY\s*[:：]\s*(.+)$", re.IGNORECASE | re.DOTALL)


def extract_clarify(text: str) -> str | None:
    """LLM 输出若以 `CLARIFY:` 开头则提取澄清问题，否则返回 None。

    兼容全角冒号、大小写与前后空白。
    """
    t = (text or "").strip()
    m = _CLARIFY_RE.match(t)
    if not m:
        return None
    q = m.group(1).strip()
    return q or None


def merge_policy(pending: str, new_text: str) -> str:
    """判断用户对澄清问题的回答是"补充"还是"新话题"。

    - 非 query_data 意图（meta/chat/deny）→ "reset"（新话题）
    - 短句（≤ MAX_FOLLOWUP_LEN）→ "merge"（视为对澄清的回答，如"销售订单表"、"本季度"）
    - 长句 → "reset"（视为自足的完整新问题）
    """
    intent, _, _ = detect_intent(new_text)
    if intent != "query_data":
        return "reset"
    if len((new_text or "").strip()) <= MAX_FOLLOWUP_LEN:
        return "merge"
    return "reset"


# 纯确认/简单应答：不拼接无意义后缀，直接用原问题让 LLM 按默认口径执行
_CONFIRM_WORDS = ("是的", "对", "可以", "确定", "好", "嗯", "手动", "按你", "你定")


def merge_question(original: str, answer: str) -> str:
    """把用户的澄清回答并入原问题，供下一轮完整走链路。

    命中确认词时直接返回原问题（避免拼出"原问题（是的）"这种无意义输入）。
    """
    if any(k in (answer or "") for k in _CONFIRM_WORDS):
        return original
    return f"{original}（{answer}）"
