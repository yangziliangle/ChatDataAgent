"""表注册表：构建/缓存全表元数据、合并配置别名、关键词粗筛。

纯数据层，不依赖 LLM，可离线测试。所有方法在需要时才懒加载数据源与配置，
保证无 Key / 无 MySQL 环境下仍可 import 并跑纯规则单测。
"""
from __future__ import annotations

import time

from interfaces import get_datasource
from interfaces.datasource import DataSource

# 粗筛候选上限（后续由 LLM 精挑为 top_k）
CANDIDATE_LIMIT = 5
# schema 缓存 TTL（秒）；0 表示不过期。可由 settings nl2sql.schema_cache_ttl 覆盖
SCHEMA_CACHE_TTL = 300

# 进程级缓存：表名 -> (写入时间戳, 元数据 dict)；None 键缓存"全表"那份
_META_CACHE: dict[str | None, tuple[float, dict[str, dict]]] = {}


class TableRegistry:
    def __init__(
        self,
        ds: DataSource | None = None,
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self._ds = ds
        self._aliases = aliases
        self._aliases_loaded = aliases is not None

    # ---------- 数据源与配置（懒加载） ----------

    def _get_ds(self) -> DataSource:
        if self._ds is None:
            self._ds = get_datasource()
        return self._ds

    def _get_aliases(self) -> dict[str, list[str]]:
        """表别名来自 settings.datasource.mysql.table_aliases（可选）。"""
        if not self._aliases_loaded:
            try:
                from core.config import settings

                self._aliases = (
                    settings().get("datasource", {}).get("mysql", {}).get("table_aliases", {}) or {}
                )
            except Exception:  # noqa: BLE001
                self._aliases = {}
            self._aliases_loaded = True
        return self._aliases or {}

    def _ttl(self) -> float:
        try:
            from core.config import settings

            return float(
                settings().get("nl2sql", {}).get("schema_cache_ttl", SCHEMA_CACHE_TTL) or 0
            )
        except Exception:  # noqa: BLE001
            return SCHEMA_CACHE_TTL

    # ---------- 缓存 ----------

    @staticmethod
    def invalidate(table: str | None = None) -> None:
        """手动失效缓存（测试/运维用）。table=None 清空全部。"""
        if table is None:
            _META_CACHE.clear()
        else:
            _META_CACHE.pop(table, None)
            _META_CACHE.pop(None, None)

    def _is_fresh(self, key: str | None) -> bool:
        if key not in _META_CACHE:
            return False
        ts, _ = _META_CACHE[key]
        ttl = self._ttl()
        if not ttl:
            return True
        return (time.time() - ts) < ttl

    def get_meta(self, table: str | None = None) -> dict[str, dict]:
        """返回 {表名: {comment, columns, aliases}}；table=None 返回全部表。

        命中缓存直接返回；未命中调数据源 get_table_meta，合并配置别名后写缓存。
        """
        if self._is_fresh(table):
            return _META_CACHE[table][1]
        if table is not None:
            # 单表：表不存在/查询失败视为无此表，不向上抛
            try:
                raw = self._get_ds().get_table_meta(table)
            except Exception:  # noqa: BLE001
                raw = {}
        else:
            raw = self._get_ds().get_table_meta(None)
        now = time.time()
        meta: dict[str, dict] = {}
        for tname, m in raw.items():
            merged = dict(m)
            merged["aliases"] = list(self._get_aliases().get(tname, []) or [])
            meta[tname] = merged
            _META_CACHE[tname] = (now, {tname: merged})
        if table is None:
            _META_CACHE[None] = (now, meta)
        return meta

    def tables(self) -> list[str]:
        """全部表名（来自缓存元数据）。"""
        return list(self.get_meta(None).keys())

    def get_schema(self, table: str) -> list[dict]:
        """表列信息 [{name, type, comment}]，走缓存；表不存在返回空列表。"""
        meta = self.get_meta(table)
        if table in meta:
            return meta[table]["columns"]
        return []

    # ---------- 关键词粗筛 ----------

    def match(self, question: str) -> list[str]:
        """按表名/别名/表注释对问题做关键词粗筛。

        打分：表名或别名完整命中 +3；表注释按 2 字滑窗 bigram 命中 +1。
        返回得分 >0 的表名，按得分降序，截断到 CANDIDATE_LIMIT。
        """
        q = (question or "").strip()
        if not q:
            return []
        ql = q.lower()
        scored: list[tuple[int, str]] = []
        for tname, m in self.get_meta(None).items():
            score = 0
            names = [tname] + list(m.get("aliases", []) or [])
            if any(n and n.lower() in ql for n in names):
                score += 3
            comment = m.get("comment", "") or ""
            if comment:
                for i in range(len(comment) - 1):
                    if comment[i : i + 2] in q:
                        score += 1
            if score > 0:
                scored.append((score, tname))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [t for _, t in scored[:CANDIDATE_LIMIT]]
