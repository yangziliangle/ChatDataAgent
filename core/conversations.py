"""服务端会话存储：SQLite（storage/conversations.sqlite3）。

会话历史从浏览器 localStorage 迁到后端，支持换设备/多端恢复（无用户体系，按会话 id 存取）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / "storage" / "conversations.sqlite3"

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB), check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            " id TEXT PRIMARY KEY, title TEXT DEFAULT '', updated_at TEXT)"
        )
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " conversation_id TEXT, role TEXT, payload TEXT)"
        )
        _conn.commit()
    return _conn


def list_conversations(limit: int = 100) -> list[dict]:
    """返回会话列表（按更新时间倒序），不含消息内容。"""
    with _lock:
        rows = _get_conn().execute(
            "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "title": r[1] or "", "updated_at": r[2]}
            for r in rows
        ]


def create_conversation(title: str = "") -> str:
    cid = str(uuid.uuid4())
    with _lock:
        _get_conn().execute(
            "INSERT INTO conversations (id, title, updated_at) VALUES (?,?,?)",
            (cid, title, _now()),
        )
        _get_conn().commit()
    return cid


def get_conversation(cid: str) -> dict | None:
    """返回会话含消息（payload JSON 解析为对象）。"""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, title FROM conversations WHERE id=?", (cid,)
        ).fetchone()
        if not row:
            return None
        msgs = conn.execute(
            "SELECT role, payload FROM messages WHERE conversation_id=? ORDER BY id",
            (cid,),
        ).fetchall()
        return {
            "id": row[0],
            "title": row[1] or "",
            "messages": [
                {"role": r[0], "content": json.loads(r[1])}
                for r in msgs
            ],
        }


def append_message(cid: str, role: str, content) -> None:
    """追加一条消息；content 为可 JSON 序列化的对象。"""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO messages (conversation_id, role, payload) VALUES (?,?,?)",
            (cid, role, json.dumps(content, ensure_ascii=False)),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (_now(), cid)
        )
        conn.commit()


def update_title(cid: str, title: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title, _now(), cid),
        )
        conn.commit()


def delete_conversation(cid: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        conn.commit()
