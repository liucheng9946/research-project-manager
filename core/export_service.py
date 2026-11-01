"""Excel 导出（OpenPyXL 格式化）：项目概览、全部任务、任务层级、分类统计、章节统计、使用说明。

导出的“全部任务”工作表表头与导入识别规则一致，可直接重新导入；
完成度以 0–100 数值存储并显示为百分比（数字格式 0"%"），重新导入不会被误读为小数。
"""
from __future__ import annotations

import io
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from core import category_service as cs
from core import statistics_service as st
from core.database import get_project_settings
from core.progress_service import METRIC_LABELS, overall_metrics
from core.project_service import get_project, list_projects
from core.task_service import list_tasks_df
from utils.date_utils import to_date

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
PCT_FMT = '0"%"'
DATE_FMT = "yyyy-mm-dd"

TASK_COLUMNS = [  # (表头, 字段/函数, 列宽)
    ("编号", "task_code", 9), ("任务名称", "title", 34), ("工作类别", "category", 16),
    ("章节", "chapter", 11), ("父任务", "_parent", 12), ("优先级", "priority", 8),
    ("状态", "status", 9), ("完成度(%)", "progress", 10), ("负责人", "assignee", 14),
    ("截止日期", "_due", 12), ("任务描述", "description", 44), ("交付物", "deliverable", 18),
    ("前置依赖/数据来源", "prerequisites", 26), ("前置任务", "_deps", 12),
    ("验收标准", "acceptance_criteria", 40), ("标签", "_tags", 14), ("备注", "notes", 24),
    ("工作量权重", "weight", 10), ("进度计算模式", "_mode", 12),
]


def _header(ws, headers: list[str], widths: list[int] | None = None, row: int = 1) -> None:
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if widths:
            ws.column_dimensions[get_column_letter(j)].width = widths[j - 1]
    ws.row_dimensions[row].height = 22


def _task_values(df: pd.DataFrame) -> list[list]:
    by_id = df.set_index("id")
    code_counts = df["task_code"].value_counts()
    rows = []
    for r in df.itertuples(index=False):
        parent = None
        if r.parent_id and r.parent_id in by_id.index:
            pc = by_id.loc[r.parent_id, "task_code"]
            parent = pc if pc and code_counts.get(pc, 0) == 1 else by_id.loc[r.parent_id, "title"]
        vals = []
        for _, f, _ in TASK_COLUMNS:
            if f == "_parent":
                v = parent
            elif f == "_due":
                v = to_date(r.due_date)
            elif f == "_deps":
                v = "、".join(r.depends_on) or None
            elif f == "_tags":
                v = "、".join(r.tags) or None
            elif f == "_mode":
                v = "自动" if r.progress_mode == "auto" else "手动"
            else:
                v = getattr(r, f)
            vals.append(v)
        rows.append(vals)
    return rows


def _write_tasks_sheet(ws, df: pd.DataFrame, project_col: list[str] | None = None) -> None:
    cols = list(TASK_COLUMNS)
    if project_col is not None:
        cols = [("项目", "_project", 14)] + cols
    _header(ws, [c[0] for c in cols], [c[2] for c in cols])
    values = _task_values(df)
    for i, vals in enumerate(values):
        if project_col is not None:
            vals = [project_col[i]] + vals
        ws.append(vals)
    n = len(values) + 1
    hdr = [c[0] for c in cols]
    pc = get_column_letter(hdr.index("完成度(%)") + 1)
    dc = get_column_letter(hdr.index("截止日期") + 1)
    sc = get_column_letter(hdr.index("状态") + 1)
    for row in ws.iter_rows(min_row=2, max_row=n):
        for c in row:
            c.alignment, c.border = WRAP, BORDER
    for r in range(2, n + 1):
        ws[f"{pc}{r}"].number_format = PCT_FMT
        ws[f"{dc}{r}"].number_format = DATE_FMT
    ws.freeze_panes = "C2" if project_col is None else "D2"
    if n > 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{n}"
        ws.conditional_formatting.add(f"{pc}2:{pc}{n}", DataBarRule(
            start_type="num", start_value=0, end_type="num", end_value=100, color="60A5FA"))
        ws.conditional_formatting.add(f"{sc}2:{sc}{n}", CellIsRule(
            operator="equal", formula=['"已完成"'], font=Font(color="15803D", bold=True)))
        ws.conditional_formatting.add(f"{sc}2:{sc}{n}", CellIsRule(
            operator="equal", formula=['"阻塞"'], font=Font(color="B91C1C", bold=True)))
        ws.conditional_formatting.add(f"{dc}2:{dc}{n}", FormulaRule(
            formula=[f'AND(ISNUMBER({dc}2),{dc}2<TODAY(),{sc}2<>"已完成")'],
            fill=PatternFill("solid", fgColor="FEE2E2"), font=Font(color="B91C1C")))


