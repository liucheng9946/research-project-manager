"""任务依赖关系：task_id 依赖 depends_on_task_id（后者为前置任务）。禁止循环依赖。"""
from __future__ import annotations

import sqlite3
from collections import deque

from core.database import new_id, now_str, rows_to_dicts
from utils.validators import ValidationError


def _project_of(conn: sqlite3.Connection, task_id: str) -> str | None:
    r = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    return r["project_id"] if r else None


def would_create_cycle(conn: sqlite3.Connection, task_id: str, depends_on_id: str) -> bool:
    """若 depends_on 已经（直接或间接）依赖 task，则新增 task→depends_on 会形成环。"""
    if task_id == depends_on_id:
        return True
    seen, queue = {depends_on_id}, deque([depends_on_id])
    while queue:
        cur = queue.popleft()
        for r in conn.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?",
                              (cur,)):
            nxt = r[0]
            if nxt == task_id:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def add_dependency(conn: sqlite3.Connection, task_id: str, depends_on_id: str) -> str | None:
    p1, p2 = _project_of(conn, task_id), _project_of(conn, depends_on_id)
    if not p1 or not p2:
        raise ValidationError("依赖关系中的任务不存在")
    if p1 != p2:
        raise ValidationError("不能依赖其他项目的任务")
    if task_id == depends_on_id:
        raise ValidationError("任务不能依赖自身")
    exists = conn.execute("SELECT id FROM task_dependencies WHERE task_id=? AND depends_on_task_id=?",
                          (task_id, depends_on_id)).fetchone()
    if exists:
        return exists["id"]
    if would_create_cycle(conn, task_id, depends_on_id):
        raise ValidationError("添加该依赖会形成循环依赖，已拒绝")
    did = new_id()
    conn.execute("INSERT INTO task_dependencies(id, task_id, depends_on_task_id, created_at) "
                 "VALUES(?,?,?,?)", (did, task_id, depends_on_id, now_str()))
    return did


def remove_dependency(conn: sqlite3.Connection, task_id: str, depends_on_id: str) -> None:
    conn.execute("DELETE FROM task_dependencies WHERE task_id=? AND depends_on_task_id=?",
                 (task_id, depends_on_id))


def set_dependencies(conn: sqlite3.Connection, task_id: str, depends_on_ids: list[str]) -> None:
    """把任务的前置依赖设置为给定集合（先删后加，任何一项失败都由外层事务回滚）。"""
    current = {r[0] for r in conn.execute(
        "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?", (task_id,))}
    target = set(depends_on_ids or [])
    for d in current - target:
        remove_dependency(conn, task_id, d)
    for d in target - current:
        add_dependency(conn, task_id, d)


def predecessors(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """前置依赖（本任务依赖的任务）。"""
    return rows_to_dicts(conn.execute(
        "SELECT t.id, t.task_code, t.title, t.progress, s.name AS status, s.kind AS status_kind "
        "FROM task_dependencies d JOIN tasks t ON t.id=d.depends_on_task_id "
        "LEFT JOIN statuses s ON s.id=t.status_id WHERE d.task_id=? ORDER BY t.task_code, t.title",
        (task_id,)))


def successors(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """后续依赖（依赖本任务的任务）。"""
    return rows_to_dicts(conn.execute(
        "SELECT t.id, t.task_code, t.title, t.progress, s.name AS status, s.kind AS status_kind "
        "FROM task_dependencies d JOIN tasks t ON t.id=d.task_id "
        "LEFT JOIN statuses s ON s.id=t.status_id WHERE d.depends_on_task_id=? "
        "ORDER BY t.task_code, t.title", (task_id,)))


def project_dependencies(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    return rows_to_dicts(conn.execute(
        "SELECT d.* FROM task_dependencies d JOIN tasks t ON t.id=d.task_id WHERE t.project_id=?",
        (project_id,)))
