import { useEffect, useState } from 'react';
import {
  appendConversationMessage,
  chat,
  chatExecute,
  chatStream,
  createConversation,
  deleteConversation,
  getConversation,
  getStrictness,
  getTables,
  listConversations,
  setStrictness as setStrictnessApi,
  updateConversationTitle,
} from './api.js';
import { applyTheme, getInitialTheme } from './theme.js';
import ChatArea from './components/ChatArea.jsx';
import InputBar from './components/InputBar.jsx';
import Sidebar from './components/Sidebar.jsx';
import ToastHost, { toast } from './components/Toast.jsx';
import TopBar from './components/TopBar.jsx';

export default function App() {
  const [theme, setTheme] = useState(getInitialTheme());
  const [conversations, setConversations] = useState([]); // [{id,title,updatedAt}]
  const [currentId, setCurrentId] = useState(null);
  const [messages, setMessages] = useState([]); // 当前会话消息
  const [dbName, setDbName] = useState('连接中…');
  const [typing, setTyping] = useState(false);
  const [strictness, setStrictness] = useState('relaxed');

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    listConversations()
      .then((d) => setConversations(d.conversations || []))
      .catch(() => setConversations([]));
    getTables()
      .then((d) => setDbName(d && !d.error ? d.db_name || '未知' : '未连接'))
      .catch(() => setDbName('未连接'));
    getStrictness()
      .then((d) => setStrictness(d.strictness || 'relaxed'))
      .catch(() => setStrictness('relaxed'));
  }, []);

  async function newChat() {
    try {
      const d = await createConversation();
      setConversations((prev) => [{ id: d.id, title: '', updatedAt: '' }, ...prev]);
      setCurrentId(d.id);
      setMessages([]);
    } catch (e) {
      toast(`新建会话失败：${e.message}`, 'error');
    }
  }

  async function selectChat(id) {
    try {
      const d = await getConversation(id);
      if (!d) return;
      setCurrentId(id);
      setMessages((d.messages || []).map((m) => m.content));
    } catch (e) {
      toast(`加载会话失败：${e.message}`, 'error');
    }
  }

  async function deleteChat(id) {
    if (!window.confirm('确定删除该会话？')) return;
    try {
      await deleteConversation(id);
    } catch { /* ignore */ }
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (currentId === id) {
      setCurrentId(null);
      setMessages([]);
    }
    toast('会话已删除', 'success');
  }

  async function saveMsg(id, role, msg) {
    try {
      await appendConversationMessage(id, role, msg);
    } catch { /* 保存失败不阻断 */ }
  }

  async function ensureConversation(q) {
    if (currentId) return currentId;
    const d = await createConversation();
    setCurrentId(d.id);
    setConversations((prev) => [{ id: d.id, title: q.slice(0, 20), updatedAt: '' }, ...prev]);
    try { await updateConversationTitle(d.id, q.slice(0, 20)); } catch { /* ignore */ }
    return d.id;
  }

  function buildAssistant(d) {
    return {
      role: 'assistant',
      reply: d.reply || '',
      sql: d.sql || '',
      table: d.table || null,
      chart: d.chart || null,
      activeTables: d.active_tables || [],
      needsClarify: !!d.needs_clarify,
      needsReview: !!d.needs_review,
      suggestions: d.suggestions || [],
      sqlExplanation: d.sql_explanation || '',
      sqlPreview: d.sql_preview || null,
      tables: d.tables && d.tables.length ? d.tables : d.table ? [d.table] : [],
      charts: d.charts && d.charts.length ? d.charts : d.chart ? [d.chart] : [],
    };
  }

  async function handleStrictnessChange(value) {
    setStrictness(value);
    try {
      await setStrictnessApi(value);
      toast(value === 'strict' ? '已切换为严谨模式' : '已切换为宽松模式', 'success');
    } catch (e) {
      toast(`切换失败：${e.message}`, 'error');
    }
  }

  async function handleReview(sql, action) {
    if (action === 'reject') {
      toast('已拒绝该 SQL', 'info');
      return;
    }
    if (!currentId) return;
    const lastUser = [...messages].reverse().find((m) => m.role === 'user');
    const question = lastUser ? lastUser.reply : '';
    setTyping(true);
    try {
      const d = await chatExecute(question, sql, currentId);
      const full = buildAssistant(d);
      setMessages((prev) => [...prev, full]);
      saveMsg(currentId, 'assistant', full);
    } catch (e) {
      toast(`执行失败：${e.message}`, 'error');
    } finally {
      setTyping(false);
    }
  }

  async function send(question) {
    const q = (question || '').trim();
    if (!q || typing) return;
    const id = await ensureConversation(q);

    const userMsg = { role: 'user', reply: q };
    setMessages((prev) => [...prev, userMsg]);
    saveMsg(id, 'user', userMsg);

    setTyping(true);
    // 先插入占位助手消息，流式逐事件更新（打字机效果）
    const asst = { role: 'assistant', reply: '', sql: '', table: null, chart: null, activeTables: [], needsClarify: false, needsReview: false, suggestions: [], sqlExplanation: '', sqlPreview: null, tables: [], charts: [] };
    setMessages((prev) => [...prev, asst]);
    let reply = '';
    const patch = (p) => {
      Object.assign(asst, p);
      setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? { ...asst } : m)));
    };
    try {
      await chatStream(q, id, (ev) => {
        if (ev.type === 'meta') patch({ activeTables: ev.active_tables || [] });
        else if (ev.type === 'table') patch({ table: ev.table, tables: [...asst.tables, ev.table] });
        else if (ev.type === 'chart') patch({ chart: ev.chart, charts: [...asst.charts, ev.chart] });
        else if (ev.type === 'reply') {
          reply += ev.text;
          patch({ reply });
        } else if (ev.type === 'done') {
          patch({
            reply: ev.reply || reply,
            suggestions: ev.suggestions || [],
            needsClarify: false,
            sqlExplanation: ev.sql_explanation || '',
            sqlPreview: ev.sql_preview || null,
          });
        }
      });
      saveMsg(id, 'assistant', { ...asst });
    } catch (e) {
      // 流式失败：回退非流式
      try {
        const d = await chat(q, id);
        const full = buildAssistant(d);
        setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? full : m)));
        saveMsg(id, 'assistant', full);
      } catch (e2) {
        toast(`服务异常：${e2.message}`, 'error');
        const errMsg = { role: 'assistant', reply: `服务异常：${e2.message}` };
        setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? errMsg : m)));
        saveMsg(id, 'assistant', errMsg);
      }
    } finally {
      setTyping(false);
    }
  }

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentId={currentId}
        onNew={newChat}
        onSelect={selectChat}
        onDelete={deleteChat}
      />
      <div className="main">
        <TopBar
          dbName={dbName}
          theme={theme}
          onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          strictness={strictness}
          onStrictnessChange={handleStrictnessChange}
        />
        <ChatArea messages={messages} typing={typing} onPickSuggestion={send} onReview={handleReview} />
        <InputBar disabled={typing} onSend={send} />
      </div>
      <ToastHost />
    </div>
  );
}