def _write_table(ws, df: pd.DataFrame, widths: list[int] | None = None, pct_cols=(), start_row=1):
    _header(ws, list(df.columns), widths or [16] * len(df.columns), row=start_row)
    for r in df.itertuples(index=False):
        ws.append(list(r))
    for row in ws.iter_rows(min_row=start_row + 1, max_row=ws.max_row):
        for c in row:
            c.border = BORDER
    for col in pct_cols:
        j = list(df.columns).index(col) + 1
        for r in range(start_row + 1, ws.max_row + 1):
            ws.cell(row=r, column=j).number_format = '0.0"%"'


USAGE = [
    "使用说明",
    "1. 本文件由“科研项目管理系统”导出，SQLite 数据库是唯一正式数据源；在此修改不会自动同步回系统。",
    "2. 如需把修改带回系统：在“数据管理 → Excel 导入”中上传本文件，选择“全部任务”工作表，"
    "重复编号处理方式选择“更新已有任务”。",
    "3. 完成度列为 0–100 的数值，单元格显示为百分比。",
    "4. “前置依赖/数据来源”为原始说明文字；“前置任务”为系统中建立的结构化依赖（编号）。",
    "5. “任务层级”工作表按父子关系缩进显示；复杂的层级、依赖与配置请使用 JSON 全量备份保存。",
    "6. 整体进度只统计叶子任务（没有子任务的任务），父任务不重复计入。",
]


