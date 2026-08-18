"""表语义模块：预加载表 schema → LLM 批量摘要 → 本地 embedding → 内存余弦检索。

辅助模糊查询选表；模型/依赖不可用时自动降级（recall 返回 None），不影响既有链路。
后端默认 fastembed（纯 ONNX Runtime，无 torch），可配置 sentence-transformers。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

from core.llm import invoke_text
from core.table_registry import TableRegistry

# 默认值（可被 settings nl2sql.embedding 覆盖）
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DEFAULT_BACKEND = "fastembed"  # fastembed（无 torch）| st（sentence-transformers）
DEFAULT_TOP_K = 5
DEFAULT_THRESHOLD = 0.55
DEFAULT_SUMMARY_BATCH = 30
DEFAULT_MAX_COLS_IN_TEXT = 20
DEFAULT_ENABLED = True
DEFAULT_SUMMARY_ENABLED = True

_BASE_DIR = Path(__file__).resolve().parent.parent
SUMMARY_CACHE_FILE = _BASE_DIR / "storage" / "table_summaries.json"

# ---------- 进程级状态 ----------

_model = None
_model_status = 0  # 0=未知 1=ok -1=失败
_model_lock = threading.Lock()
_emb_cache: dict[str, tuple[str, str, list[float]]] = {}  # 表名 -> (指纹, 检索文本, 单位向量)
_emb_lock = threading.Lock()
_summary_lock = threading.Lock()


# ---------- 配置 ----------

def _cfg() -> dict:
    try:
        from core.config import settings

        return settings().get("nl2sql", {}).get("embedding", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def _enabled() -> bool:
    return bool(_cfg().get("enabled", DEFAULT_ENABLED))


def _summary_enabled() -> bool:
    return bool(_cfg().get("summary_enabled", DEFAULT_SUMMARY_ENABLED))


def _cache_file() -> Path:
    f = _cfg().get("cache_file")
    if f:
        p = Path(f)
        return p if p.is_absolute() else _BASE_DIR / p
    return SUMMARY_CACHE_FILE


# ---------- 模型懒加载 ----------

def _load_model():
    """按配置后端加载 embedding 模型；失败返回 None。

    默认用 hf-mirror 镜像下载模型并禁用 xet 后端（国内网络），
    可通过 settings nl2sql.embedding.hf_endpoint 改回官方源。
    """
    cfg = _cfg()
    backend = cfg.get("backend", DEFAULT_BACKEND)
    model_name = cfg.get("model", MODEL_NAME)
    device = cfg.get("device", "cpu")
    os.environ.setdefault("HF_ENDPOINT", cfg.get("hf_endpoint", "https://hf-mirror.com"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        if backend == "st":
            from sentence_transformers import SentenceTransformer

            return SentenceTransformer(model_name, device=device)
        from fastembed import TextEmbedding

        return TextEmbedding(model_name=model_name, device=device)
    except Exception:  # noqa: BLE001 —— 未装/加载失败
        return None


def _get_model():
    global _model, _model_status
    if _model_status != 0:
        return _model
    if not _enabled():
        _model_status = -1
        return None
    with _model_lock:
        if _model_status == 0:
            _model = _load_model()
            _model_status = 1 if _model is not None else -1
    return _model


def reset_model() -> None:
    """重置模型状态（测试/运维用）。"""
    global _model, _model_status
    with _model_lock:
        _model, _model_status = None, 0
        _emb_cache.clear()


def _encode(model, texts: list[str], query: bool = False) -> np.ndarray:
    """统一编码 + 归一化，返回 (N, dim) float32。

    兼容 fastembed（embed / bge 的 query_embed）、sentence-transformers（encode）与测试桩。
    """
    if query and hasattr(model, "query_embed"):
        arr = np.asarray(list(model.query_embed(texts)), dtype="float32")
    elif hasattr(model, "embed"):
        arr = np.asarray(list(model.embed(texts)), dtype="float32")
    else:
        arr = np.asarray(model.encode(texts), dtype="float32")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


# ---------- 摘要生成与缓存 ----------

def _fingerprint(meta_entry: dict) -> str:
    cols = [
        (c.get("name"), c.get("type"), c.get("comment"))
        for c in meta_entry.get("columns", [])
    ]
    raw = f"{meta_entry.get('comment', '') or ''}|{cols}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_summary_json(text: str, known_tables: list[str]) -> dict[str, str]:
    """解析 LLM 输出的摘要 JSON，只保留已知表名；失败返回空 dict。"""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.IGNORECASE | re.DOTALL)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        data = json.loads(t[start : end + 1])
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    known = set(known_tables)
    return {
        k: str(v).strip()[:80]
        for k, v in data.items()
        if k in known and str(v).strip()
    }


_SUMMARY_PROMPT_TEMPLATE = """你是数据库表语义分析专家。以下是某 MySQL 业务库中的一批表，每行格式：表名 | 表注释 | 列(列名:类型:列注释)
---
{table_lines}
---
请为每一张表生成一句不超过 50 字的"业务用途摘要"（简体中文），说明该表存储什么业务数据、对应什么业务场景，供后续根据用户自然语言问题匹配表使用。若表注释为空，依据列名/列注释推断；无法推断则写"业务用途待确认"。
严格输出一个 JSON 对象，键必须是给定表名（原样），值是该表摘要。不要 markdown 代码块、不要任何解释。
示例：{{"sales_order": "销售订单表：记录每笔订单的客户、金额与下单时间"}}"""


def _generate_summaries_batch(
    tables: list[str], meta: dict, batch: int
) -> dict[str, str]:
    """分批调用 LLM 生成摘要；某批失败重试一次，再失败跳过该批（回退裸文本）。"""
    result: dict[str, str] = {}
    for i in range(0, len(tables), batch):
        chunk = tables[i : i + batch]
        lines = []
        for t in chunk:
            m = meta.get(t, {})
            comment = m.get("comment", "") or ""
            cols = [
                f"{c.get('name')}:{c.get('type')}:{c.get('comment')}"
                for c in m.get("columns", [])[:6]
            ]
            lines.append(f"- {t} | {comment} | {'、'.join(cols)}")
        prompt = _SUMMARY_PROMPT_TEMPLATE.format(table_lines="\n".join(lines))
        summaries: dict[str, str] = {}
        for _ in range(2):  # 失败重试一次
            try:
                content = invoke_text(prompt)
                summaries = _parse_summary_json(content, chunk)
                if summaries:
                    break
            except Exception:  # noqa: BLE001
                summaries = {}
        result.update(summaries)
    return result


def _load_summary_file(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        tables = data.get("tables", {})
        if isinstance(tables, dict):
            return tables
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _save_summary_file(path: Path, tables: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tables": tables,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写盘


def ensure_summaries(registry: TableRegistry, meta: dict) -> dict[str, str]:
    """返回 {表名: 摘要}；缺失/指纹变化/新表 lazily 生成并落盘。失败不抛。"""
    if not _summary_enabled():
        return {}
    summaries: dict[str, str] = {}
    with _summary_lock:
        try:
            path = _cache_file()
            cached = _load_summary_file(path)
        except Exception:  # noqa: BLE001
            cached = {}
        need = [
            t
            for t, m in meta.items()
            if not (t in cached and cached[t].get("fp") == _fingerprint(m)
                    and cached[t].get("summary"))
        ]
        if not need:
            for t in meta:
                s = cached.get(t, {}).get("summary")
                if s:
                    summaries[t] = s
            return summaries
        generated = _generate_summaries_batch(
            need, meta, _cfg().get("summary_batch", DEFAULT_SUMMARY_BATCH)
        )
        for t in need:
            if generated.get(t):
                summaries[t] = generated[t]
                cached[t] = {"summary": generated[t], "fp": _fingerprint(meta[t])}
            else:
                cached.pop(t, None)  # 生成失败的表移出缓存
        # 清理已不存在的表
        for t in list(cached):
            if t not in meta:
                cached.pop(t, None)
        # 已存在且未变化的摘要仍保留
        for t in meta:
            if t not in summaries and cached.get(t, {}).get("summary"):
                summaries[t] = cached[t]["summary"]
        try:
            _save_summary_file(_cache_file(), cached)
        except OSError:
            pass
        return summaries


# ---------- embedding 文本与检索 ----------

def _build_text(table: str, meta_entry: dict, summary: str) -> str:
    comment = meta_entry.get("comment", "") or ""
    cols = [
        f"{c.get('name')}({c.get('comment') or c.get('type')})"
        for c in meta_entry.get("columns", [])[:DEFAULT_MAX_COLS_IN_TEXT]
    ]
    parts = []
    if comment:
        parts.append(f"表注释：{comment}")
    if summary:
        parts.append(f"业务摘要：{summary}")
    parts.append(f"表名：{table}")
    parts.append(f"字段：{'、'.join(cols)}")
    return "\n".join(parts)


def _refresh_emb_cache(model, meta: dict, summaries: dict) -> None:
    """按指纹增量更新 embedding 缓存（recall 与 warm_up 共用）。"""
    need = [
        t
        for t in meta
        if t not in _emb_cache or _emb_cache[t][0] != _fingerprint(meta[t])
    ]
    if not need:
        return
    texts = {t: _build_text(t, meta[t], summaries.get(t, "")) for t in need}
    vecs = _encode(model, list(texts.values()))
    with _emb_lock:
        for t, v in zip(need, vecs):
            _emb_cache[t] = (_fingerprint(meta[t]), texts[t], v.tolist())


def recall(
    question: str, registry: TableRegistry, top_k: int | None = None
) -> list[tuple[str, float]] | None:
    """内存余弦检索 top_k 候选表 [(表名, 相似度)]；模型不可用/异常返回 None（调用方降级）。"""
    model = _get_model()
    if model is None:
        return None
    try:
        meta = registry.get_meta(None)
        if not meta:
            return None
        top_k = top_k or _cfg().get("top_k", DEFAULT_TOP_K)
        try:
            summaries = ensure_summaries(registry, meta)
        except Exception:  # noqa: BLE001 —— 摘要失败回退裸文本
            summaries = {}

        # 增量更新 embedding 缓存
        _refresh_emb_cache(model, meta, summaries)

        with _emb_lock:
            items = [(t, v) for t, (_, _, v) in _emb_cache.items() if t in meta]
        if not items:
            return None
        qv = _encode(model, [question], query=True)[0]
        matrix = np.stack([v for _, v in items], dtype="float32")
        sims = matrix @ qv
        order = np.argsort(-sims)[:top_k]
        out = [(items[i][0], float(sims[i])) for i in order if sims[i] > 0]
        return out or None
    except Exception:  # noqa: BLE001 —— embedding 任何异常都不影响主链路
        return None


# ---------- 门面与预热 ----------

class _SemanticFacade:
    def recall(self, question, registry, top_k=None):
        return recall(question, registry, top_k)


_semantic: _SemanticFacade | None = None


def get_semantic():
    global _semantic
    if _semantic is None:
        _semantic = _SemanticFacade()
    return _semantic


def warm_up(sync: bool = True) -> None:
    """预加载摘要 + embedding。sync=False 时后台线程执行，不阻塞首查。"""

    def _do() -> None:
        try:
            model = _get_model()
            if model is None:
                return
            registry = TableRegistry()
            meta = registry.get_meta(None)
            if not meta:
                return
            try:
                summaries = ensure_summaries(registry, meta)
            except Exception:  # noqa: BLE001
                summaries = {}
            _refresh_emb_cache(model, meta, summaries)
        except Exception:  # noqa: BLE001
            pass

    if sync:
        _do()
    else:
        threading.Thread(target=_do, daemon=True).start()
