"""跨模块共享常量（低层、无依赖，供纯逻辑模块复用，避免依赖方向颠倒）。"""
from __future__ import annotations

# 追问判定：短句（≤此长度）视为对上一轮/澄清的回答
MAX_FOLLOWUP_LEN = 20