def export_project_excel(conn: sqlite3.Connection, project_id: str, dest,
                         df: pd.DataFrame | None = None, title_note: str | None = None):
    """导出单个项目。df 为筛选后的任务（None 表示全部）。dest 为路径或 BytesIO。"""
    proj = get_project(conn, project_id)
    full = list_tasks_df(conn, project_id)
    data = full if df is None else df
    data = data.sort_values("tree_index") if not data.empty else data
    settings = get_project_settings(conn, project_id)
    ex_p = bool(settings.get("stats_exclude_paused"))
    wb = Workbook()
    ws = wb.active
    ws.title = "项目概览"
    m = overall_metrics(data, ex_p, settings.get("completion_basis", "status"))
    c = st.basic_counts(data)
    info = [("项目名称", proj["name"]), ("项目描述", proj["description"] or ""),
            ("导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("导出范围", title_note or ("全部任务" if df is None else "当前筛选结果")),
            ("任务总数", c["total"]), ("已完成", c["done"]), ("进行中", c["in_progress"]),
            ("阻塞", c["blocked"]), ("逾期", c["overdue"]), ("有效叶子任务数", m["leaf_count"]),
            ("A " + METRIC_LABELS["A"], m["A"]), ("B " + METRIC_LABELS["B"], m["B"]),
            ("C " + METRIC_LABELS["C"], m["C"])]
    _header(ws, ["项目", "值"], [44, 60])
    for k, v in info:
        ws.append([k, v])
    for r in range(len(info) - 1, len(info) + 2):
        ws.cell(row=r, column=2).number_format = '0.0"%"'
    _write_tasks_sheet(wb.create_sheet("全部任务"), data)

    wh = wb.create_sheet("任务层级")
    _header(wh, ["层级", "编号", "任务名称（缩进表示层级）", "状态", "完成度(%)", "进度模式", "权重", "子任务数"],
            [6, 9, 60, 9, 10, 10, 8, 9])
    for r in data.itertuples(index=False):
        wh.append([int(r.depth) + 1, r.task_code, "    " * int(r.depth) + r.title, r.status,
                   r.progress, "自动" if r.progress_mode == "auto" else "手动", r.weight, r.n_children])
    for i in range(2, wh.max_row + 1):
        wh.cell(row=i, column=5).number_format = PCT_FMT
    wh.freeze_panes = "A2"

    cats = cs.list_items(conn, "categories", project_id)
    _write_table(wb.create_sheet("分类统计"), st.group_progress(data, "category", cats, ex_p)
                 .drop(columns=["颜色"]), [24, 9, 9, 9, 9, 14], pct_cols=["平均完成度"])
    chs = cs.list_items(conn, "chapters", project_id)
    _write_table(wb.create_sheet("章节统计"), st.chapter_progress(data, chs, False, ex_p)
                 .drop(columns=["颜色"]), [18, 9, 9, 9, 9, 14], pct_cols=["平均完成度"])
    wu = wb.create_sheet("使用说明")
    wu.column_dimensions["A"].width = 120
    for line in USAGE:
        wu.append([line])
    wu["A1"].font = Font(bold=True, size=13)
    return _save(wb, dest)


def export_hierarchy_excel(conn, project_id, dest):
    """仅导出任务层级（含完整任务信息，按树形顺序）。"""
    df = list_tasks_df(conn, project_id)
    df = df.sort_values("tree_index") if not df.empty else df
    wb = Workbook()
    ws = wb.active
    ws.title = "任务层级"
    _write_tasks_sheet(ws, df)
    for i, r in enumerate(df.itertuples(index=False), start=2):
        ws.cell(row=i, column=2).value = "    " * int(r.depth) + r.title
    return _save(wb, dest)


def export_statistics_excel(conn, project_id, dest):
    df = list_tasks_df(conn, project_id)
    settings = get_project_settings(conn, project_id)
    ex_p = bool(settings.get("stats_exclude_paused"))
    wb = Workbook()
    ws = wb.active
    ws.title = "状态分布"
    _write_table(ws, st.status_counts(df, cs.list_items(conn, "statuses", project_id))
                 .drop(columns=["颜色"]), [14, 9, 10], pct_cols=["占比"])
    _write_table(wb.create_sheet("优先级分布"), st.priority_counts(
        df, cs.list_items(conn, "priorities", project_id)).drop(columns=["颜色"]),
        [14, 9, 10], pct_cols=["占比"])
    _write_table(wb.create_sheet("分类统计"), st.group_progress(
        df, "category", cs.list_items(conn, "categories", project_id), ex_p).drop(columns=["颜色"]),
        [24, 9, 9, 9, 9, 14], pct_cols=["平均完成度"])
    _write_table(wb.create_sheet("章节统计"), st.chapter_progress(
        df, cs.list_items(conn, "chapters", project_id), False, ex_p).drop(columns=["颜色"]),
        [18, 9, 9, 9, 9, 14], pct_cols=["平均完成度"])
    _write_table(wb.create_sheet("负责人"), st.assignee_workload(df, ex_p), [14, 9, 9, 9, 9, 9, 14],
                 pct_cols=["平均完成度"])
    _write_table(wb.create_sheet("完成度分布"), st.progress_distribution(df), [14, 9, 10], pct_cols=["占比"])
    return _save(wb, dest)


def export_all_projects_excel(conn, dest):
    wb = Workbook()
    ws = wb.active
    ws.title = "项目列表"
    _header(ws, ["项目名称", "描述", "任务数", "已完成", "平均完成度(叶子)", "创建时间"], [24, 40, 9, 9, 16, 20])
    frames, names = [], []
    for p in list_projects(conn):
        df = list_tasks_df(conn, p["id"])
        df = df.sort_values("tree_index") if not df.empty else df
        settings = get_project_settings(conn, p["id"])
        m = overall_metrics(df, bool(settings.get("stats_exclude_paused")))
        ws.append([p["name"], p["description"], len(df), st.basic_counts(df)["done"], m["B"], p["created_at"]])
        frames.append(df)
        names += [p["name"]] * len(df)
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=5).number_format = '0.0"%"'
    all_df = pd.concat(frames, ignore_index=True) if frames and any(len(f) for f in frames) else \
        list_tasks_df(conn, "__none__")
    _write_tasks_sheet(wb.create_sheet("全部任务"), all_df, project_col=names)
    wu = wb.create_sheet("使用说明")
    wu.column_dimensions["A"].width = 120
    for line in USAGE:
        wu.append([line])
    return _save(wb, dest)


def _save(wb: Workbook, dest):
    if dest is None:
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    if isinstance(dest, (str, Path)):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)
    return dest
