export default function TableCard({ table }) {
  const { columns = [], rows = [] } = table;
  return (
    <div className="table-card">
      <div className="tc-title">查询结果（{rows.length} 行）</div>
      <div className="tc-wrap">
        <table>
          <thead>
            <tr>{columns.map((c, i) => <th key={i}>{String(c ?? '')}</th>)}</tr>
          </thead>
          <tbody>
            {rows.slice(0, 50).map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j}>{String(v ?? '')}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
