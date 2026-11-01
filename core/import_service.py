"""Excel 导入：读取工作表 → 识别表头 → 字段映射 → 校验 → 预览 → 事务提交。

* 用 openpyxl 逐单元格读取，空单元格保持 None，绝不出现字符串 'nan'；
* 自动在前 20 行中寻找表头（包含“任务名称”等已知列名的行），
  标题行、说明行、空行不会被当作任务；
* 校验阶段只读数据库，不写入；提交阶段在调用方事务内完成，失败整体回滚。
"""
from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import openpyxl

from core import category_service as cs
from core.progress_service import recalc_project
from core.task_service import UNSET, create_task, find_by_code, update_task
from utils.date_utils import DateParseError, parse_date
from utils.helpers import (chapter_group, chapter_sort_key, clean_text,
                           find_task_code_refs, is_blank)

# 目标字段 -> (中文名, 可识别的表头写法)
FIELD_SPECS: dict[str, tuple[str, list[str]]] = {
    "task_code": ("编号", ["编号", "任务编号", "ID", "id"]),
    "title": ("任务名称", ["任务名称", "任务", "名称", "标题"]),
    "priority": ("优先级", ["优先级"]),
    "chapter": ("章节/模块", ["章节/模块", "章节", "论文章节", "所属章节"]),
    "category": ("工作类别", ["工作类别", "类别", "工作模块", "模块"]),
    "description": ("具体工作与要求", ["具体工作与要求", "任务描述", "描述", "具体内容"]),
    "deliverable": ("交付物", ["交付物"]),
    "prerequisites": ("前置依赖/数据来源", ["前置依赖/数据来源", "前置依赖", "数据来源"]),
    "assignee": ("负责人", ["负责人"]),
    "status": ("状态", ["状态", "完成状态"]),
    "progress": ("完成度(%)", ["完成度(%)", "完成度（%）", "完成度", "进度"]),
    "due_date": ("计划完成日期", ["计划完成日期", "截止日期", "到期日期"]),
    "acceptance_criteria": ("验收标准", ["验收标准"]),
    "notes": ("备注", ["备注"]),
    "parent": ("父任务", ["父任务", "父任务编号"]),
    "tags": ("标签", ["标签"]),
    "weight": ("工作量权重", ["工作量权重", "权重"]),
    "progress_mode": ("进度计算模式", ["进度计算模式", "进度模式"]),
}
REQUIRED_FIELDS = ["title"]

DUPLICATE_POLICIES = {
    "skip": "跳过（保留数据库中已有任务）",
    "update": "更新已有任务（按编号匹配）",
    "new": "仍作为新任务导入（编号允许重复）",
}


@dataclass
class SheetData:
    sheet: str
    header_row: int                    # Excel 行号（从 1 开始）
    columns: list[str]
    rows: list[tuple[int, list[Any]]]  # (Excel 行号, 单元格值)
    title: str | None = None           # 表头之上的第一行文字（如工作表顶部的标题行）


@dataclass
class Issue:
    row: int | None
    field: str | None
    level: str        # error / warning
    message: str


@dataclass
class ImportPlan:
    records: list[dict]
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]


def _open(src: str | Path | bytes | BinaryIO):
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    return openpyxl.load_workbook(src, data_only=True, read_only=False)


def list_sheets(src) -> list[str]:
    wb = _open(src)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def _known_headers() -> set[str]:
    return {h for _, aliases in FIELD_SPECS.values() for h in aliases}


def detect_header_row(values: list[list[Any]], max_scan: int = 20) -> int | None:
    """返回表头所在的 0 基行索引：包含“任务名称”类列名且至少 3 个已知列名的首行。"""
    known = _known_headers()
    title_aliases = set(FIELD_SPECS["title"][1])
    for i, row in enumerate(values[:max_scan]):
        cells = {str(v).strip() for v in row if not is_blank(v)}
        if cells & title_aliases and len(cells & known) >= 3:
            return i
    return None


def read_sheet(src, sheet: str | None = None, header_row: int | None = None) -> SheetData:
    wb = _open(src)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        values = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    idx = (header_row - 1) if header_row else detect_header_row(values)
    if idx is None:
        raise ValueError(f"工作表“{ws.title}”中未找到包含“任务名称”的表头行，请手动指定表头行号")
    header = values[idx] if idx < len(values) else []
    columns, seen = [], {}
    for j, h in enumerate(header):
        name = clean_text(h) or f"列{j + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        columns.append(name)
    rows = []
    for i in range(idx + 1, len(values)):
        cells = list(values[i]) + [None] * (len(columns) - len(values[i]))
        cells = cells[: len(columns)]
        if all(is_blank(c) for c in cells):
            continue
        rows.append((i + 1, cells))
    title = None
    for r in values[:idx]:
        t = next((clean_text(c) for c in r if not is_blank(c)), None)
        if t:
            title = t
            break
    return SheetData(sheet=ws.title, header_row=idx + 1, columns=columns, rows=rows, title=title)


