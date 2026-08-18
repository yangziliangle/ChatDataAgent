"""CLI 问答入口：运行后可命令行提问查询数据。

用法：python main.py
示例：各品类的销售额是多少？ / 按月统计订单趋势图表 / 各城市的客户数量
输入 exit 或 quit 退出。
"""
from __future__ import annotations

import sys

from core.agent_graph import chat


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if "--warmup" in sys.argv:
        from core.table_semantic import warm_up

        print("正在预热表语义（LLM 摘要 + embedding）...")
        warm_up(sync=True)
        print("预热完成。")
        return

    print("=" * 56)
    print("  ChatDataAgent · 数据查询分析助手")
    print("  当前模式：真实 MySQL + DeepSeek")
    print("  输入 exit / quit 退出")
    print("=" * 56)

    thread_id = ""
    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见～")
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit", "退出"):
            print("感谢使用，再见～")
            break
        outcome = chat(question, thread_id=thread_id or None)
        thread_id = outcome.thread_id
        print(f"\n助手：{outcome.reply}")
        if outcome.sql:
            print(f"[SQL] {outcome.sql}")
        if outcome.table and outcome.table.get("rows"):
            cols = outcome.table["columns"]
            print(f"[表格] {len(outcome.table['rows'])} 行 | 列: {', '.join(cols)}")
        if outcome.chart:
            print(f"[图表] {outcome.chart['type']}（前端展示）")
        if outcome.active_tables:
            print(f"[关联表] {'、'.join(outcome.active_tables)}")
        if outcome.needs_clarify:
            print("[澄清] 需要你补充信息")
        if outcome.error:
            print(f"[提示] {outcome.error}")


if __name__ == "__main__":
    main()
