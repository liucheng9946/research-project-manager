"""项目级科研工作日志（project_daily_logs）。

记录工具而非考勤：不要求每天填写，没有打卡或连续天数统计。
related_task_ids 以 JSON 数组保存；读取时自动过滤已删除的任务。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from core.database import new_id, now_str, rows_to_dicts
from utils.date_utils import DateParseError, parse_date
from utils.validators import ValidationError, optional_text, require_text

TEXT_FIELDS = ("title", "content", "done_work", "problems", "next_steps")
FIELD_LABELS = {"title": "标题", "content": "当日工作内容", "done_work": "完成的工作",
                "problems": "遇到的问题", "next_steps": "下一步计划"}


def _date(v) -> str:
    try:
        d = parse_date(v)
    except DateParseError as e:
        raise ValidationError(str(e)) from None
    if not d:
        raise ValidationError("日志日期不能为空")
    return d


def _check_tasks(conn, project_id: str, task_ids) -> list[str]:
    ids = list(dict.fromkeys(task_ids or []))
    for t in ids:
        r = conn.execute("SELECT project_id FROM tasks WHERE id=?", (t,)).fetchone()
        if not r or r["project_id"] != project_id:
            raise ValidationError("关联任务不存在或不属于当前项目")
    return ids


def create(conn: sqlite3.Connection, project_id: str, log_date, title: str, content: str | None = None,
           done_work: str | None = None, problems: str | None = None, next_steps: str | None = None,
           related_task_ids: list[str] | None = None) -> str:
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        raise ValidationError("项目不存在")
    lid = new_id()
    ts = now_str()
    conn.execute(
        "INSERT INTO project_daily_logs(id, project_id, log_date, title, content, done_work, problems, "
        "next_steps, related_task_ids, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (lid, project_id, _date(log_date), require_text(title, "日志标题", 200), optional_text(content),
         optional_text(done_work), optional_text(problems), optional_text(next_steps),
         json.dumps(_check_tasks(conn, project_id, related_task_ids)), ts, ts))
    return lid


def get(conn: sqlite3.Connection, log_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM project_daily_logs WHERE id=?", (log_id,)).fetchone()
    return _decorate(conn, [dict(r)])[0] if r else None


def update(conn: sqlite3.Connection, log_id: str, **changes) -> None:
    cur = conn.execute("SELECT * FROM project_daily_logs WHERE id=?", (log_id,)).fetchone()
    if not cur:
        raise ValidationError("日志不存在")
    sets = {}
    for k, v in changes.items():
        if k == "log_date":
            sets[k] = _date(v)
        elif k == "title":
            sets[k] = require_text(v, "日志标题", 200)
        elif k in TEXT_FIELDS:
            sets[k] = optional_text(v)
        elif k == "related_task_ids":
            sets[k] = json.dumps(_check_tasks(conn, cur["project_id"], v))
    if not sets:
        return
    conn.execute(f"UPDATE project_daily_logs SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE id=?",
                 (*sets.values(), now_str(), log_id))


def delete(conn: sqlite3.Connection, log_id: str) -> None:
    conn.execute("DELETE FROM project_daily_logs WHERE id=?", (log_id,))


def _decorate(conn, rows: list[dict]) -> list[dict]:
    for r in rows:
        try:
            ids = json.loads(r.get("related_task_ids") or "[]")
        except ValueError:
            ids = []
        tasks = []
        for t in ids:
            x = conn.execute("SELECT id, task_code, title FROM tasks WHERE id=?", (t,)).fetchone()
            if x:
                tasks.append(dict(x))
        r["related_tasks"] = tasks
        r["related_task_ids"] = [t["id"] for t in tasks]
    return rows


def search(conn: sqlite3.Connection, project_id: str, date_from=None, date_to=None,
           keyword: str | None = None, task_id: str | None = None) -> list[dict]:
    """按日期范围、关键词（标题/内容/完成/问题/计划，多个词同时匹配）、关联任务查询。"""
    sql = "SELECT * FROM project_daily_logs WHERE project_id=?"
    args: list = [project_id]
    if date_from:
        sql += " AND log_date >= ?"
        args.append(parse_date(date_from))
    if date_to:
        sql += " AND log_date <= ?"
        args.append(parse_date(date_to))
    for word in (keyword or "").split():
        like = f"%{word}%"
        sql += " AND (" + " OR ".join(f"IFNULL({f},'') LIKE ?" for f in TEXT_FIELDS) + ")"
        args += [like] * len(TEXT_FIELDS)
    if task_id:
        sql += " AND related_task_ids LIKE ?"
        args.append(f'%"{task_id}"%')
    sql += " ORDER BY log_date DESC, created_at DESC, rowid DESC"
    return _decorate(conn, rows_to_dicts(conn.execute(sql, args)))


def list_by_date(conn: sqlite3.Connection, project_id: str, d) -> list[dict]:
    day = _date(d)
    return search(conn, project_id, day, day)


def dates_with_logs(conn: sqlite3.Connection, project_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT log_date FROM project_daily_logs WHERE project_id=? ORDER BY log_date DESC",
        (project_id,))]


def logs_for_task(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    r = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    return search(conn, r["project_id"], task_id=task_id) if r else []


def today_str() -> str:
    return date.today().isoformat()
