"""项目级配置项（工作类别、章节、优先级、状态、负责人、标签）的统一管理。

这些都是“查找表”，结构相似，因此用一套带白名单的通用函数处理，
避免在各页面重复编写 SQL。删除被任务引用的配置项时，必须指定
重新分配目标（或明确选择置空），绝不静默删除任务。
"""
from __future__ import annotations

import re
import sqlite3

from core.database import new_id, now_str, rows_to_dicts
from core.schema import STATUS_KINDS
from utils.helpers import split_people
from utils.validators import (ValidationError, optional_text, require_text,
                              validate_color)

LOOKUPS: dict[str, dict] = {
    "categories": {"label": "工作类别", "fields": ["name", "description", "color", "sort_order"],
                   "task_col": "category_id"},
    "chapters": {"label": "论文章节", "fields": ["name", "group_name", "description", "sort_order"],
                 "task_col": "chapter_id"},
    "priorities": {"label": "优先级", "fields": ["name", "description", "color", "sort_order"],
                   "task_col": "priority_id"},
    "statuses": {"label": "状态", "fields": ["name", "kind", "color", "sort_order", "is_default"],
                 "task_col": "status_id"},
    "people": {"label": "负责人", "fields": ["name", "description", "sort_order"],
               "link": ("task_people", "person_id")},
    "tags": {"label": "标签", "fields": ["name", "color", "sort_order"],
             "link": ("task_tags", "tag_id")},
}

UNSET = object()


def _meta(table: str) -> dict:
    if table not in LOOKUPS:
        raise ValidationError(f"不支持的配置类型：{table}")
    return LOOKUPS[table]


def list_items(conn: sqlite3.Connection, table: str, project_id: str) -> list[dict]:
    _meta(table)
    return rows_to_dicts(conn.execute(
        f"SELECT * FROM {table} WHERE project_id=? ORDER BY sort_order, name", (project_id,)))


def get_item(conn: sqlite3.Connection, table: str, item_id: str) -> dict | None:
    _meta(table)
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    return dict(r) if r else None


def find_by_name(conn: sqlite3.Connection, table: str, project_id: str, name: str) -> dict | None:
    _meta(table)
    r = conn.execute(f"SELECT * FROM {table} WHERE project_id=? AND name=?",
                     (project_id, name)).fetchone()
    return dict(r) if r else None


def _clean(table: str, data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k not in _meta(table)["fields"]:
            continue
        if k == "name":
            out[k] = require_text(v, f"{LOOKUPS[table]['label']}名称", 100)
        elif k == "color":
            out[k] = validate_color(v)
        elif k == "sort_order":
            out[k] = int(v or 0)
        elif k == "kind":
            if v not in STATUS_KINDS:
                raise ValidationError(f"未知的状态类别：{v}")
            out[k] = v
        elif k == "is_default":
            out[k] = 1 if v else 0
        else:
            out[k] = optional_text(v)
    return out


def create_item(conn: sqlite3.Connection, table: str, project_id: str, **data) -> str:
    data = _clean(table, data)
    if "name" not in data:
        raise ValidationError("名称不能为空")
    if find_by_name(conn, table, project_id, data["name"]):
        raise ValidationError(f"{LOOKUPS[table]['label']}“{data['name']}”已存在")
    if "sort_order" not in data:
        r = conn.execute(f"SELECT COALESCE(MAX(sort_order), -1) + 1 FROM {table} "
                         "WHERE project_id=?", (project_id,)).fetchone()
        data["sort_order"] = r[0]
    if table == "statuses" and data.get("is_default"):
        conn.execute("UPDATE statuses SET is_default=0 WHERE project_id=?", (project_id,))
    ts = now_str()
    iid = new_id()
    cols = ["id", "project_id", *data.keys(), "created_at", "updated_at"]
    conn.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                 (iid, project_id, *data.values(), ts, ts))
    return iid


def get_or_create(conn: sqlite3.Connection, table: str, project_id: str, name: str,
                  **defaults) -> str:
    found = find_by_name(conn, table, project_id, name.strip())
    if found:
        return found["id"]
    return create_item(conn, table, project_id, name=name, **defaults)


def _replace_person_token(text: str | None, old: str, new: str | None) -> str | None:
    if not text:
        return text
    names = split_people(text)
    if old not in names:
        return text
    if new is None:
        names = [n for n in names if n != old]
        return "、".join(names) if names else None
    # 尽量保留原文的分隔符写法，只替换整词
    return re.sub(rf"(?<![^\s+/、,，;；＋／]){re.escape(old)}(?![^\s+/、,，;；＋／])", new, text)


