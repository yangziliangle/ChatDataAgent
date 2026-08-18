"""轻量进程内 TTL 缓存（线程安全）。

用于结果缓存（省 LLM 成本）；多实例部署需替换为 Redis 共享缓存。
"""
from __future__ import annotations

import threading
import time

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
DEFAULT_TTL = 300


def set(key: str, value, ttl: int = DEFAULT_TTL) -> None:
    """写入缓存；ttl<=0 表示不过期。"""
    with _lock:
        _cache[key] = (time.time() + ttl if ttl > 0 else float("inf"), value)


def get(key: str):
    """读取缓存；过期条目自动清除并返回 None。"""
    with _lock:
        item = _cache.get(key)
        if item is None:
            return None
        expire_at, value = item
        if time.time() > expire_at:
            _cache.pop(key, None)
            return None
        return value


def clear() -> None:
    with _lock:
        _cache.clear()


def size() -> int:
    with _lock:
        return len(_cache)
