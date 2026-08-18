"""表选择器：三规则判定（classify，纯规则）+ LLM 精挑（select_topk）。

- classify / _parse_selected_tables 不依赖 LLM，可离线测试；
- 只有 select_topk / finalize 的 select 分支才调用 LLM，inherit 分支零额外调用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from core.constants import MAX_FOLLOWUP_LEN
from core.llm import invoke_text
from core.table_registry import CANDIDATE_LIMIT, TableRegistry
from core.table_semantic import DEFAULT_TOP_K, DEFAULT_THRESHOLD

TOP_K = 3

# 指代延续词：命中（且未命中任何表名）时继承上轮表上下文。
# 刻意不含"上"（避免"上个月/上周"等误判），由业务高频追问信号兜底。
_CONTINUATION_WORDS = ("那", "再", "它", "这个", "这些", "那个", "上述", "平均", "趋势", "呢")


@dataclass(frozen=True)
class Decision:
    route: Literal["select", "inherit", "empty"]
    tables: tuple[str, ...]
    inherited: bool = False
    # 表级是否确定：仅兜底路径（无关键词命中）设为 False
    confident: bool = True


@dataclass(frozen=True)
class SelectionResult:
    """decide() 的结构化结果，供 agent_graph 判断是否需要澄清。

    method: 观测/测试用（llm | embedding_high | inherit | empty）；
    confident 复用表达两个分支（embedding 高置信=True，LLM 低置信=False）。
    """

    tables: list[str]
    confident: bool
    route: Literal["select", "inherit", "empty"]
    method: str = "llm"


class TableSelector:
    def __init__(self, registry: TableRegistry | None = None, semantic=None) -> None:
        self._registry = registry
        self._semantic = semantic  # 测试可注入 FakeSemantic

    def _get_registry(self) -> TableRegistry:
        if self._registry is None:
            self._registry = TableRegistry()
        return self._registry

    def _get_semantic(self):
        if self._semantic is None:
            from core.table_semantic import get_semantic

            self._semantic = get_semantic()
        return self._semantic

    # ---------- 三规则判定（纯规则，可离线测试） ----------

    def classify(self, question: str, active_tables: list[str]) -> Decision:
        """根据当前问题与上轮 active_tables，决定 select / inherit / empty。

        规则①：显式命中表名/别名/表注释 → 切换为命中的表（select）
        规则③：既命中延续词又含新表 → 新表并入继承表一起选（select）
        规则②：未命中任何表，且是短句或含延续词 → 继承上轮表（inherit，跳过选表）
        兜底：无命中且不是追问 → 取前若干张表交给 LLM 精挑（select）
        """
        q = (question or "").strip()
        if not q:
            return Decision("empty", (), confident=False)
        registry = self._get_registry()
        all_tables = registry.tables()
        if not all_tables:
            return Decision("empty", (), confident=False)
        # 先过滤已不存在（被删）的继承表
        active = [t for t in active_tables if t in all_tables]

        hits = registry.match(q)
        has_cont = any(w in q for w in _CONTINUATION_WORDS)

        if hits:
            # 规则①：切换
            new_terms = [t for t in hits if t not in active]
            # 规则③：混合 → 新表并入继承表，交给 LLM 精挑
            if has_cont and new_terms and active:
                merged = list(dict.fromkeys(list(hits) + active))
                return Decision("select", tuple(merged))
            return Decision("select", tuple(hits))
        # 规则②：无显式表名 → 短句或含延续词 → 继承
        if active and (has_cont or len(q) <= MAX_FOLLOWUP_LEN):
            return Decision("inherit", tuple(active), inherited=True)
        # 兜底：无关键词命中且非追问 → 取前 CANDIDATE_LIMIT 张表交 LLM 精挑（配合 sql_gen 的表引用兜底）
        return Decision("select", tuple(all_tables[:CANDIDATE_LIMIT]), confident=False)

    # ---------- LLM 精挑 ----------

    def decide(self, question: str, active_tables: list[str]) -> SelectionResult:
        """图节点入口：classify + 分派，返回本轮使用的表与置信度。

        empty/inherit/显式命中走原逻辑；兜底路径（confident=False）走 embedding 混合策略。
        """
        decision = self.classify(question, active_tables)
        if decision.route == "empty":
            return SelectionResult([], False, "empty", "empty")
        if decision.route == "inherit":
            return SelectionResult(list(decision.tables), True, "inherit", "inherit")
        if decision.confident:
            # 显式命中（规则①/③）：LLM 从命中表精挑，embedding 不介入
            return SelectionResult(
                self.select_topk(question, list(decision.tables)),
                True,
                "select",
                "llm",
            )
        return self._select_fallback(question, decision)

    def _select_fallback(self, question: str, decision: Decision) -> SelectionResult:
        """兜底路径混合策略：embedding 高置信直用 top-3，否则 LLM 精挑，降级回现状。"""
        cfg = self._embedding_cfg()
        threshold = cfg.get("threshold", DEFAULT_THRESHOLD)
        top_k = cfg.get("top_k", DEFAULT_TOP_K)
        try:
            scored = self._get_semantic().recall(
                question, self._get_registry(), top_k
            )
        except Exception:  # noqa: BLE001 —— 语义模块异常视为降级
            scored = None
        if scored and scored[0][1] >= threshold:
            tables = [t for t, _ in scored[:TOP_K]]
            if tables:
                return SelectionResult(tables, True, "select", "embedding_high")
        if scored:
            merged = self._merge_candidates(
                question, [t for t, _ in scored], list(decision.tables)
            )
            return SelectionResult(
                self.select_topk(question, merged), False, "select", "llm"
            )
        # 降级：与现状一致（候选为兜底表，LLM 精挑）
        return SelectionResult(
            self.select_topk(question, list(decision.tables)),
            False,
            "select",
            "llm",
        )

    @staticmethod
    def _embedding_cfg() -> dict:
        try:
            from core.config import settings

            return settings().get("nl2sql", {}).get("embedding", {}) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _merge_candidates(
        self,
        question: str,
        emb_tables: list[str],
        fallback_tables: list[str],
        limit: int = CANDIDATE_LIMIT,
    ) -> list[str]:
        """合并关键词命中 ∪ embedding 召回 ∪ 兜底表，去重保序后截断。"""
        kw = self._get_registry().match(question)
        return list(dict.fromkeys(kw + emb_tables + fallback_tables))[:limit]

    def select_topk(self, question: str, candidates: list[str], k: int = TOP_K) -> list[str]:
        """LLM 从候选表中挑最相关的 k 张表。

        解析为空（LLM 认为问题过宽、输出 NONE 或无有效表名）时返回空列表，作为"不确定"
        信号由上层触发澄清；仅 LLM 调用异常时保守回退 candidates[:k]。
        """
        if not candidates:
            return []
        registry = self._get_registry()
        meta = registry.get_meta(None)
        lines = []
        for t in candidates:
            m = meta.get(t, {})
            col_hint = ", ".join(c.get("name", "") for c in m.get("columns", [])[:6])
            lines.append(f"- {t} | {m.get('comment', '')} | {col_hint}")
        prompt = (
            f"你是数据库表选择助手。请从候选表中选出与用户问题最相关的 {k} 张表。\n\n"
            "候选表（表名 | 表注释 | 部分字段）：\n"
            + "\n".join(lines)
            + f"\n\n用户问题：{question}\n\n"
            "规则：\n"
            "1. 只输出所选表的表名，每行一个；不要序号、不要解释、不要 markdown 代码块；"
            "按相关度从高到低排列。\n"
            "2. 如果用户问题过于宽泛，无法判断目标表，请只输出 NONE。\n\n"
            "所选表名："
        )
        try:
            content = invoke_text(prompt)
        except Exception:  # noqa: BLE001 —— LLM 调用失败时保守放行
            return candidates[:k]
        selected = self._parse_selected_tables(content, candidates, registry)
        if not selected:
            return []
        return list(dict.fromkeys(selected))[:k]

    # ---------- 纯解析（可离线测试） ----------

    @staticmethod
    def _parse_selected_tables(
        text: str,
        candidates: list[str],
        registry: TableRegistry | None = None,
    ) -> list[str]:
        """解析 LLM 输出的表名列表，只保留候选集内（或别名映射回）的表名，去重保序。"""
        name_to_table = {t: t for t in candidates}
        if registry is not None:
            meta = registry.get_meta(None)
            for t in candidates:
                for alias in meta.get(t, {}).get("aliases", []) or []:
                    name_to_table[alias] = t
        out: list[str] = []
        for raw in (text or "").splitlines():
            token = raw.strip().strip("`")
            # 去掉序号/列表符号前缀（如 "1."、"1、"、"1) "）
            token = re.sub(r"^[\d\s]*[\.\、\)\-]?\s*", "", token).strip()
            if not token:
                continue
            if token in name_to_table:
                t = name_to_table[token]
                if t not in out:
                    out.append(t)
        return out
