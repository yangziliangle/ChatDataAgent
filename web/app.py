"""Flask Web 后端：ChatDataAgent 聊天查询界面（端口 5003）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 允许从项目根导入 core/interfaces/impl 包（无论从哪个目录启动）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template, request  # noqa: E402
from flask_cors import CORS  # noqa: E402

from core.agent_graph import chat  # noqa: E402
from core.config import settings  # noqa: E402
from interfaces import get_datasource  # noqa: E402

app = Flask(__name__)
CORS(app)


def ok(data=None):
    """统一成功响应：{code:0, message:"ok", data}。"""
    return jsonify({"code": 0, "message": "ok", "data": data})


def err(code: int, message: str):
    """统一错误响应：{code, message, data:null}。"""
    return jsonify({"code": code, "message": message, "data": None}), code


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    """健康检查（供 Node 网关探测）。"""
    return jsonify({"ok": True})


@app.get("/api/tables")
def api_tables():
    """返回数据源标识与表结构（供前端展示）。"""
    ds = get_datasource()
    try:
        data = ds.get_table_meta(None)
        db_name = settings()["datasource"]["mysql"].get("database") or "MySQL"
        return ok({"db_name": db_name, "tables": data})
    except Exception as e:  # noqa: BLE001
        return err(500, str(e))


@app.post("/api/chat")
def api_chat():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    thread_id = body.get("thread_id") or ""
    if not question:
        return err(400, "请输入问题")
    try:
        outcome = chat(question, thread_id=thread_id or None)
        return ok(outcome.to_dict())
    except Exception as e:  # noqa: BLE001
        return err(500, f"服务异常：{e}")


@app.post("/api/chat/stream")
def api_chat_stream():
    """SSE 流式问答：逐事件推送 meta/table/chart/reply/done。"""
    from flask import Response

    from core.agent_graph import chat_stream

    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    thread_id = body.get("thread_id") or ""

    def gen():
        for event in chat_stream(question, thread_id=thread_id or None):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/execute")
def api_chat_execute():
    """执行审核通过的 SQL（不经过选表/重新生成）。"""
    from core.agent_graph import execute_sql

    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    sql = (body.get("sql") or "").strip()
    thread_id = body.get("thread_id") or ""
    if not sql:
        return err(400, "缺少 sql")
    try:
        outcome = execute_sql(question, sql, thread_id=thread_id or None)
        return ok(outcome.to_dict())
    except Exception as e:  # noqa: BLE001
        return err(500, f"执行失败：{e}")


@app.get("/api/config/strictness")
def api_get_strictness():
    from core.runtime import get_strictness

    return ok({"strictness": get_strictness()})


@app.post("/api/config/strictness")
def api_set_strictness():
    from core.runtime import set_strictness

    body = request.get_json(silent=True) or {}
    value = (body.get("strictness") or "").strip()
    if set_strictness(value):
        return ok({"strictness": value})
    return err(400, "strictness 必须是 relaxed 或 strict")


@app.get("/api/conversations")
def api_convs_list():
    from core.conversations import list_conversations

    return ok({"conversations": list_conversations()})


@app.post("/api/conversations")
def api_convs_create():
    from core.conversations import create_conversation

    return ok({"id": create_conversation()})


@app.get("/api/conversations/<cid>")
def api_convs_get(cid: str):
    from core.conversations import get_conversation

    conv = get_conversation(cid)
    return ok(conv) if conv else err(404, "会话不存在")


@app.delete("/api/conversations/<cid>")
def api_convs_delete(cid: str):
    from core.conversations import delete_conversation

    delete_conversation(cid)
    return ok()


@app.post("/api/conversations/<cid>/messages")
def api_convs_append(cid: str):
    from core.conversations import append_message

    body = request.get_json(silent=True) or {}
    role = body.get("role", "assistant")
    content = body.get("content")
    if content is None:
        return err(400, "缺少 content")
    append_message(cid, role, content)
    return ok()


@app.post("/api/conversations/<cid>")
def api_convs_update(cid: str):
    from core.conversations import update_title

    body = request.get_json(silent=True) or {}
    update_title(cid, body.get("title", ""))
    return ok()


def _maybe_warmup() -> None:
    """若配置 warmup_on_start，启动后台预热表语义（不阻塞启动）。"""
    try:
        from core.config import settings
        from core.table_semantic import warm_up

        cfg = settings().get("nl2sql", {}).get("embedding", {}) or {}
        if cfg.get("warmup_on_start", False):
            warm_up(sync=False)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    _maybe_warmup()
    print("ChatDataAgent Web 已启动: http://127.0.0.1:5003")
    # 部署时设 HOST=0.0.0.0 允许外部访问；PORT 可覆盖端口
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5003")), debug=False)
