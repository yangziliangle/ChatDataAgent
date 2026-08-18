"""MySQL 真实数据源：通过 pymysql 连接业务数据库，执行只读查询。

遵循 interfaces.datasource.DataSource 契约，供 NL2SQL Agent 使用。
安全：连接串参数来自 settings.json；表名做反引号转义；SQL 执行前由上层 sql_gen 做只读校验。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pymysql

from interfaces.datasource import DataSource, DataSourceNotConfigured, QueryResult


def _json_safe(v):
    """把数据库值转换为 JSON 可序列化类型（Decimal / datetime / bytes）。"""
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f == int(f) else f
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.decode(errors="replace")
    return v


class MySQLDataSource(DataSource):
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        self._enabled = bool(self._cfg.get("enabled", False))
        self._conn: pymysql.connections.Connection | None = None

    # ---------- 连接管理 ----------

    @staticmethod
    def _cfg_ints() -> dict:
        try:
            from core.config import settings

            nl2sql = settings().get("nl2sql", {}) or {}
            return {
                "max_rows": int(nl2sql.get("max_rows", 500) or 0),
                "max_execution_time_ms": int(nl2sql.get("max_execution_time_ms", 10000) or 0),
            }
        except Exception:  # noqa: BLE001
            return {"max_rows": 500, "max_execution_time_ms": 10000}

    def _ensure_connection(self) -> None:
        if not self._enabled:
            raise DataSourceNotConfigured(
                "MySQL 数据源未启用：请在 config/settings.json 中设置 datasource.mysql.enabled=true"
            )
        if self._conn is None:
            database = self._cfg.get("database") or None
            try:
                self._conn = pymysql.connect(
                    host=self._cfg.get("host", "localhost"),
                    port=int(self._cfg.get("port", 3306)),
                    user=self._cfg.get("user", ""),
                    password=self._cfg.get("password", ""),
                    database=database,
                    charset="utf8mb4",
                    connect_timeout=5,
                    cursorclass=pymysql.cursors.DictCursor,
                )
            except Exception as e:  # noqa: BLE001
                raise DataSourceNotConfigured(f"MySQL 连接失败：{e}")
            # 查询超时保护（MAX_EXECUTION_TIME 仅对 SELECT 生效；旧版本不支持则忽略）
            ms = self._cfg_ints()["max_execution_time_ms"]
            if ms > 0:
                try:
                    with self._conn.cursor() as cur:
                        cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {ms}")
                except Exception:  # noqa: BLE001 —— 不支持该变量不阻断
                    pass

    def _quote_table(self, table: str) -> str:
        """表名安全转义（去除反引号，防止注入）。"""
        safe = table.replace("`", "").strip()
        if not safe:
            raise ValueError("非法表名")
        return f"`{safe}`"

    # ---------- 契约实现 ----------

    def get_tables(self) -> list[str]:
        self._ensure_connection()
        with self._conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            rows = cur.fetchall()
            return [list(r.values())[0] for r in rows]

    def get_schema(self, table: str) -> list[dict]:
        self._ensure_connection()
        q = f"SHOW FULL COLUMNS FROM {self._quote_table(table)}"
        with self._conn.cursor() as cur:
            cur.execute(q)
            rows = cur.fetchall()
            return [
                {
                    "name": r.get("Field", ""),
                    "type": r.get("Type", ""),
                    "comment": r.get("Comment") or "",
                }
                for r in rows
            ]

    def get_table_meta(self, table: str | None = None) -> dict[str, dict]:
        """一次查询返回全部（或单张）表元数据：表名 + 表注释 + 列 + 列注释。"""
        self._ensure_connection()
        sql = (
            "SELECT t.TABLE_NAME, t.TABLE_COMMENT, c.COLUMN_NAME, "
            "c.COLUMN_TYPE, c.COLUMN_COMMENT "
            "FROM information_schema.TABLES t "
            "LEFT JOIN information_schema.COLUMNS c "
            "  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME "
            "WHERE t.TABLE_SCHEMA = DATABASE()"
            + (" AND t.TABLE_NAME = %s" if table else "")
            + " ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION"
        )
        with self._conn.cursor() as cur:
            if table:
                cur.execute(sql, (table,))
            else:
                cur.execute(sql)
            rows = cur.fetchall()
        meta: dict[str, dict] = {}
        for r in rows:
            tname = r.get("TABLE_NAME", "")
            entry = meta.setdefault(
                tname,
                {"comment": r.get("TABLE_COMMENT") or "", "columns": [], "aliases": []},
            )
            if r.get("COLUMN_NAME") is not None:
                entry["columns"].append(
                    {
                        "name": r.get("COLUMN_NAME", ""),
                        "type": r.get("COLUMN_TYPE", ""),
                        "comment": r.get("COLUMN_COMMENT") or "",
                    }
                )
        return meta

    def execute_query(self, sql: str) -> QueryResult:
        self._ensure_connection()
        max_rows = self._cfg_ints()["max_rows"]
        with self._conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            if not rows:
                return QueryResult(columns=[], rows=[], row_count=0)
            columns = list(rows[0].keys())
            # 值统一转 JSON 安全类型（SUM/AVG 返回 Decimal、DATETIME 等）
            data = [[_json_safe(r.get(c)) for c in columns] for r in rows]
            # 结果行数上限（防全表拉爆）；row_count 保留真实行数
            if max_rows > 0 and len(data) > max_rows:
                data = data[:max_rows]
            return QueryResult(columns=columns, rows=data, row_count=len(rows))

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
