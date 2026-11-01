"""任务 CRUD、父子关系、批量操作、排序与查询。

所有写操作接收一个已开启事务的连接（由调用方 `with transaction() as conn` 提供），
函数内部任何校验失败都会抛出 ValidationError，调用方事务随之整体回滚。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd

from core import dependency_service, progress_service
from core.category_service import default_status_id, get_or_create
from core.database import new_id, now_str, rows_to_dicts
from utils.date_utils import DateParseError, is_overdue, parse_date
from utils.helpers import is_blank, split_people
from utils.validators import (ValidationError, optional_text, require_text,
                              validate_progress, validate_weight)


class DuplicateCodeError(ValidationError):
    """任务编号与项目内已有任务重复。调用方可选择 on_duplicate='allow' 后重试。"""

    def __init__(self, code: str, existing: list[dict]):
        self.code = code
        self.existing = existing
        titles = "、".join(e["title"] for e in existing[:3])
        super().__init__(f"任务编号“{code}”已被使用（{titles}）")


UNSET: Any = object()

FIELD_LABELS = {
    "task_code": "任务编号", "title": "任务名称", "description": "任务描述",
    "deliverable": "交付物", "prerequisites": "前置依赖/数据来源（原文）",
    "category_id": "工作类别", "chapter_id": "论文章节", "priority_id": "优先级",
    "status_id": "状态", "assignee": "负责人", "progress": "完成度",
    "progress_mode": "进度计算模式", "weight": "工作量权重", "due_date": "截止日期",
    "acceptance_criteria": "验收标准", "notes": "备注", "parent_id": "父任务",
    "sort_order": "排序",
}
EDITABLE_FIELDS = list(FIELD_LABELS)
BULK_FIELDS = ["assignee", "due_date", "category_id", "chapter_id", "priority_id",
               "status_id", "progress"]
_LOOKUP_COLS = {"category_id": "categories", "chapter_id": "chapters",
                "priority_id": "priorities", "status_id": "statuses"}

# 变更钩子：任务字段真实变化后调用 hook(conn, task_id, diffs)。
# diffs: list[(field, old, new)]。由历史记录模块注册（V1.1）。
CHANGE_HOOKS: list = []
CREATE_HOOKS: list = []


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _normalize(conn: sqlite3.Connection, project_id: str, data: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k not in EDITABLE_FIELDS:
            continue
        if k == "title":
            out[k] = require_text(v, "任务名称", 300)
        elif k == "task_code":
            out[k] = None if is_blank(v) else str(v).strip()[:50]
        elif k == "progress":
            out[k] = validate_progress(v)
        elif k == "weight":
            out[k] = validate_weight(v)
        elif k == "progress_mode":
            if v not in ("manual", "auto"):
                raise ValidationError("进度计算模式只能是 manual 或 auto")
            out[k] = v
        elif k == "due_date":
            try:
                out[k] = parse_date(v)
            except DateParseError as e:
                raise ValidationError(str(e)) from None
        elif k in _LOOKUP_COLS:
            if is_blank(v):
                out[k] = None
            else:
                r = conn.execute(f"SELECT project_id FROM {_LOOKUP_COLS[k]} WHERE id=?",
                                 (v,)).fetchone()
                if not r or r["project_id"] != project_id:
                    raise ValidationError(f"{FIELD_LABELS[k]}无效或不属于当前项目")
                out[k] = v
        elif k == "parent_id":
            if is_blank(v):
                out[k] = None
            else:
                r = conn.execute("SELECT project_id FROM tasks WHERE id=?", (v,)).fetchone()
                if not r or r["project_id"] != project_id:
                    raise ValidationError("父任务不存在或不属于当前项目")
                out[k] = v
        elif k == "sort_order":
            out[k] = int(v or 0)
        elif k == "assignee":
            out[k] = optional_text(v, 200)
        else:
            out[k] = optional_text(v)
    return out


def find_by_code(conn: sqlite3.Connection, project_id: str, code: str,
                 exclude_id: str | None = None) -> list[dict]:
    if is_blank(code):
        return []
    sql = "SELECT id, task_code, title FROM tasks WHERE project_id=? AND task_code=?"
    args: list = [project_id, code]
    if exclude_id:
        sql += " AND id<>?"
        args.append(exclude_id)
    return rows_to_dicts(conn.execute(sql, args))


def descendants(conn: sqlite3.Connection, task_id: str) -> list[str]:
    rows = conn.execute(
        "WITH RECURSIVE d(id) AS (SELECT id FROM tasks WHERE parent_id=? "
        "UNION ALL SELECT t.id FROM tasks t JOIN d ON t.parent_id=d.id) SELECT id FROM d",
        (task_id,)).fetchall()
    return [r[0] for r in rows]


def children(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    return rows_to_dicts(conn.execute(
        "SELECT t.id, t.task_code, t.title, t.progress, t.weight, t.progress_mode, "
        "s.name AS status, s.color AS status_color, s.kind AS status_kind "
        "FROM tasks t LEFT JOIN statuses s ON s.id=t.status_id "
        "WHERE t.parent_id=? ORDER BY t.sort_order, t.task_code, t.title", (task_id,)))


def _check_parent(conn: sqlite3.Connection, task_id: str, parent_id: str | None) -> None:
    if parent_id is None:
        return
    if parent_id == task_id:
        raise ValidationError("不能把任务设置为自己的父任务")
    if parent_id in descendants(conn, task_id):
        raise ValidationError("不能把任务移动到它自己的子孙任务之下（会形成循环层级）")


def _status_kind(conn: sqlite3.Connection, status_id: str | None) -> str | None:
    if not status_id:
        return None
    r = conn.execute("SELECT kind FROM statuses WHERE id=?", (status_id,)).fetchone()
    return r["kind"] if r else None


def _next_sort(conn: sqlite3.Connection, project_id: str, parent_id: str | None) -> int:
    if parent_id:
        r = conn.execute("SELECT COALESCE(MAX(sort_order), -1)+1 FROM tasks WHERE parent_id=?",
                         (parent_id,)).fetchone()
    else:
        r = conn.execute("SELECT COALESCE(MAX(sort_order), -1)+1 FROM tasks "
                         "WHERE project_id=? AND parent_id IS NULL", (project_id,)).fetchone()
    return int(r[0])


def sync_people(conn: sqlite3.Connection, task_id: str, project_id: str,
                assignee: str | None) -> None:
    """根据负责人原文同步 task_people 多对多关联（自动创建不存在的负责人）。"""
    conn.execute("DELETE FROM task_people WHERE task_id=?", (task_id,))
    for name in split_people(assignee):
        pid = get_or_create(conn, "people", project_id, name)
        conn.execute("INSERT OR IGNORE INTO task_people(task_id, person_id) VALUES(?,?)",
                     (task_id, pid))


def set_tags(conn: sqlite3.Connection, task_id: str, tag_ids: list[str]) -> None:
    proj = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    conn.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
    for tid in tag_ids or []:
        r = conn.execute("SELECT project_id FROM tags WHERE id=?", (tid,)).fetchone()
        if not r or r["project_id"] != proj:
            raise ValidationError("标签无效或不属于当前项目")
        conn.execute("INSERT OR IGNORE INTO task_tags(task_id, tag_id) VALUES(?,?)", (task_id, tid))


def _run_progress_recalc(conn: sqlite3.Connection, project_id: str) -> None:
    for tid, old, new in progress_service.recalc_project(conn, project_id):
        for hook in CHANGE_HOOKS:
            hook(conn, tid, [("progress", old, new)], auto=True)


# ---------------------------------------------------------------------------
# Create / Update / Delete
# ---------------------------------------------------------------------------
def create_task(conn: sqlite3.Connection, project_id: str, data: dict,
                tag_ids: list[str] | None = None, dependency_ids: list[str] | None = None,
                on_duplicate: str = "error", recalc: bool = True) -> str:
    if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
        raise ValidationError("项目不存在")
    d = _normalize(conn, project_id, data)
    if "title" not in d:
        raise ValidationError("任务名称不能为空")
    if d.get("task_code") and on_duplicate == "error":
        dup = find_by_code(conn, project_id, d["task_code"])
        if dup:
            raise DuplicateCodeError(d["task_code"], dup)
    if "status_id" not in d or d["status_id"] is None:
        d["status_id"] = d.get("status_id") or default_status_id(conn, project_id)
    d.setdefault("progress", 0.0)
    d.setdefault("progress_mode", "manual")
    d.setdefault("weight", 1.0)
    if "sort_order" not in d:
        d["sort_order"] = _next_sort(conn, project_id, d.get("parent_id"))
    ts = now_str()
    tid = new_id()
    completed_at = ts if _status_kind(conn, d["status_id"]) == "done" else None
    extra = {k: data[k] for k in ("source_file", "source_row") if k in data}
    cols = ["id", "project_id", *d.keys(), *extra.keys(), "created_at", "updated_at",
            "completed_at"]
    vals = [tid, project_id, *d.values(), *extra.values(), ts, ts, completed_at]
    conn.execute(f"INSERT INTO tasks({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", vals)
    sync_people(conn, tid, project_id, d.get("assignee"))
    if tag_ids:
        set_tags(conn, tid, tag_ids)
    if dependency_ids:
        dependency_service.set_dependencies(conn, tid, dependency_ids)
    for hook in CREATE_HOOKS:
        hook(conn, tid)
    if recalc:
        _run_progress_recalc(conn, project_id)
    return tid


def get_task(conn: sqlite3.Connection, task_id: str) -> dict | None:
    r = conn.execute(
        "SELECT t.*, c.name AS category, ch.name AS chapter, p.name AS priority, "
        "p.color AS priority_color, s.name AS status, s.kind AS status_kind, "
        "s.color AS status_color, par.title AS parent_title, par.task_code AS parent_code "
        "FROM tasks t LEFT JOIN categories c ON c.id=t.category_id "
        "LEFT JOIN chapters ch ON ch.id=t.chapter_id LEFT JOIN priorities p ON p.id=t.priority_id "
        "LEFT JOIN statuses s ON s.id=t.status_id LEFT JOIN tasks par ON par.id=t.parent_id "
        "WHERE t.id=?", (task_id,)).fetchone()
    if not r:
        return None
    t = dict(r)
    t["tag_ids"] = [x[0] for x in conn.execute("SELECT tag_id FROM task_tags WHERE task_id=?",
                                               (task_id,))]
    t["tags"] = [x[0] for x in conn.execute(
        "SELECT g.name FROM task_tags tt JOIN tags g ON g.id=tt.tag_id WHERE tt.task_id=? "
        "ORDER BY g.sort_order", (task_id,))]
    t["dependency_ids"] = [x[0] for x in conn.execute(
        "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?", (task_id,))]
    t["n_children"] = conn.execute("SELECT COUNT(*) FROM tasks WHERE parent_id=?",
                                   (task_id,)).fetchone()[0]
    return t


def update_task(conn: sqlite3.Connection, task_id: str, changes: dict,
                tag_ids: Any = UNSET, dependency_ids: Any = UNSET,
                on_duplicate: str = "error", recalc: bool = True) -> list[tuple]:
    """更新任务；只写入真正变化的字段。返回 [(字段, 旧值, 新值), ...]。"""
    cur = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not cur:
        raise ValidationError("任务不存在（可能已被删除）")
    cur = dict(cur)
    pid = cur["project_id"]
    d = _normalize(conn, pid, changes)
    if "parent_id" in d:
        _check_parent(conn, task_id, d["parent_id"])
    if d.get("task_code") and d["task_code"] != cur["task_code"] and on_duplicate == "error":
        dup = find_by_code(conn, pid, d["task_code"], exclude_id=task_id)
        if dup:
            raise DuplicateCodeError(d["task_code"], dup)
    diffs = []
    for k, v in d.items():
        old = cur[k]
        if k in ("progress", "weight"):
            same = old is not None and v is not None and abs(float(old) - float(v)) < 1e-9
        else:
            same = (old or None) == (v or None) if not isinstance(v, (int, float)) else old == v
        if not same:
            diffs.append((k, old, v))
    if not diffs and tag_ids is UNSET and dependency_ids is UNSET:
        return []
    ts = now_str()
    if diffs:
        sets = {k: v for k, _, v in diffs}
        if "parent_id" in sets and "sort_order" not in sets:
            sets["sort_order"] = _next_sort(conn, pid, sets["parent_id"])
        if "status_id" in sets:
            new_kind = _status_kind(conn, sets["status_id"])
            if new_kind == "done" and not cur["completed_at"]:
                sets["completed_at"] = ts
            elif new_kind != "done":
                sets["completed_at"] = None
        sql = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE tasks SET {sql}, updated_at=? WHERE id=?",
                     (*sets.values(), ts, task_id))
        if "assignee" in sets:
            sync_people(conn, task_id, pid, sets["assignee"])
    if tag_ids is not UNSET:
        before = set(x[0] for x in conn.execute("SELECT tag_id FROM task_tags WHERE task_id=?",
                                                (task_id,)))
        set_tags(conn, task_id, tag_ids)
        if before != set(tag_ids or []):
            conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ts, task_id))
    if dependency_ids is not UNSET:
        dependency_service.set_dependencies(conn, task_id, dependency_ids)
    if diffs:
        for hook in CHANGE_HOOKS:
            hook(conn, task_id, diffs, auto=False)
    if recalc and any(k in ("progress", "progress_mode", "weight", "parent_id", "status_id")
                      for k, _, _ in diffs):
        _run_progress_recalc(conn, pid)
    return diffs


def delete_impact(conn: sqlite3.Connection, task_ids: list[str]) -> dict:
    """删除前的影响范围：后代任务、依赖关系等。"""
    ids = set(task_ids)
    desc = set()
    for t in task_ids:
        desc.update(descendants(conn, t))
    desc -= ids
    all_ids = list(ids | desc)
    ph = ",".join("?" * len(all_ids)) or "''"
    deps = conn.execute(f"SELECT COUNT(*) FROM task_dependencies WHERE task_id IN ({ph}) "
                        f"OR depends_on_task_id IN ({ph})", all_ids * 2).fetchone()[0]
    with_children = [t for t in task_ids if conn.execute(
        "SELECT 1 FROM tasks WHERE parent_id=? LIMIT 1", (t,)).fetchone()]
    return {"selected": len(ids), "descendants": len(desc), "dependencies": deps,
            "with_children": with_children, "descendant_ids": list(desc)}


def delete_tasks(conn: sqlite3.Connection, task_ids: list[str], child_mode: str | None = None) -> int:
    """删除任务。

    child_mode（仅当所选任务有子任务时需要）：
      'cascade' 同时删除全部后代任务；
      'promote' 子任务提升到被删除任务的上一级（被删的是顶层任务时成为顶层任务）。
    有子任务但未指定 child_mode 时拒绝执行，绝不静默级联。返回删除的任务数。
    """
    if not task_ids:
        return 0
    rows = [dict(r) for r in conn.execute(
        f"SELECT id, parent_id, project_id FROM tasks WHERE id IN ({','.join('?' * len(task_ids))})",
        task_ids)]
    if len(rows) != len(set(task_ids)):
        raise ValidationError("部分任务不存在（可能已被删除）")
    projects = {r["project_id"] for r in rows}
    impact = delete_impact(conn, task_ids)
    if impact["with_children"] and child_mode not in ("cascade", "promote"):
        raise ValidationError("所选任务包含子任务，请选择“同时删除后代任务”或“将子任务提升一级”")
    to_delete = set(task_ids)
    if impact["with_children"] and child_mode == "cascade":
        to_delete |= set(impact["descendant_ids"])
    elif impact["with_children"] and child_mode == "promote":
        parent_of = {r["id"]: r["parent_id"] for r in rows}

        def surviving_parent(pid):
            # 如果上级也在删除列表中，继续向上找
            while pid in to_delete:
                pid = parent_of.get(pid) or conn.execute(
                    "SELECT parent_id FROM tasks WHERE id=?", (pid,)).fetchone()[0]
            return pid

        ts = now_str()
        for tid in task_ids:
            new_parent = surviving_parent(parent_of[tid])
            for ch in conn.execute("SELECT id FROM tasks WHERE parent_id=?", (tid,)).fetchall():
                if ch[0] in to_delete:
                    continue
                conn.execute("UPDATE tasks SET parent_id=?, updated_at=? WHERE id=?",
                             (new_parent, ts, ch[0]))
    ids = list(to_delete)
    ph = ",".join("?" * len(ids))
    conn.execute(f"UPDATE tasks SET parent_id=NULL WHERE id IN ({ph})", ids)
    conn.execute(f"DELETE FROM tasks WHERE id IN ({ph})", ids)
    for p in projects:
        _run_progress_recalc(conn, p)
    return len(ids)


def bulk_update(conn: sqlite3.Connection, task_ids: list[str], field: str, value) -> int:
    if field not in BULK_FIELDS:
        raise ValidationError(f"不支持批量修改字段：{field}")
    n = 0
    projects = set()
    for tid in task_ids:
        diffs = update_task(conn, tid, {field: value}, recalc=False)
        if diffs:
            n += 1
        projects.add(conn.execute("SELECT project_id FROM tasks WHERE id=?", (tid,)).fetchone()[0])
    for p in projects:
        _run_progress_recalc(conn, p)
    return n


def move_order(conn: sqlite3.Connection, task_id: str, direction: int) -> None:
    """在同级任务中上移(-1)或下移(+1)。"""
    t = conn.execute("SELECT project_id, parent_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        return
    if t["parent_id"]:
        sib = [r[0] for r in conn.execute(
            "SELECT id FROM tasks WHERE parent_id=? ORDER BY sort_order, task_code, title",
            (t["parent_id"],))]
    else:
        sib = [r[0] for r in conn.execute(
            "SELECT id FROM tasks WHERE project_id=? AND parent_id IS NULL "
            "ORDER BY sort_order, task_code, title", (t["project_id"],))]
    i = sib.index(task_id)
    j = i + direction
    if j < 0 or j >= len(sib):
        return
    sib[i], sib[j] = sib[j], sib[i]
    for order, sid in enumerate(sib):
        conn.execute("UPDATE tasks SET sort_order=? WHERE id=?", (order, sid))


# ---------------------------------------------------------------------------
# 进展记录（V1.0 的 task_comments 已在迁移 v2 中复制到 task_activity_logs）
# ---------------------------------------------------------------------------
def add_comment(conn: sqlite3.Connection, task_id: str, content: str) -> str:
    """V1.1 起进展记录统一写入工作历史（task_activity_logs，类型 note）。"""
    from core.activity_service import add_log
    return add_log(conn, task_id, content, "note")


def list_comments(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    from core.activity_service import list_logs
    return list_logs(conn, task_id, types=("note",))


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
TASK_DF_COLUMNS = [
    "id", "task_code", "title", "description", "deliverable", "prerequisites", "category_id",
    "category", "category_color", "category_order", "chapter_id", "chapter", "chapter_group",
    "chapter_order", "priority_id", "priority", "priority_color", "priority_order", "status_id",
    "status", "status_kind", "status_color", "status_order", "assignee", "progress",
    "progress_mode", "weight", "due_date", "acceptance_criteria", "notes", "parent_id",
    "sort_order", "created_at", "updated_at", "completed_at", "source_file", "source_row",
]


def list_tasks_df(conn: sqlite3.Connection, project_id: str) -> pd.DataFrame:
    """项目全部任务（含关联名称与派生字段）。一次查询 + pandas 处理，适合数千条任务。"""
    rows = conn.execute(
        "SELECT t.id, t.task_code, t.title, t.description, t.deliverable, t.prerequisites, "
        "t.category_id, c.name AS category, c.color AS category_color, c.sort_order AS category_order, "
        "t.chapter_id, ch.name AS chapter, ch.group_name AS chapter_group, ch.sort_order AS chapter_order, "
        "t.priority_id, p.name AS priority, p.color AS priority_color, p.sort_order AS priority_order, "
        "t.status_id, s.name AS status, s.kind AS status_kind, s.color AS status_color, "
        "s.sort_order AS status_order, t.assignee, t.progress, t.progress_mode, t.weight, t.due_date, "
        "t.acceptance_criteria, t.notes, t.parent_id, t.sort_order, t.created_at, t.updated_at, "
        "t.completed_at, t.source_file, t.source_row "
        "FROM tasks t LEFT JOIN categories c ON c.id=t.category_id "
        "LEFT JOIN chapters ch ON ch.id=t.chapter_id LEFT JOIN priorities p ON p.id=t.priority_id "
        "LEFT JOIN statuses s ON s.id=t.status_id WHERE t.project_id=?", (project_id,)).fetchall()
    # dtype=object：保持空值为 None（pandas 3 默认字符串类型会把 None 变成 NaN）
    df = pd.DataFrame([tuple(r) for r in rows], columns=TASK_DF_COLUMNS, dtype=object)
    for c in ("category_order", "chapter_order", "priority_order", "status_order", "sort_order"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(10**6).astype(int)
    extra_cols = ["n_children", "has_children", "is_leaf", "is_done", "overdue", "tags", "people",
                  "depends_on", "depth", "parent_title", "tree_index", "path"]
    if df.empty:
        for c in extra_cols:
            df[c] = pd.Series(dtype=object)
        return df
    df["progress"] = df["progress"].astype(float)
    df["weight"] = df["weight"].astype(float)
    df["status_kind"] = df["status_kind"].fillna("todo")
    n_child = df["parent_id"].value_counts()
    df["n_children"] = df["id"].map(n_child).fillna(0).astype(int)
    df["has_children"] = df["n_children"] > 0
    df["is_leaf"] = ~df["has_children"]
    df["is_done"] = df["status_kind"] == "done"
    df["overdue"] = [is_overdue(d, done or k == "cancelled")
                     for d, done, k in zip(df["due_date"], df["is_done"], df["status_kind"])]
    tag_map: dict[str, list[str]] = {}
    for r in conn.execute("SELECT tt.task_id, g.name FROM task_tags tt JOIN tags g ON g.id=tt.tag_id "
                          "WHERE g.project_id=? ORDER BY g.sort_order", (project_id,)):
        tag_map.setdefault(r[0], []).append(r[1])
    df["tags"] = df["id"].map(lambda i: tag_map.get(i, []))
    ppl_map: dict[str, list[str]] = {}
    for r in conn.execute("SELECT tp.task_id, pe.name FROM task_people tp JOIN people pe "
                          "ON pe.id=tp.person_id WHERE pe.project_id=? ORDER BY pe.sort_order, pe.name",
                          (project_id,)):
        ppl_map.setdefault(r[0], []).append(r[1])
    df["people"] = df["id"].map(lambda i: ppl_map.get(i, []))
    code_of = dict(zip(df["id"], [c or t for c, t in zip(df["task_code"], df["title"])]))
    dep_map: dict[str, list[str]] = {}
    for r in conn.execute("SELECT d.task_id, d.depends_on_task_id FROM task_dependencies d "
                          "JOIN tasks t ON t.id=d.task_id WHERE t.project_id=?", (project_id,)):
        dep_map.setdefault(r[0], []).append(code_of.get(r[1], "?"))
    df["depends_on"] = df["id"].map(lambda i: dep_map.get(i, []))
    title_of = dict(zip(df["id"], df["title"]))
    df["parent_title"] = df["parent_id"].map(lambda p: title_of.get(p) if p else None)
    # 树形顺序与深度
    order = tree_order(df)
    pos = {tid: (i, depth, path) for i, (tid, depth, path) in enumerate(order)}
    df["tree_index"] = df["id"].map(lambda i: pos.get(i, (10**9, 0, ""))[0])
    df["depth"] = df["id"].map(lambda i: pos.get(i, (0, 0, ""))[1])
    df["path"] = df["id"].map(lambda i: pos.get(i, (0, 0, ""))[2])
    return df


def tree_order(df: pd.DataFrame) -> list[tuple[str, int, str]]:
    """深度优先顺序：[(id, 深度, 路径标题), ...]，同级按 sort_order、编号、名称排序。"""
    if df.empty:
        return []
    ids = set(df["id"])
    kids: dict[str | None, list] = {}
    for r in df[["id", "parent_id", "sort_order", "task_code", "title"]].itertuples(index=False):
        parent = r.parent_id if r.parent_id in ids else None
        kids.setdefault(parent, []).append((r.sort_order, r.task_code or "", r.title, r.id))
    for v in kids.values():
        v.sort()
    out: list[tuple[str, int, str]] = []
    stack = [(t[3], 0, t[2]) for t in reversed(kids.get(None, []))]
    seen = set()
    while stack:
        tid, depth, path = stack.pop()
        if tid in seen:
            continue
        seen.add(tid)
        out.append((tid, depth, path))
        for t in reversed(kids.get(tid, [])):
            stack.append((t[3], depth + 1, f"{path} / {t[2]}"))
    return out


def find_duplicate_codes(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    return rows_to_dicts(conn.execute(
        "SELECT task_code, COUNT(*) AS n FROM tasks WHERE project_id=? AND task_code IS NOT NULL "
        "GROUP BY task_code HAVING COUNT(*)>1 ORDER BY task_code", (project_id,)))


def task_options(conn: sqlite3.Connection, project_id: str, exclude: set[str] | None = None) -> list[dict]:
    """供选择框使用的任务列表（树形顺序）。"""
    df = list_tasks_df(conn, project_id)
    if df.empty:
        return []
    df = df.sort_values("tree_index")
    exclude = exclude or set()
    out = []
    for r in df.itertuples(index=False):
        if r.id in exclude:
            continue
        label = ("　" * int(r.depth)) + (f"{r.task_code} " if r.task_code else "") + r.title
        out.append({"id": r.id, "label": label})
    return out


# ---------------------------------------------------------------------------
# 注册工作历史钩子：字段真实变化时自动记录（V1.1）
# ---------------------------------------------------------------------------
from core import activity_service as _activity  # noqa: E402

CHANGE_HOOKS.append(_activity.record_changes)
