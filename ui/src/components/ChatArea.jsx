import { useEffect, useRef } from 'react';
import ChatMessage from './ChatMessage.jsx';
import SuggestionChips from './SuggestionChips.jsx';

export default function ChatArea({ messages, typing, onPickSuggestion, onReview }) {
  const chatAreaRef = useRef(null);

  useEffect(() => {
    if (chatAreaRef.current) {
      chatAreaRef.current.scrollTop = chatAreaRef.current.scrollHeight;
    }
  }, [messages, typing]);

  return (
    <main className="chat-area" ref={chatAreaRef}>
      <div className="chat-inner">
        {messages.length === 0 && (
          <div className="welcome">
            <div className="w-logo">📊</div>
            <h1>数据问数助手</h1>
            <p>用自然语言提问，自动查数据库并生成分析图表</p>
            <div className="w-caps">
              <span>NL2SQL 查询</span>
              <span>图表分析</span>
              <span>多轮追问</span>
            </div>
            <SuggestionChips onPick={onPickSuggestion} />
          </div>
        )}
        {messages.map((m, i) => (
          <ChatMessage key={i} msg={m} onPickSuggestion={onPickSuggestion} onReview={onReview} />
        ))}
        {typing && (
          <div className="msg assistant">
            <div className="avatar">🤖</div>
            <div className="content">
              <div className="bubble typing">正在分析</div>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