def update_item(conn: sqlite3.Connection, table: str, item_id: str, **data) -> None:
    item = get_item(conn, table, item_id)
    if not item:
        raise ValidationError(f"{LOOKUPS[table]['label']}不存在")
    data = _clean(table, data)
    if not data:
        return
    if "name" in data and data["name"] != item["name"]:
        if find_by_name(conn, table, item["project_id"], data["name"]):
            raise ValidationError(f"{LOOKUPS[table]['label']}“{data['name']}”已存在")
        if table == "people":  # 同步更新任务中的负责人原文
            for t in conn.execute("SELECT t.id, t.assignee FROM tasks t JOIN task_people tp "
                                  "ON tp.task_id=t.id WHERE tp.person_id=?", (item_id,)).fetchall():
                conn.execute("UPDATE tasks SET assignee=?, updated_at=? WHERE id=?",
                             (_replace_person_token(t["assignee"], item["name"], data["name"]),
                              now_str(), t["id"]))
    if table == "statuses" and data.get("is_default"):
        conn.execute("UPDATE statuses SET is_default=0 WHERE project_id=?", (item["project_id"],))
    sets = ", ".join(f"{k}=?" for k in data)
    conn.execute(f"UPDATE {table} SET {sets}, updated_at=? WHERE id=?",
                 (*data.values(), now_str(), item_id))


def usage_count(conn: sqlite3.Connection, table: str, item_id: str) -> int:
    m = _meta(table)
    if "task_col" in m:
        return conn.execute(f"SELECT COUNT(*) FROM tasks WHERE {m['task_col']}=?",
                            (item_id,)).fetchone()[0]
    link, col = m["link"]
    return conn.execute(f"SELECT COUNT(*) FROM {link} WHERE {col}=?", (item_id,)).fetchone()[0]


def delete_item(conn: sqlite3.Connection, table: str, item_id: str,
                reassign_to=UNSET) -> int:
    """删除配置项。

    若有任务引用：
      * reassign_to 未提供 → 拒绝删除（抛出 ValidationError）；
      * reassign_to=None   → 关联任务该字段置空（标签/负责人为解除关联）；
      * reassign_to=<id>   → 关联任务改为该配置项。
    返回受影响的任务数。
    """
    m = _meta(table)
    item = get_item(conn, table, item_id)
    if not item:
        raise ValidationError(f"{m['label']}不存在")
    n = usage_count(conn, table, item_id)
    if n and reassign_to is UNSET:
        raise ValidationError(
            f"{m['label']}“{item['name']}”仍被 {n} 项任务使用，请先选择重新分配方式")
    if reassign_to not in (UNSET, None):
        target = get_item(conn, table, reassign_to)
        if not target or target["project_id"] != item["project_id"] or reassign_to == item_id:
            raise ValidationError("重新分配的目标无效")
    ts = now_str()
    if n:
        if "task_col" in m:
            new_val = None if reassign_to is None else reassign_to
            conn.execute(f"UPDATE tasks SET {m['task_col']}=?, updated_at=? WHERE {m['task_col']}=?",
                         (new_val, ts, item_id))
        else:
            link, col = m["link"]
            if reassign_to is not None:
                conn.execute(f"INSERT OR IGNORE INTO {link}(task_id, {col}) "
                             f"SELECT task_id, ? FROM {link} WHERE {col}=?", (reassign_to, item_id))
            if table == "people":
                new_name = get_item(conn, "people", reassign_to)["name"] if reassign_to else None
                for t in conn.execute("SELECT t.id, t.assignee FROM tasks t JOIN task_people tp "
                                      "ON tp.task_id=t.id WHERE tp.person_id=?",
                                      (item_id,)).fetchall():
                    conn.execute("UPDATE tasks SET assignee=?, updated_at=? WHERE id=?",
                                 (_replace_person_token(t["assignee"], item["name"], new_name),
                                  ts, t["id"]))
            conn.execute(f"DELETE FROM {link} WHERE {col}=?", (item_id,))
    conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
    return n


def move_item(conn: sqlite3.Connection, table: str, item_id: str, direction: int) -> None:
    """上移(-1)/下移(+1)：重新规整同项目内的排序号。"""
    item = get_item(conn, table, item_id)
    if not item:
        return
    items = list_items(conn, table, item["project_id"])
    ids = [i["id"] for i in items]
    idx = ids.index(item_id)
    j = idx + direction
    if j < 0 or j >= len(ids):
        return
    ids[idx], ids[j] = ids[j], ids[idx]
    for order, iid in enumerate(ids):
        conn.execute(f"UPDATE {table} SET sort_order=? WHERE id=?", (order, iid))


def default_status_id(conn: sqlite3.Connection, project_id: str) -> str | None:
    r = conn.execute("SELECT id FROM statuses WHERE project_id=? ORDER BY is_default DESC, "
                     "sort_order LIMIT 1", (project_id,)).fetchone()
    return r["id"] if r else None


def status_kind_map(conn: sqlite3.Connection, project_id: str) -> dict[str, str]:
    return {r["id"]: r["kind"] for r in conn.execute(
        "SELECT id, kind FROM statuses WHERE project_id=?", (project_id,))}
