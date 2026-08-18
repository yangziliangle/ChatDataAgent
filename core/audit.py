"""查询审计日志：记录每次问数的关键信息，落 SQLite（storage/audit.sqlite3）。

供商业化合规使用；多租户后需替换为独立审计库。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
_AUDIT_DB = _STORAGE_DIR / "audit.sqlite3"

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    thread_id TEXT,
    question TEXT,
    intent TEXT,
    sql TEXT,
    rows INTEGER,
    chart TEXT,
    needs_clarify INTEGER DEFAULT 0,
    error TEXT,
    duration_ms INTEGER
)
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_AUDIT_DB), check_same_thread=False)
        _conn.execute(_CREATE_SQL)
        _conn.commit()
    return _conn


def log_audit(
    *,
    thread_id: str = "",
    question: str = "",
    intent: str = "",
    sql: str = "",
    rows: int = 0,
    chart: str = "",
    needs_clarify: bool = False,
    error: str = "",
    duration_ms: int = 0,
) -> None:
    """写一条审计记录；失败静默（不阻断正常回复）。"""
    try:
        with _lock:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO audit_log (ts, thread_id, question, intent, sql, rows, chart,"
                " needs_clarify, error, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    thread_id,
                    question,
                    intent,
                    sql,
                    rows,
                    chart,
                    1 if needs_clarify else 0,
                    error,
                    duration_ms,
                ),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        pass
