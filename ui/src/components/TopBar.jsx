export default function TopBar({ dbName, theme, onToggleTheme, strictness, onStrictnessChange }) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <span className="topbar-title">📊 ChatDataAgent</span>
        <span className="topbar-sub">数据问数助手</span>
      </div>
      <div className="topbar-right">
        <div className="ds-badge">
          <span className="dot" />
          数据源：<span className="db">{dbName}</span>
        </div>
        <div className="strictness-toggle" title="查询严谨度：宽松=口径不明用默认口径执行；严谨=先反问确认">
          <button
            className={`s-chip ${strictness === 'relaxed' ? 'active' : ''}`}
            onClick={() => onStrictnessChange('relaxed')}
          >
            宽松
          </button>
          <button
            className={`s-chip ${strictness === 'strict' ? 'active' : ''}`}
            onClick={() => onStrictnessChange('strict')}
          >
            严谨
          </button>
        </div>
        <button className="theme-toggle" onClick={onToggleTheme} title="切换明暗主题">
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      </div>
    </header>
  );
}
