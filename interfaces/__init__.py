"""接口层：统一出口，供外部获取数据源实例。

本项目必须连接真实数据库（MySQL），不提供模拟数据源。
"""
from __future__ import annotations

from interfaces.datasource import DataSource, DataSourceNotConfigured, QueryResult


def get_datasource() -> DataSource:
    """返回真实 MySQL 数据源。

    必须在 config/settings.json 中配置 datasource.mysql 并 enabled=true，
    否则抛 DataSourceNotConfigured 友好提示。
    """
    from core.config import settings
    from impl.real.datasource import MySQLDataSource

    return MySQLDataSource(settings()["datasource"]["mysql"])


__all__ = ["DataSource", "DataSourceNotConfigured", "QueryResult", "get_datasource"]
