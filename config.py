"""全局配置：路径与常量。

所有业务数据（分类、优先级、状态等）都存放在数据库中，这里只放
与数据无关的运行配置。路径可通过环境变量 RPM_DB_PATH 覆盖（测试使用）。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_DIR = DATA_DIR / "database"
IMPORTS_DIR = DATA_DIR / "imports"
EXPORTS_DIR = DATA_DIR / "exports"
BACKUPS_DIR = BASE_DIR / "backups"

DEFAULT_DB_PATH = DATABASE_DIR / "research_manager.db"


def get_db_path() -> Path:
    """返回当前使用的数据库路径（环境变量优先）。"""
    env = os.environ.get("RPM_DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH


def get_backups_dir() -> Path:
    env = os.environ.get("RPM_BACKUPS_DIR")
    return Path(env) if env else BACKUPS_DIR


APP_TITLE = "科研项目管理系统"
APP_ICON = "📋"

# 列表分页默认每页条数
DEFAULT_PAGE_SIZE = 50

# 自动备份：距上次备份超过该小时数时，启动应用会自动备份一次
AUTO_BACKUP_INTERVAL_HOURS = 24
# 自动备份保留份数（手动备份不受此限制）
AUTO_BACKUP_KEEP = 20

# 界面主色（仅用于样式，不承载业务含义）
COLOR_PRIMARY = "#2563EB"
COLOR_DANGER = "#DC2626"
COLOR_SUCCESS = "#16A34A"
COLOR_WARNING = "#EA580C"
COLOR_MUTED = "#6B7280"


def is_demo() -> bool:
    """云端演示模式：环境变量 RPM_DEMO=1（Streamlit Community Cloud 中在 Secrets 里写 RPM_DEMO = "1"，
    顶层密钥会同时作为环境变量提供）。本地不设置即为正常模式。"""
    return os.environ.get("RPM_DEMO", "").strip().lower() in ("1", "true", "yes", "on")
