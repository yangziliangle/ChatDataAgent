import TableBrowser from './TableBrowser.jsx';

export default function Sidebar({ conversations, currentId, onNew, onSelect, onDelete }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="logo">📊</div>
        <span>ChatDataAgent</span>
      </div>

      <button className="new-chat-btn" onClick={onNew}>＋ 新建会话</button>

      <div className="conv-list">
        <div className="side-section-title">会话</div>
        {conversations.length === 0 && <div className="side-empty">暂无会话，开始提问吧</div>}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === currentId ? 'active' : ''}`}
            onClick={() => onSelect(c.id)}
          >
            <span className="conv-title">{c.title || '新会话'}</span>
            <button
              className="conv-del"
              title="删除会话"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(c.id);
              }}
            >
              🗑
            </button>
          </div>
        ))}
      </div>

      <div className="sidebar-bottom">
        <TableBrowser />
      </div>
    </aside>
  );
}
