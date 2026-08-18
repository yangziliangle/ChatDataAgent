"""LLM 工厂：返回 DeepSeek LLM 实例，未配置 Key 直接抛异常。"""
from __future__ import annotations

from core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE,
)

_llm_cache: dict = {}


def get_llm():
    """返回 DeepSeek LLM 实例。必须配置 API Key，否则抛异常。"""
    if "llm" in _llm_cache:
        return _llm_cache["llm"]
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "必须配置 DeepSeek API Key：请在 config/.env 中设置 DEEPSEEK_API_KEY"
        )
    from langchain_deepseek import ChatDeepSeek

    llm = ChatDeepSeek(
        api_key=DEEPSEEK_API_KEY,
        model=DEEPSEEK_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        temperature=DEEPSEEK_TEMPERATURE,
        max_tokens=DEEPSEEK_MAX_TOKENS,
        timeout=60,
    )
    _llm_cache["llm"] = llm
    return llm


def invoke_text(prompt) -> str:
    """调用 LLM 并返回文本内容（兼容 content 属性或直接字符串）。"""
    resp = get_llm().invoke(prompt)
    return resp.content if hasattr(resp, "content") else str(resp)
