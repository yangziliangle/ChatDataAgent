/**
 * ChatDataAgent Node.js 网关
 *
 * 职责：
 *   1. 对外统一端口（默认 3000），转发 /api/* 到 Python 核心服务（web/app.py :5003）
 *   2. /api/health 探测 Python 核心可达性
 *   3. 生产模式托管 React 构建产物（../ui/dist）
 *
 * 启动：npm start（需先启动 Python 核心：python web/app.py）
 * 环境变量：PYTHON_CORE_URL（默认 http://127.0.0.1:5003）、PORT（默认 3000）
 */
import express from 'express';
import cors from 'cors';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT || 3000);
const PYTHON_CORE = (process.env.PYTHON_CORE_URL || 'http://127.0.0.1:5003').replace(/\/+$/, '');

const app = express();
app.use(cors());
app.use(express.json());

/** 内存滑动窗口限流（按 IP）。超限返回 429。 */
const rateBuckets = new Map();
function rateLimit(limit, windowMs) {
  return (req, res, next) => {
    const ip = (req.headers['x-forwarded-for'] || req.ip || 'unknown').split(',')[0].trim();
    const now = Date.now();
    const bucket = (rateBuckets.get(ip) || []).filter((t) => now - t < windowMs);
    if (bucket.length >= limit) {
      return res.status(429).json({ code: 429, message: '请求过于频繁，请稍后再试', data: null });
    }
    bucket.push(now);
    rateBuckets.set(ip, bucket);
    // 概率性清理过期桶，避免内存膨胀
    if (Math.random() < 0.01) {
      for (const [k, arr] of rateBuckets) {
        if (arr.filter((t) => now - t < windowMs).length === 0) rateBuckets.delete(k);
      }
    }
    next();
  };
}

/** 把请求转发到 Python 核心，透传状态码与 JSON。 */
async function proxy(req, res, targetPath) {
  try {
    const isBodyless = ['GET', 'HEAD'].includes(req.method);
    const r = await fetch(`${PYTHON_CORE}${targetPath}`, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body: isBodyless ? undefined : JSON.stringify(req.body ?? {}),
    });
    const data = await r.json().catch(() => ({}));
    res.status(r.status).json(data);
  } catch (e) {
    res.status(502).json({ error: `Python 核心服务不可达（${PYTHON_CORE}）: ${e.message}` });
  }
}

app.get('/api/health', async (_req, res) => {
  try {
    const r = await fetch(`${PYTHON_CORE}/api/health`, { method: 'GET' });
    res.json({ ok: true, core: r.ok ? 'up' : 'down' });
  } catch {
    res.json({ ok: true, core: 'down' });
  }
});

app.get('/api/tables', rateLimit(120, 60_000), (req, res) => proxy(req, res, '/api/tables'));
app.post('/api/chat', rateLimit(60, 60_000), (req, res) => proxy(req, res, '/api/chat'));
app.post('/api/chat/execute', rateLimit(60, 60_000), (req, res) => proxy(req, res, '/api/chat/execute'));
// 运行时配置
app.get('/api/config/strictness', rateLimit(60, 60_000), (req, res) => proxy(req, res, '/api/config/strictness'));
app.post('/api/config/strictness', rateLimit(60, 60_000), (req, res) => proxy(req, res, '/api/config/strictness'));
// 服务端会话
app.get('/api/conversations', rateLimit(60, 60_000), (req, res) => proxy(req, res, '/api/conversations'));
app.post('/api/conversations', rateLimit(60, 60_000), (req, res) => proxy(req, res, '/api/conversations'));
app.get('/api/conversations/:cid', rateLimit(60, 60_000), (req, res) => proxy(req, res, `/api/conversations/${req.params.cid}`));
app.post('/api/conversations/:cid', rateLimit(60, 60_000), (req, res) => proxy(req, res, `/api/conversations/${req.params.cid}`));
app.post('/api/conversations/:cid/messages', rateLimit(120, 60_000), (req, res) => proxy(req, res, `/api/conversations/${req.params.cid}/messages`));
app.delete('/api/conversations/:cid', rateLimit(60, 60_000), (req, res) => proxy(req, res, `/api/conversations/${req.params.cid}`));

// SSE 流式转发：把 Python 核心的 event-stream 原样 pipe 给前端
app.post('/api/chat/stream', rateLimit(60, 60_000), async (req, res) => {
  try {
    const r = await fetch(`${PYTHON_CORE}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body ?? {}),
    });
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(decoder.decode(value, { stream: true }));
    }
    res.end();
  } catch (e) {
    res.status(502).json({ code: 502, message: `Python 核心服务不可达: ${e.message}`, data: null });
  }
});

// 生产：托管 React 构建产物（ui/dist），访问 http://localhost:PORT
app.use(express.static(path.join(__dirname, '..', 'ui', 'dist')));

app.listen(PORT, () => {
  console.log(`ChatDataAgent 网关已启动: http://localhost:${PORT} → ${PYTHON_CORE}`);
});
