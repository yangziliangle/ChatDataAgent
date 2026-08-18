import { useEffect, useState } from 'react';
import { getTables } from '../api.js';

/** 数据源表浏览：折叠面板列出表 → 字段。 */
export default function TableBrowser() {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState({});

  useEffect(() => {
    getTables()
      .then((d) => {
        if (d && !d.error) setData(d);
        else setData({ tables: {} });
      })
      .catch(() => setData({ tables: {} }));
  }, []);

  if (!data) {
    return <div className="tb-loading">加载表结构…</div>;
  }
  const entries = Object.entries(data.tables || {});

  return (
    <div className="table-browser">
      <div className="side-section-title">数据源表（{entries.length}）</div>
      {entries.length === 0 && <div className="side-empty">暂无表</div>}
      {entries.map(([name, meta]) => (
        <div key={name} className="tb-table">
          <div
            className="tb-table-head"
            onClick={() => setOpen((o) => ({ ...o, [name]: !o[name] }))}
          >
            <span className="tb-caret">{open[name] ? '▾' : '▸'}</span>
            <span className="tb-name">{name}</span>
            {meta.comment && <span className="tb-comment">{meta.comment}</span>}
          </div>
          {open[name] && (
            <div className="tb-cols">
              {(meta.columns || []).map((c) => (
                <div key={c.name} className="tb-col">
                  <span className="tb-col-name">{c.name}</span>
                  <span className="tb-col-type">{c.type}</span>
                  {c.comment && <span className="tb-col-comment">{c.comment}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
