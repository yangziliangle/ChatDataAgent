"""进程内可调运行时参数（当前：严谨度 strictness）。

严谨度控制 SQL 生成的澄清严格程度；重启后回默认 relaxed。
"""
from __future__ import annotations

_VALID = ("relaxed", "strict")
_strictness = "relaxed"


def get_strictness() -> str:
    return _strictness


def set_strictness(value: str) -> bool:
    global _strictness
    if value in _VALID:
        _strictness = value
        return True
    return False