def auto_mapping(columns: list[str]) -> dict[str, str | None]:
    mapping: dict[str, str | None] = {}
    used = set()
    for f, (_, aliases) in FIELD_SPECS.items():
        mapping[f] = None
        for a in aliases:
            if a in columns and a not in used:
                mapping[f] = a
                used.add(a)
                break
    return mapping


def build_plan(sheet: SheetData, mapping: dict[str, str | None],
               conn: sqlite3.Connection | None = None, project_id: str | None = None) -> ImportPlan:
    """把工作表行转换为待导入记录并校验（不写数据库）。"""
    col_idx = {c: i for i, c in enumerate(sheet.columns)}
    issues: list[Issue] = []
    if not mapping.get("title"):
        issues.append(Issue(None, "title", "error", "必须为“任务名称”指定对应列"))
        return ImportPlan([], issues)
    records = []
    for excel_row, cells in sheet.rows:
        raw = {f: (cells[col_idx[c]] if c and c in col_idx else None) for f, c in mapping.items()}
        title = clean_text(raw.get("title"))
        code = clean_text(raw.get("task_code"))
        if not title:
            # 既无名称也无编号：说明行/汇总行，跳过；有编号无名称：报错
            if code:
                issues.append(Issue(excel_row, "title", "error", f"第 {excel_row} 行有编号“{code}”但缺少任务名称"))
            continue
        rec: dict[str, Any] = {"_row": excel_row, "title": title, "task_code": code}
        for f in ("priority", "chapter", "category", "description", "deliverable", "prerequisites",
                  "assignee", "status", "acceptance_criteria", "notes", "parent", "tags",
                  "progress_mode"):
            rec[f] = clean_text(raw.get(f))
        # 完成度：允许 10、"10"、"10%"、0.1（百分比格式单元格）
        pv = raw.get("progress")
        if is_blank(pv):
            rec["progress"] = 0.0
        else:
            try:
                s = str(pv).strip().rstrip("%").strip()
                p = float(s)
                if isinstance(pv, float) and 0 < p <= 1 and not str(pv).endswith("%"):
                    issues.append(Issue(excel_row, "progress", "warning",
                                        f"第 {excel_row} 行完成度为 {p}，按原值导入（如为百分比格式请检查）"))
                if not 0 <= p <= 100:
                    raise ValueError
                rec["progress"] = p
            except ValueError:
                issues.append(Issue(excel_row, "progress", "error",
                                    f"第 {excel_row} 行完成度“{pv}”不是 0–100 的数字"))
                rec["progress"] = None
        try:
            rec["due_date"] = parse_date(raw.get("due_date"))
        except DateParseError as e:
            issues.append(Issue(excel_row, "due_date", "error", f"第 {excel_row} 行{e}"))
            rec["due_date"] = None
        wv = raw.get("weight")
        try:
            rec["weight"] = 1.0 if is_blank(wv) else float(wv)
            if rec["weight"] <= 0:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(Issue(excel_row, "weight", "error", f"第 {excel_row} 行工作量权重“{wv}”无效"))
        if rec.get("progress_mode") not in (None, "manual", "auto", "手动", "自动"):
            issues.append(Issue(excel_row, "progress_mode", "warning",
                                f"第 {excel_row} 行进度模式“{rec['progress_mode']}”无法识别，按手动处理"))
        rec["progress_mode"] = {"自动": "auto", "auto": "auto"}.get(rec.get("progress_mode") or "", "manual")
        records.append(rec)

    # 文件内重复编号
    seen: dict[str, int] = {}
    for r in records:
        c = r["task_code"]
        if c:
            if c in seen:
                issues.append(Issue(r["_row"], "task_code", "warning",
                                    f"编号“{c}”在文件中重复（第 {seen[c]} 行与第 {r['_row']} 行）"))
            else:
                seen[c] = r["_row"]
    # 与数据库比较
    if conn is not None and project_id:
        for r in records:
            if r["task_code"] and find_by_code(conn, project_id, r["task_code"]):
                r["_duplicate"] = True
                issues.append(Issue(r["_row"], "task_code", "warning",
                                    f"编号“{r['task_code']}”在当前项目中已存在"))
        for f, table in (("priority", "priorities"), ("status", "statuses"),
                         ("chapter", "chapters"), ("category", "categories")):
            names = sorted({r[f] for r in records if r.get(f)})
            missing = [n for n in names if not cs.find_by_name(conn, table, project_id, n)]
            if missing:
                issues.append(Issue(None, f, "warning",
                                    f"{FIELD_SPECS[f][0]}中的“{'、'.join(missing)}”在项目中不存在，导入时将自动创建"))
    return ImportPlan(records, issues)


_STATUS_KIND_GUESS = [("完成", "done"), ("进行", "in_progress"), ("审核", "review"),
                      ("阻塞", "blocked"), ("暂缓", "paused"), ("取消", "cancelled")]


