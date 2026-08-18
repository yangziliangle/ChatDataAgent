const SUGGESTIONS = ['有哪些表？', '各部门有多少员工？', '平均薪资是多少？', '员工的性别分布'];

export default function SuggestionChips({ onPick }) {
  return (
    <div className="w-suggest">
      {SUGGESTIONS.map((s) => (
        <div key={s} className="chip" onClick={() => onPick(s)}>
          {s}
        </div>
      ))}
    </div>
  );
}
