"""意图识别：按关键词把用户输入分为 query_data / chart / meta / chat / deny。

独立成模块，供 agent_graph 与 clarify（合并策略）共用，避免循环导入。
"""
from __future__ import annotations

_QUERY_KEYWORDS = (
    "多少", "统计", "汇总", "占比", "趋势", "列表", "列出", "展示", "显示",
    "查询", "查一下", "哪些", "最", "top", "数量", "金额", "销售额", "订单",
    "客户", "商品", "销量", "收入", "库存", "员工", "薪资", "工资", "部门",
    "性别", "男的", "女的", "男女", "是男", "是女", "分别",
)
_CHART_KEYWORDS = ("图表", "柱状", "柱形", "饼图", "折线", "条形", "可视化", "画图", "图")
_META_KEYWORDS = (
    "哪些表", "那些表", "什么表", "有什么表", "几张表", "都有哪些表", "表结构",
    "有哪些字段", "表的字段", "表名", "有哪些列", "表是干什么", "表是干嘛",
    "表是啥", "些表", "有哪些数据库", "database",
)


_CHAT_KEYWORDS = (
    "你好", "您好", "谢谢", "感谢", "再见", "拜拜", "你是谁", "在吗",
    "hello", "hi", "哈哈", "哈哈哈", "做什么的",
)
# 写操作关键词：命中即明确拒绝（系统只读）
_WRITE_KEYWORDS = (
    "删除", "删掉", "删了", "删", "修改", "更新", "插入", "新增",
    "添加", "写入", "清空", "去掉", "删除掉", "delete", "update",
    "insert", "drop", "alter", "改成", "改一下",
)


def _extract_chart_type(text: str) -> str | None:
    """提取用户指定的图表类型：funnel / bar / line / pie。"""
    t = text.lower()
    if any(k in t for k in ("漏斗", "funnel")):
        return "funnel"
    if any(k in t for k in ("柱状", "柱形", "条形", "bar")):
        return "bar"
    if any(k in t for k in ("折线", "趋势线", "line")):
        return "line"
    if any(k in t for k in ("饼状", "饼图", "pie")):
        return "pie"
    return None


def detect_intent(text: str) -> tuple[str, bool, str | None]:
    t = text.lower()
    if any(k in t for k in _WRITE_KEYWORDS):
        return "deny", False, None
    if any(k in t for k in _META_KEYWORDS):
        return "meta", False, None
    chart_type = _extract_chart_type(t)
    if chart_type or any(k in t for k in _CHART_KEYWORDS):
        return "chart", True, chart_type
    if any(k in t for k in _CHAT_KEYWORDS):
        return "chat", False, None
    # 默认：只要不明确是闲聊，都按数据查询处理；默认需要图表（类型由 analyzer 自动判断）
    return "query_data", True, None
