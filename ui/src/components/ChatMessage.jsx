import ReactMarkdown from 'react-markdown';
import ChartCard from './ChartCard.jsx';
import TableCard from './TableCard.jsx';

export default function ChatMessage({ msg, onPickSuggestion, onReview }) {
  const { role, reply, sql, table, chart, activeTables, needsClarify, needsReview, suggestions, sqlExplanation, sqlPreview, tables, charts } = msg;
  return (
    <div className={`msg ${role}`}>
      <div className="avatar">{role === 'user' ? '👤' : '🤖'}</div>
      <div className="content">
        {role === 'user' ? (
          <div className="bubble">{reply}</div>
        ) : (
          <div className="bubble">
            <ReactMarkdown>{reply}</ReactMarkdown>
          </div>
        )}
        {needsClarify && <div className="sql-tag">需要你补充信息</div>}
        {sql && (
          <div className="sql-tag">
            <span>SQL: {sql}</span>
            <button
              className="copy-btn"
              title="复制 SQL"
              onClick={() => navigator.clipboard.writeText(sql)}
            >
              📋 复制
            </button>
          </div>
        )}
        {needsReview && onReview && (
          <div className="review-card">
            {sqlExplanation && <div className="review-explanation">🔍 {sqlExplanation}</div>}
            {sqlPreview && sqlPreview.rows && sqlPreview.rows.length > 0 && (
              <div className="review-preview">
                <div className="tc-title">数据预览（前 {sqlPreview.rows.length} 行）</div>
                <div className="rp-wrap">
                  <table className="review-table">
                    <thead>
                      <tr>{(sqlPreview.columns || []).map((c, i) => <th key={i}>{String(c ?? '')}</th>)}</tr>
                    </thead>
                    <tbody>
                      {sqlPreview.rows.map((r, i) => (
                        <tr key={i}>{r.map((v, j) => <td key={j}>{String(v ?? '')}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {sql && (
              <div className="sql-tag">
                <span>SQL: {sql}</span>
                <button className="copy-btn" title="复制 SQL" onClick={() => navigator.clipboard.writeText(sql)}>📋 复制</button>
              </div>
            )}
            <div className="review-bar">
              <span className="review-label">SQL 待审核</span>
              <button className="review-btn approve" onClick={() => onReview(sql, 'approve')}>✅ 执行</button>
              <button className="review-btn reject" onClick={() => onReview(sql, 'reject')}>❌ 拒绝</button>
            </div>
          </div>
        )}
        {activeTables && activeTables.length > 0 && (
          <div className="sql-tag">关联表：{activeTables.join('、')}</div>
        )}
        {role === 'assistant' && suggestions && suggestions.length > 0 && (
          <div className="suggest-chips">
            <span className="suggest-label">可以继续问：</span>
            {suggestions.map((s) => (
              <button key={s} className="suggest-chip" onClick={() => onPickSuggestion(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {(tables && tables.length ? tables : table ? [table] : []).map((t, i) =>
          t && t.rows && t.rows.length > 0 ? <TableCard key={`t${i}`} table={t} /> : null,
        )}
        {(charts && charts.length ? charts : chart ? [chart] : []).map((c, i) =>
          c ? <ChartCard key={`c${i}`} chart={c} /> : null,
        )}
      </div>
    </div>
  );
}
