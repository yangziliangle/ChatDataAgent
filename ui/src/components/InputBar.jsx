import { useState } from 'react';

export default function InputBar({ disabled, onSend }) {
  const [text, setText] = useState('');

  function submit() {
    const q = text.trim();
    if (!q || disabled) return;
    setText('');
    onSend(q);
  }

  return (
    <div className="input-bar">
      <div className="input-inner">
        <div className="input-box">
          <textarea
            value={text}
            rows={1}
            placeholder="输入你的数据问题，例如：各部门有多少员工？"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
        </div>
        <button className="send-btn" onClick={submit} disabled={disabled} title="发送">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
          </svg>
        </button>
      </div>
      <div className="input-tip">只支持数据查询，Enter 发送，Shift+Enter 换行</div>
    </div>
  );
}
