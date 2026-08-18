"""配置加载：settings.json + .env。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
ENV_PATH = CONFIG_DIR / ".env"
SETTINGS_PATH = CONFIG_DIR / "settings.json"

load_dotenv(ENV_PATH)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
DEEPSEEK_TEMPERATURE = 0.1
DEEPSEEK_MAX_TOKENS = 4096

_settings_cache: dict | None = None

# 数据源敏感字段 → 环境变量映射（env 优先于 settings.json，用于生产不落盘明文密钥）
_MYSQL_ENV_KEYS = {
    "host": "MYSQL_HOST",
    "port": "MYSQL_PORT",
    "user": "MYSQL_USER",
    "password": "MYSQL_PASSWORD",
    "database": "MYSQL_DATABASE",
    "enabled": "MYSQL_ENABLED",
}


def settings() -> dict:
    """读取（并缓存）settings.json；datasource.mysql 敏感字段可被 MYSQL_* 环境变量覆盖。"""
    global _settings_cache
    if _settings_cache is None:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            _settings_cache = json.load(f)
    mysql = _settings_cache.get("datasource", {}).get("mysql")
    if isinstance(mysql, dict):
        for key, env in _MYSQL_ENV_KEYS.items():
            val = os.getenv(env)
            if val is None or val == "":
                continue
            if env == "MYSQL_PORT":
                try:
                    mysql[key] = int(val)
                except ValueError:
                    pass
            elif env == "MYSQL_ENABLED":
                mysql[key] = val.lower() in ("1", "true", "yes", "on")
            else:
                mysql[key] = val
    return _settings_cache
