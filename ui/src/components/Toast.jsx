import { useEffect, useState } from 'react';

let listeners = [];

function emit(item) {
  listeners.forEach((l) => l(item));
}

/** 全局轻提示：toast('已复制', 'success' | 'error' | 'info') */
export function toast(msg, type = 'info') {
  emit({ id: Date.now() + Math.random(), msg, type });
}

export default function ToastHost() {
  const [items, setItems] = useState([]);

  useEffect(() => {
    const listener = (t) => {
      setItems((prev) => [...prev, t]);
      setTimeout(() => {
        setItems((prev) => prev.filter((x) => x.id !== t.id));
      }, 2600);
    };
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((l) => l !== listener);
    };
  }, []);

  return (
    <div className="toast-host">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.type}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}
