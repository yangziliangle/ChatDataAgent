/**
 * 后端 API 封装（经 Node 网关 /api/*，dev 由 Vite proxy 转发到 :3000）。
 * 统一响应格式：{code, message, data}；code!==0 或 HTTP 非 2xx 抛 Error(message)。
 */

async function request(method, url, data) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (data !== undefined) opts.body = JSON.stringify(data);

  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error('网络错误，请检查服务是否已启动');
  }

  let payload;
  try {
    payload = await res.json();
  } catch {
    throw new Error(`请求失败（HTTP ${res.status}）`);
  }
  if (!res.ok || payload.code !== 0) {
    throw new Error(payload.message || `请求失败（HTTP ${res.status}）`);
  }
  return payload.data;
}

export function getTables() {
  return request('GET', '/api/tables');
}

export function chat(question, threadId = '') {
  return request('POST', '/api/chat', { question, thread_id: threadId });
}

/** 执行审核通过的 SQL。 */
export function chatExecute(question, sql, threadId = '') {
  return request('POST', '/api/chat/execute', { question, sql, thread_id: threadId });
}

// ===== 运行时配置 =====
export function getStrictness() {
  return request('GET', '/api/config/strictness');
}
export function setStrictness(value) {
  return request('POST', '/api/config/strictness', { strictness: value });
}

/** SSE 流式问答：onEvent 逐个收到 {type:'meta'|'table'|'chart'|'reply'|'done'}。 */
export async function chatStream(question, threadId, onEvent) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, thread_id: threadId }),
  });
  if (!res.ok) {
    let msg = `请求失败（HTTP ${res.status}）`;
    try {
      const p = await res.json();
      if (p.message) msg = p.message;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop();
    for (const part of parts) {
      const line = part.trim();
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch { /* 忽略坏事件 */ }
      }
    }
  }
}

// ===== 服务端会话 =====
export function listConversations() {
  return request('GET', '/api/conversations');
}
export function createConversation() {
  return request('POST', '/api/conversations');
}
export function getConversation(id) {
  return request('GET', `/api/conversations/${id}`);
}
export function deleteConversation(id) {
  return request('DELETE', `/api/conversations/${id}`);
}
export function appendConversationMessage(id, role, content) {
  return request('POST', `/api/conversations/${id}/messages`, { role, content });
}
export function updateConversationTitle(id, title) {
  return request('POST', `/api/conversations/${id}`, { title });
}
