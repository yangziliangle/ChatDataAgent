"""数据源抽象接口：业务数据只读访问（NL2SQL 查询）。

提供统一的数据访问契约，真实 MySQL 实现遵循此接口。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class QueryResult:
    """结构化查询结果（供前端渲染表格）。"""

    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0


class DataSourceNotConfigured(RuntimeError):
    """真实数据源未启用/未配置时抛出。"""


class DataSource(ABC):
    """业务数据源抽象。

    所有方法只读；查询一律经过 execute_query，由上层 NL2SQL 做只读校验。
    """

    @abstractmethod
    def get_tables(self) -> list[str]:
        """返回可查询的表名列表。"""

    @abstractmethod
    def get_schema(self, table: str) -> list[dict]:
        """返回表的列信息：[{name, type, comment}, ...]"""

    def get_table_meta(self, table: str | None = None) -> dict[str, dict]:
        """返回表元数据：{表名: {"comment": str, "columns": [{name,type,comment}], "aliases": []}}。

        table=None 返回全部表。默认实现组合 get_tables + get_schema（表注释置空），
        具体实现可覆盖以获得更丰富的表注释。
        """
        tables = [table] if table else self.get_tables()
        return {
            t: {"comment": "", "columns": self.get_schema(t), "aliases": []}
            for t in tables
        }

    @abstractmethod
    def execute_query(self, sql: str) -> QueryResult:
        """执行只读 SQL，返回结构化结果。"""
