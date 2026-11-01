"""项目管理：创建（空白 / 默认模板 / 复制配置）、修改、删除、当前项目切换。"""
from __future__ import annotations

import sqlite3

from core import schema
from core.database import (DatabaseError, get_global_setting, new_id, now_str,
                           rows_to_dicts, set_global_setting)
from utils.validators import ValidationError, optional_text, require_text

# 默认模板（通用科研项目）：不假定项目具有论文章节结构
TEMPLATE_CATEGORIES = [
    ("文献调研", "#0EA5E9"),
    ("方法设计", "#2563EB"),
    ("实验与数据", "#16A34A"),
    ("论文写作", "#7C3AED"),
    ("成果发布", "#EA580C"),
]

CREATE_MODES = {
    "blank": "空白项目（仅建立默认优先级与状态，分类、章节、标签由你自行配置）",
    "template": "默认模板（通用科研分类 + 默认优先级、状态、标签，不含章节）",
    "copy": "基于现有项目（复制分类、章节、优先级、状态、标签、负责人与项目设置，不复制任务）",
}


def list_projects(conn: sqlite3.Connection, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM projects"
    if not include_inactive:
        sql += " WHERE is_active=1"
    return rows_to_dicts(conn.execute(sql + " ORDER BY created_at, name"))


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(r) if r else None


def get_project_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    r = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def _seed_priorities_statuses(conn, pid: str, ts: str) -> None:
    for i, (name, desc, color) in enumerate(schema.DEFAULT_PRIORITIES):
        conn.execute(
            "INSERT INTO priorities(id, project_id, name, description, color, sort_order, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id(), pid, name, desc, color, i, ts, ts))
    for i, (name, kind, color, is_def) in enumerate(schema.DEFAULT_STATUSES):
        conn.execute(
            "INSERT INTO statuses(id, project_id, name, kind, color, sort_order, is_default, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id(), pid, name, kind, color, i, is_def, ts, ts))


def _seed_tags(conn, pid: str, ts: str) -> None:
    for i, name in enumerate(schema.DEFAULT_TAGS):
        conn.execute(
            "INSERT INTO tags(id, project_id, name, color, sort_order, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)", (new_id(), pid, name, "#6B7280", i, ts, ts))


_COPY_TABLES = {
    "categories": ["name", "description", "color", "sort_order"],
    "chapters": ["name", "group_name", "description", "sort_order"],
    "priorities": ["name", "description", "color", "sort_order"],
    "statuses": ["name", "kind", "color", "sort_order", "is_default"],
    "tags": ["name", "color", "sort_order"],
    "people": ["name", "description", "sort_order"],
}


def _copy_config(conn, src: str, dst: str, ts: str) -> None:
    """复制项目配置：逐条创建新记录（新 UUID），绝不共享源记录。"""
    for table, cols in _COPY_TABLES.items():
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE project_id=?", (src,)).fetchall()
        for r in rows:
            conn.execute(
                f"INSERT INTO {table}(id, project_id, {', '.join(cols)}, created_at, updated_at) "
                f"VALUES(?, ?, {', '.join('?' * len(cols))}, ?, ?)",
                (new_id(), dst, *[r[c] for c in cols], ts, ts))
    conn.execute(
        "INSERT INTO project_settings(project_id, key, value, updated_at) "
        "SELECT ?, key, value, ? FROM project_settings WHERE project_id=?", (dst, ts, src))


def create_project(conn: sqlite3.Connection, name: str, description: str | None = None,
                   mode: str = "template", source_project_id: str | None = None) -> str:
    name = require_text(name, "项目名称", 100)
    if mode not in CREATE_MODES:
        raise ValidationError(f"未知的创建方式：{mode}")
    if get_project_by_name(conn, name):
        raise ValidationError(f"项目名称“{name}”已存在")
    ts = now_str()
    pid = new_id()
    conn.execute(
        "INSERT INTO projects(id, name, description, created_at, updated_at, is_active) "
        "VALUES(?,?,?,?,?,1)", (pid, name, optional_text(description), ts, ts))
    if mode == "copy":
        if not source_project_id or not get_project(conn, source_project_id):
            raise ValidationError("请选择要复制配置的源项目")
        _copy_config(conn, source_project_id, pid, ts)
    else:
        _seed_priorities_statuses(conn, pid, ts)
        if mode == "template":
            _seed_tags(conn, pid, ts)
            for i, (cname, color) in enumerate(TEMPLATE_CATEGORIES):
                conn.execute(
                    "INSERT INTO categories(id, project_id, name, color, sort_order, "
                    "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                    (new_id(), pid, cname, color, i, ts, ts))
    return pid


def update_project(conn: sqlite3.Connection, project_id: str, name: str | None = None,
                   description: str | None = None, is_active: bool | None = None) -> None:
    p = get_project(conn, project_id)
    if not p:
        raise ValidationError("项目不存在")
    new_name = require_text(name, "项目名称", 100) if name is not None else p["name"]
    if new_name != p["name"] and get_project_by_name(conn, new_name):
        raise ValidationError(f"项目名称“{new_name}”已存在")
    conn.execute(
        "UPDATE projects SET name=?, description=?, is_active=?, updated_at=? WHERE id=?",
        (new_name, optional_text(description) if description is not None else p["description"],
         int(is_active) if is_active is not None else p["is_active"], now_str(), project_id))


def delete_project(conn: sqlite3.Connection, project_id: str, confirm_name: str) -> None:
    """删除项目及其全部数据。必须输入完整项目名称确认。"""
    p = get_project(conn, project_id)
    if not p:
        raise ValidationError("项目不存在")
    if confirm_name != p["name"]:
        raise ValidationError("确认名称与项目名称不一致，未删除")
    # 先断开任务之间的父子引用，再级联删除
    conn.execute("UPDATE tasks SET parent_id=NULL WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    if get_global_setting(conn, "current_project_id") == project_id:
        set_global_setting(conn, "current_project_id", None)


def project_task_count(conn: sqlite3.Connection, project_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?",
                        (project_id,)).fetchone()[0]


def get_current_project_id(conn: sqlite3.Connection) -> str | None:
    """当前项目：设置中记录的项目；若不存在则回退到第一个启用的项目。"""
    pid = get_global_setting(conn, "current_project_id")
    if pid and get_project(conn, pid):
        return pid
    r = conn.execute("SELECT id FROM projects ORDER BY is_active DESC, created_at LIMIT 1").fetchone()
    return r["id"] if r else None


def set_current_project(conn: sqlite3.Connection, project_id: str) -> None:
    if not get_project(conn, project_id):
        raise DatabaseError("要切换的项目不存在")
    set_global_setting(conn, "current_project_id", project_id)
