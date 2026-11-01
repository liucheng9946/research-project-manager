"""数据访问层：连接管理、事务、初始化与版本化迁移。

所有页面与服务只能通过本模块获取连接：
    with transaction() as conn:   # 写操作，异常自动回滚
        ...
    with read_conn() as conn:     # 只读操作
        ...
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

import config
from core import schema


class DatabaseError(Exception):
    """带中文说明的数据库错误，界面层直接展示 str(e)。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def new_id() -> str:
    return str(uuid.uuid4())


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """打开连接：启用外键、Row 工厂、显式事务模式（isolation_level=None）。"""
    path = Path(db_path) if db_path else config.get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    except sqlite3.Error as e:  # pragma: no cover - 极少发生
        raise DatabaseError(f"无法打开数据库文件：{path}（{e}）") from e
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def transaction(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """写事务：BEGIN IMMEDIATE … COMMIT；任何异常都会 ROLLBACK 并继续抛出。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def read_conn(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 版本与健康检查
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def get_schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_meta"):
        return 0
    r = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(r["value"]) if r else 0


def check_database(db_path: str | Path | None = None) -> dict:
    """检查数据库状态，不做任何修改。

    返回 {"exists", "ok", "version", "latest", "message"}。
    """
    path = Path(db_path) if db_path else config.get_db_path()
    info = {"path": str(path), "exists": path.exists(), "ok": False,
            "version": 0, "latest": schema.SCHEMA_VERSION, "message": ""}
    if not path.exists():
        info["message"] = "数据库文件不存在，将自动初始化。"
        return info
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            res = conn.execute("PRAGMA quick_check").fetchone()[0]
            if res != "ok":
                info["message"] = f"数据库完整性检查失败：{res}"
                return info
            info["version"] = get_schema_version(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        info["message"] = f"数据库文件无法读取，可能已损坏或不是 SQLite 文件：{e}"
        return info
    if info["version"] > schema.SCHEMA_VERSION:
        info["message"] = (f"数据库版本（{info['version']}）高于程序支持的版本"
                           f"（{schema.SCHEMA_VERSION}），请升级程序。")
        return info
    info["ok"] = True
    info["message"] = "正常"
    return info


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------
def _split_sql(script: str) -> list[str]:
    """按语句拆分迁移脚本（迁移脚本中不允许在字符串内出现分号）。"""
    parts, buf = [], []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _snapshot(conn: sqlite3.Connection) -> dict:
    """迁移前后用于核对的关键数据摘要。"""
    if not _table_exists(conn, "tasks"):
        return {"tasks": 0, "codes": []}
    n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    codes = [r[0] for r in conn.execute(
        "SELECT id || '|' || IFNULL(task_code,'') || '|' || title || '|' || progress "
        "FROM tasks ORDER BY id")]
    return {"tasks": n, "codes": codes}


def ensure_v2_defaults(conn: sqlite3.Connection) -> None:
    """V1.1 默认值：文件类型初始列表；把旧版进展记录（task_comments）复制为工作记录。
    可重复执行（幂等），也用于恢复旧版本 JSON 备份之后。"""
    ts = now_str()
    for i, (name, exts) in enumerate(schema.DEFAULT_ATTACHMENT_TYPES):
        conn.execute("INSERT OR IGNORE INTO attachment_types(id, name, extensions, sort_order, "
                     "created_at, updated_at) VALUES(?,?,?,?,?,?)", (new_id(), name, exts, i, ts, ts))
    conn.execute("INSERT OR IGNORE INTO task_activity_logs(id, task_id, activity_type, content, "
                 "created_at, updated_at) SELECT id, task_id, 'note', content, created_at, created_at "
                 "FROM task_comments")


# 迁移后的数据修复/默认值钩子：版本号 -> 函数(conn)
POST_MIGRATION_HOOKS: dict[int, Callable[[sqlite3.Connection], None]] = {2: ensure_v2_defaults}


def pending_migrations(conn: sqlite3.Connection) -> list[int]:
    v = get_schema_version(conn)
    return [k for k in sorted(schema.MIGRATIONS) if k > v]


def migrate(db_path: str | Path | None = None,
            backup_func: Callable[[Path], Path | None] | None = None,
            target_version: int | None = None) -> dict:
    """执行尚未执行的迁移。

    * 已存在且有数据的数据库在迁移前先调用 backup_func 备份；
    * 全部待执行迁移在同一事务中完成，任何一步失败整体回滚；
    * 迁移后核对原有任务数量与关键字段，不一致则回滚。
    """
    path = Path(db_path) if db_path else config.get_db_path()
    existed = path.exists() and path.stat().st_size > 0
    conn = connect(path)
    result = {"from": 0, "to": 0, "applied": [], "backup": None}
    try:
        current = get_schema_version(conn)
        result["from"] = result["to"] = current
        todo = [k for k in sorted(schema.MIGRATIONS) if k > current
                and (target_version is None or k <= target_version)]
        if not todo:
            return result
        if existed and current > 0 and backup_func is not None:
            conn.close()
            result["backup"] = str(backup_func(path))
            conn = connect(path)
        before = _snapshot(conn)
        conn.execute("PRAGMA foreign_keys = OFF")  # 迁移期间临时关闭，结束后校验
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)")
            for ver in todo:
                for stmt in _split_sql(schema.MIGRATIONS[ver]):
                    conn.execute(stmt)
                hook = POST_MIGRATION_HOOKS.get(ver)
                if hook:
                    hook(conn)
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(ver),))
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"migrated_v{ver}_at", now_str()))
                result["applied"].append(ver)
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise DatabaseError(f"迁移后外键检查失败（{len(fk)} 处），已回滚。")
            after = _snapshot(conn)
            if after != before:
                raise DatabaseError(
                    f"迁移后任务数据核对不一致（迁移前 {before['tasks']} 项，"
                    f"迁移后 {after['tasks']} 项），已回滚。")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        result["to"] = todo[-1]
        return result
    finally:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            pass
        conn.close()


def init_database(db_path: str | Path | None = None,
                  backup_func: Callable[[Path], Path | None] | None = None) -> dict:
    """确保数据库存在且为最新版本，并写入全局默认设置。可重复调用（幂等）。"""
    path = Path(db_path) if db_path else config.get_db_path()
    info = check_database(path)
    if info["exists"] and not info["ok"]:
        raise DatabaseError(info["message"])
    result = migrate(path, backup_func=backup_func)
    with transaction(path) as conn:
        for k, v in schema.DEFAULT_GLOBAL_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO app_settings(key, value, updated_at) VALUES(?,?,?)",
                (k, json.dumps(v, ensure_ascii=False), now_str()))
    return result


# ---------------------------------------------------------------------------
# 设置读写（全局 / 项目分开存储）
# ---------------------------------------------------------------------------
def get_global_setting(conn: sqlite3.Connection, key: str, default=None):
    r = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    if r is None or r["value"] is None:
        return schema.DEFAULT_GLOBAL_SETTINGS.get(key, default)
    try:
        return json.loads(r["value"])
    except (TypeError, ValueError):
        return r["value"]


def set_global_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), now_str()))


def get_project_setting(conn: sqlite3.Connection, project_id: str, key: str, default=None):
    r = conn.execute("SELECT value FROM project_settings WHERE project_id=? AND key=?",
                     (project_id, key)).fetchone()
    if r is None or r["value"] is None:
        return schema.DEFAULT_PROJECT_SETTINGS.get(key, default)
    try:
        return json.loads(r["value"])
    except (TypeError, ValueError):
        return r["value"]


def get_project_settings(conn: sqlite3.Connection, project_id: str) -> dict:
    out = dict(schema.DEFAULT_PROJECT_SETTINGS)
    for r in conn.execute("SELECT key, value FROM project_settings WHERE project_id=?",
                          (project_id,)):
        try:
            out[r["key"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            out[r["key"]] = r["value"]
    return out


def set_project_setting(conn: sqlite3.Connection, project_id: str, key: str, value) -> None:
    conn.execute(
        "INSERT INTO project_settings(project_id, key, value, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(project_id, key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (project_id, key, json.dumps(value, ensure_ascii=False), now_str()))
