"""任务工作历史（task_activity_logs）。

* 自动记录：任务的完成度、状态、优先级、截止日期、任务内容在数据库中真实变化时，
  由 task_service.update_task 的变更钩子调用 record_changes 写入；值未变化不会产生记录，
  因此页面刷新不会重复生成历史。
* 手动记录：工作记录（note）与阶段性成果（milestone），可新增、编辑、删除、互相转换。
  自动记录是系统审计信息，不允许编辑或删除。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from core.database import new_id, rows_to_dicts
from core.schema import ACTIVITY_TYPES, MANUAL_ACTIVITY_TYPES
from utils.validators import ValidationError, require_text

_TEXT_FIELDS = {"title": "任务名称", "description": "任务描述", "deliverable": "交付物",
                "acceptance_criteria": "验收标准"}


def _ts() -> str:
    # 毫秒精度，保证同一秒内的多条记录顺序稳定
    return datetime.now().isoformat(sep=" ", timespec="milliseconds")


def _name(conn: sqlite3.Connection, table: str, item_id: str | None) -> str:
    if not item_id:
        return "未设置"
    r = conn.execute(f"SELECT name FROM {table} WHERE id=?", (item_id,)).fetchone()
    return r["name"] if r else "（已删除）"


def _pct(v) -> str:
    f = float(v or 0)
    return f"{f:.0f}%" if abs(f - round(f)) < 1e-9 else f"{f:.1f}%"


def _insert(conn, task_id, atype, content, old=None, new=None, created_at=None) -> str:
    lid = new_id()
    ts = created_at or _ts()
    conn.execute("INSERT INTO task_activity_logs(id, task_id, activity_type, content, old_value, "
                 "new_value, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                 (lid, task_id, atype, content, None if old is None else str(old),
                  None if new is None else str(new), ts, ts))
    return lid


def record_changes(conn: sqlite3.Connection, task_id: str, diffs: list[tuple], auto: bool = False) -> int:
    """根据真实变化的字段写入历史记录。返回写入条数。"""
    n = 0
    text_changes = {}
    for field, old, new in diffs:
        if field == "progress":
            if abs(float(old or 0) - float(new or 0)) < 1e-9:
                continue
            suffix = "（按子任务自动汇总）" if auto else ""
            _insert(conn, task_id, "progress_update", f"完成度：{_pct(old)} → {_pct(new)}{suffix}",
                    float(old or 0), float(new or 0))
        elif field == "status_id":
            o, w = _name(conn, "statuses", old), _name(conn, "statuses", new)
            _insert(conn, task_id, "status_change", f"状态：{o} → {w}", o, w)
        elif field == "priority_id":
            o, w = _name(conn, "priorities", old), _name(conn, "priorities", new)
            _insert(conn, task_id, "priority_change", f"优先级：{o} → {w}", o, w)
        elif field == "due_date":
            o, w = old or "未设置", new or "未设置"
            _insert(conn, task_id, "due_date_change", f"截止日期：{o} → {w}", old, new)
        elif field in _TEXT_FIELDS:
            text_changes[field] = (old, new)
            continue
        else:
            continue
        n += 1
    if text_changes:
        labels = "、".join(_TEXT_FIELDS[f] for f in text_changes)
        _insert(conn, task_id, "description_update", f"修改了：{labels}",
                json.dumps({f: o for f, (o, _) in text_changes.items()}, ensure_ascii=False),
                json.dumps({f: w for f, (_, w) in text_changes.items()}, ensure_ascii=False))
        n += 1
    return n


# ---------------------------------------------------------------------------
# 手动记录
# ---------------------------------------------------------------------------
def add_log(conn: sqlite3.Connection, task_id: str, content: str, activity_type: str = "note") -> str:
    if activity_type not in MANUAL_ACTIVITY_TYPES:
        raise ValidationError("只能手动添加“工作记录”或“阶段性成果”")
    if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
        raise ValidationError("任务不存在")
    return _insert(conn, task_id, activity_type, require_text(content, "记录内容", 10000))


def get_log(conn: sqlite3.Connection, log_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM task_activity_logs WHERE id=?", (log_id,)).fetchone()
    return dict(r) if r else None


def _manual(conn, log_id) -> dict:
    lg = get_log(conn, log_id)
    if not lg:
        raise ValidationError("记录不存在")
    if lg["activity_type"] not in MANUAL_ACTIVITY_TYPES:
        raise ValidationError("系统自动生成的变更记录不能修改或删除")
    return lg


def update_log(conn: sqlite3.Connection, log_id: str, content: str | None = None,
               activity_type: str | None = None) -> None:
    lg = _manual(conn, log_id)
    new_content = require_text(content, "记录内容", 10000) if content is not None else lg["content"]
    new_type = activity_type or lg["activity_type"]
    if new_type not in MANUAL_ACTIVITY_TYPES:
        raise ValidationError("只能在“工作记录”和“阶段性成果”之间转换")
    if new_content == lg["content"] and new_type == lg["activity_type"]:
        return
    conn.execute("UPDATE task_activity_logs SET content=?, activity_type=?, updated_at=? WHERE id=?",
                 (new_content, new_type, _ts(), log_id))


def delete_log(conn: sqlite3.Connection, log_id: str) -> None:
    _manual(conn, log_id)
    conn.execute("DELETE FROM task_activity_logs WHERE id=?", (log_id,))


def list_logs(conn: sqlite3.Connection, task_id: str, types: tuple[str, ...] | None = None,
              newest_first: bool = True) -> list[dict]:
    sql = "SELECT * FROM task_activity_logs WHERE task_id=?"
    args: list = [task_id]
    if types:
        sql += f" AND activity_type IN ({','.join('?' * len(types))})"
        args += list(types)
    order = "DESC" if newest_first else "ASC"
    sql += f" ORDER BY created_at {order}, rowid {order}"
    out = rows_to_dicts(conn.execute(sql, args))
    for o in out:
        o["type_label"] = ACTIVITY_TYPES.get(o["activity_type"], o["activity_type"])
        o["editable"] = o["activity_type"] in MANUAL_ACTIVITY_TYPES
    return out


def project_logs(conn: sqlite3.Connection, project_id: str, types: tuple[str, ...] | None = None,
                 date_from: str | None = None, date_to: str | None = None, limit: int = 500) -> list[dict]:
    """项目内全部任务的历史记录（用于科研日志页的“当日任务动态”和阶段性成果汇总）。"""
    sql = ("SELECT l.*, t.task_code, t.title AS task_title FROM task_activity_logs l "
           "JOIN tasks t ON t.id=l.task_id WHERE t.project_id=?")
    args: list = [project_id]
    if types:
        sql += f" AND l.activity_type IN ({','.join('?' * len(types))})"
        args += list(types)
    if date_from:
        sql += " AND substr(l.created_at, 1, 10) >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND substr(l.created_at, 1, 10) <= ?"
        args.append(date_to)
    sql += " ORDER BY l.created_at DESC, l.rowid DESC LIMIT ?"
    args.append(limit)
    out = rows_to_dicts(conn.execute(sql, args))
    for o in out:
        o["type_label"] = ACTIVITY_TYPES.get(o["activity_type"], o["activity_type"])
    return out
