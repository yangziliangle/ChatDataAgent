"""数据分析：把查询结果整理为表格 + 生成 ECharts 图表数据。

用 pandas 处理查询结果（DataFrame 构造、数值化、排序截取），
chart 数据结构（前端直接用于 ECharts option）：
    {"type": "bar"|"line"|"pie",
     "x": [...],                       # 横轴/类别
     "series": [{"name": str, "data": [...]}]}
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from interfaces.datasource import QueryResult

_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_MAX_CHART_CATEGORIES = 15


def _native(v):
    """把 pandas/numpy 值转为 Python 原生类型（JSON 可序列化）。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            pass
    return v


def _to_num(v) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_str(v) -> str:
    if v is None:
        return ""
    return str(v)


class Analyzer:
    def analyze(
        self,
        result: QueryResult,
        question: str,
        force_chart: bool = False,
        chart_type: str | None = None,
    ) -> dict[str, Any]:
        """返回 {columns, rows, chart(可为 None)}。

        chart_type: 用户指定的图表类型（bar/line/pie），优先于自动判断。
        """
        if not result or not result.rows:
            return {"columns": [], "rows": [], "chart": None}

        columns = list(result.columns)
        df = pd.DataFrame(result.rows, columns=columns)

        chart = None
        if len(columns) >= 2 and (force_chart or self._chart_candidate(df)):
            chart = self._build_chart(df, columns, chart_type)

        # 转回 Python 原生值（numpy 标量 → int/float，NaN → None），保持 JSON 友好
        rows = [
            [_native(cell) for cell in row]
            for row in df.astype(object).values.tolist()
        ]
        return {"columns": columns, "rows": rows, "chart": chart}

    @staticmethod
    def _chart_candidate(df: pd.DataFrame) -> bool:
        """是否具备图表条件：至少 2 行且第二列可数值化。"""
        if len(df) < 2 or df.shape[1] < 2:
            return False
        vals = pd.to_numeric(df[df.columns[1]], errors="coerce").dropna()
        return len(vals) > 0

    @staticmethod
    def _build_chart(
        df: pd.DataFrame,
        columns: list[str],
        chart_type: str | None = None,
    ) -> dict[str, Any] | None:
        # 第一列作为类别/横轴，后续数值列作为系列
        x_col, val_cols = columns[0], columns[1:]
        head = df.head(_MAX_CHART_CATEGORIES)
        x_data = [_to_str(_native(v)) for v in head[x_col].tolist()]

        series = []
        for col in val_cols:
            vals = [_to_num(_native(v)) for v in pd.to_numeric(head[col], errors="coerce").tolist()]
            if not any(v is not None for v in vals):
                continue
            series.append({"name": col, "data": vals})

        if not series:
            return None

        # 用户指定的类型优先；否则自动判断
        if chart_type:
            ctype = chart_type
        elif x_col and _DATE_RE.match(x_col) or (x_data and all(_DATE_RE.match(str(x)) for x in x_data)):
            ctype = "line"
        elif len(x_data) <= 6:
            ctype = "pie"
        else:
            ctype = "bar"

        return {"type": ctype, "x": x_data, "series": series}