def _guess_kind(name: str) -> str:
    for kw, kind in _STATUS_KIND_GUESS:
        if kw in name:
            return kind
    return "todo"


def _lookup_id(conn, table: str, project_id: str, name: str | None, cache: dict) -> str | None:
    if not name:
        return None
    key = (table, name)
    if key not in cache:
        found = cs.find_by_name(conn, table, project_id, name)
        if found:
            cache[key] = found["id"]
        else:
            extra = {}
            if table == "statuses":
                extra["kind"] = _guess_kind(name)
            if table == "chapters":
                extra["group_name"] = chapter_group(name)
            cache[key] = cs.create_item(conn, table, project_id, name=name, **extra)
    return cache[key]


def commit_plan(conn: sqlite3.Connection, project_id: str, plan: ImportPlan,
                default_category_id: str | None = None, duplicate_policy: str = "skip",
                source_name: str | None = None) -> dict:
    """在调用方事务内写入。存在错误级问题时拒绝提交。"""
    if plan.errors:
        raise ValueError(f"存在 {len(plan.errors)} 个错误，请修正后再导入")
    if duplicate_policy not in DUPLICATE_POLICIES:
        raise ValueError("未知的重复处理方式")
    cache: dict = {}
    created, updated, skipped = [], [], []
    code_to_id: dict[str, str] = {}
    pending_parent: list[tuple[str, str]] = []
    for r in plan.records:
        data = {
            "task_code": r["task_code"], "title": r["title"], "description": r.get("description"),
            "deliverable": r.get("deliverable"), "prerequisites": r.get("prerequisites"),
            "assignee": r.get("assignee"), "progress": r.get("progress") or 0,
            "due_date": r.get("due_date"), "acceptance_criteria": r.get("acceptance_criteria"),
            "notes": r.get("notes"), "weight": r.get("weight", 1.0),
            "progress_mode": r.get("progress_mode", "manual"),
            "priority_id": _lookup_id(conn, "priorities", project_id, r.get("priority"), cache),
            "status_id": _lookup_id(conn, "statuses", project_id, r.get("status"), cache),
            "chapter_id": _lookup_id(conn, "chapters", project_id, r.get("chapter"), cache),
            "category_id": (_lookup_id(conn, "categories", project_id, r.get("category"), cache)
                            or default_category_id),
        }
        tag_ids = None
        if r.get("tags"):
            tag_ids = [_lookup_id(conn, "tags", project_id, t.strip(), cache)
                       for t in r["tags"].replace("，", ",").replace("、", ",").split(",") if t.strip()]
        existing = find_by_code(conn, project_id, r["task_code"]) if r["task_code"] else []
        if existing and duplicate_policy == "skip":
            skipped.append(r["task_code"])
            code_to_id.setdefault(r["task_code"], existing[0]["id"])
            continue
        if existing and duplicate_policy == "update":
            tid = existing[0]["id"]
            update_task(conn, tid, data, tag_ids=tag_ids if tag_ids is not None else UNSET,
                        on_duplicate="allow", recalc=False)
            updated.append(tid)
        else:
            data["source_file"] = source_name
            data["source_row"] = r["_row"]
            tid = create_task(conn, project_id, data, tag_ids=tag_ids, on_duplicate="allow",
                              recalc=False)
            created.append(tid)
        if r["task_code"]:
            code_to_id[r["task_code"]] = tid
        if r.get("parent"):
            pending_parent.append((tid, r["parent"]))
    # 父任务（按编号或名称匹配，先文件内后数据库）
    unresolved = []
    for tid, ref in pending_parent:
        pid_ = code_to_id.get(ref)
        if not pid_:
            m = find_by_code(conn, project_id, ref)
            if not m:
                m = [dict(x) for x in conn.execute(
                    "SELECT id FROM tasks WHERE project_id=? AND title=?", (project_id, ref))]
            pid_ = m[0]["id"] if len(m) == 1 else None
        if pid_ and pid_ != tid:
            update_task(conn, tid, {"parent_id": pid_}, recalc=False)
        else:
            unresolved.append(ref)
    recalc_project(conn, project_id)
    return {"created": len(created), "updated": len(updated), "skipped": len(skipped),
            "skipped_codes": skipped, "unresolved_parents": unresolved, "created_ids": created}


def code_reference_hints(records: list[dict]) -> list[dict]:
    """列出“前置依赖/数据来源”原文中引用到的任务编号（仅提示，不自动建立依赖）。"""
    codes = {r["task_code"] for r in records if r.get("task_code")}
    out = []
    for r in records:
        refs = [c for c in find_task_code_refs(r.get("prerequisites")) if c in codes and c != r["task_code"]]
        if refs:
            out.append({"task_code": r["task_code"], "title": r["title"], "refs": refs,
                        "text": r.get("prerequisites")})
    return out


def ordered_chapters(names: list[str]) -> list[str]:
    return sorted(set(n for n in names if n), key=chapter_sort_key)
