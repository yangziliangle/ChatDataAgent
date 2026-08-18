"""ChatDataAgent 验收测试：只读安全校验、真实 MySQL、图表生成、端到端 Agent。

本项目必须连接真实 MySQL + DeepSeek，无模拟数据源。
运行：conda activate langchain1.2 && python tests/test_dialogs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 兼容 Windows GBK 控制台
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 允许从项目根导入包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent_graph import chat  # noqa: E402
from core.analyzer import Analyzer  # noqa: E402
from core.sql_gen import SQLGenerator, SQLValidationError  # noqa: E402
from interfaces import get_datasource  # noqa: E402
from interfaces.datasource import QueryResult  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_validate():
    print("1) 只读安全校验")
    gen = SQLGenerator()
    ok = gen.validate("SELECT category, SUM(amount) FROM sales_order GROUP BY category")
    check("合法 SELECT 通过", ok.upper().startswith("SELECT"), ok)
    for bad in ["DELETE FROM sales_order", "UPDATE sales_order SET amount=0", "DROP TABLE sales_order", "SELECT * FROM a; SELECT 1"]:
        try:
            gen.validate(bad)
            check(f"拦截危险语句: {bad.split()[0].upper()}", False, "未拦截")
        except SQLValidationError:
            check(f"拦截危险语句: {bad.split()[0].upper()}", True)


def test_mysql():
    print("2) 真实 MySQL 数据源")
    ds = get_datasource()
    tables = ds.get_tables()
    check("获取到数据表", len(tables) > 0, str(tables))
    if tables:
        schema = ds.get_schema(tables[0])
        check("获取表结构", len(schema) > 0, str(schema[:1]))
        meta = ds.get_table_meta()
        check("获取表元数据", isinstance(meta, dict) and len(meta) > 0, str(list(meta)[:3]))
        if meta:
            first = list(meta)[0]
            check(
                "元数据含 comment/columns",
                "comment" in meta[first] and len(meta[first].get("columns", [])) > 0,
                str(meta[first].keys()),
            )
        r = ds.execute_query(f"SELECT * FROM `{tables[0]}` LIMIT 3")
        check("执行查询返回行", r.row_count > 0, str(r.row_count))
        check("查询有列名", len(r.columns) > 0, str(r.columns))


def test_analyzer():
    print("3) 图表数据生成")
    an = Analyzer()
    r = QueryResult(columns=["category", "total_amount"], rows=[["手机", 100], ["电脑", 200], ["家电", 300]])
    out = an.analyze(r, "各品类销售额", force_chart=True, chart_type="bar")
    check("生成柱状图", out["chart"] is not None and out["chart"]["type"] == "bar", str(out["chart"]))
    out2 = an.analyze(r, "各品类销售额", force_chart=True, chart_type="pie")
    check("生成饼图", out2["chart"]["type"] == "pie", str(out2["chart"]))
    out3 = an.analyze(r, "用户行为漏斗", force_chart=True, chart_type="funnel")
    check("生成漏斗图", out3["chart"]["type"] == "funnel", str(out3["chart"]))


def test_agent_chat():
    print("4) 端到端 Agent 对话（真实 MySQL + DeepSeek）")
    out = chat("有哪些表？")
    check("元数据查询", out.intent == "meta" and bool(out.table), out.intent)
    out2 = chat("各部门有多少员工？")
    check("数据查询", bool(out2.sql) and bool(out2.table and out2.table.get("rows")), out2.sql)
    out3 = chat("删除 id=1 的员工")
    check("写操作拒绝", out3.intent == "deny", out3.intent)
    # 多轮表上下文继承（同一 thread，需真实环境）
    first_tables = set(out2.active_tables)
    out4 = chat("那平均薪资呢？", thread_id=out2.thread_id)
    check(
        "多轮追问继承上轮表",
        bool(out4.active_tables) and set(out4.active_tables) == first_tables,
        str((first_tables, out4.active_tables)),
    )


def test_mysql_required():
    print("5) MySQL 必须配置")
    from interfaces.datasource import DataSourceNotConfigured  # noqa: PLC0415
    from impl.real.datasource import MySQLDataSource  # noqa: PLC0415

    real = MySQLDataSource({"enabled": False})
    try:
        real.get_tables()
        check("未启用 MySQL 抛友好错误", False, "未抛错")
    except DataSourceNotConfigured:
        check("未启用 MySQL 抛友好错误", True)


def main():
    print("=" * 50)
    print("ChatDataAgent 验收测试")
    print("=" * 50)
    test_validate()
    test_mysql()
    test_analyzer()
    test_agent_chat()
    test_mysql_required()
    print("=" * 50)
    print(f"结果：通过 {PASS} 条，失败 {FAIL} 条")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
