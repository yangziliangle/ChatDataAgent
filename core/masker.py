"""PII 脱敏：对查询结果中敏感列（手机号/身份证/邮箱/密码等）自动遮罩。"""
from __future__ import annotations

_SENSITIVE_KEYWORDS = (
    "phone", "mobile", "tel", "telephone", "身份证", "idcard", "id_card",
    "email", "邮箱", "password", "pwd", "密码", "银行卡", "credit", "account", "手机",
)


def _is_sensitive(col: str) -> bool:
    """列名是否命中敏感关键词（小写匹配）。"""
    c = (col or "").lower()
    return any(k in c for k in _SENSITIVE_KEYWORDS)


def mask_value(v) -> str:
    """字符串打码：长度≥7 保留前3后2（如 138****1234）；否则全 *。"""
    s = str(v)
    n = len(s)
    if n <= 4:
        return "*" * n
    if n >= 7:
        return s[:3] + "*" * (n - 5) + s[-2:]
    return s[0] + "*" * (n - 2) + s[-1]


def mask_analysis(analysis: dict | None) -> dict | None:
    """对分析结果的 rows 中敏感列遮罩；无敏感列原样返回。"""
    if not analysis:
        return analysis
    columns = analysis.get("columns", []) or []
    rows = analysis.get("rows", []) or []
    sensitive = [i for i, c in enumerate(columns) if _is_sensitive(str(c))]
    if not sensitive or not rows:
        return analysis
    for r in rows:
        for i in sensitive:
            if i < len(r) and r[i] is not None and r[i] != "":
                r[i] = mask_value(r[i])
    return analysis
