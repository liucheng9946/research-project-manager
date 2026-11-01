"""任务表格视图：分页、行内快速修改（状态/优先级/完成度/截止日期）、勾选后批量操作。"""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from core import task_service as ts
from components.task_form import open_delete, open_detail
from components.ui import empty_state, safe_write
from utils.date_utils import to_date


def paginate(df: pd.DataFrame, key: str, page_size: int) -> tuple[pd.DataFrame, int]:
    n = len(df)
    pages = max(1, math.ceil(n / page_size))
    pk = f"{key}_page"
    if st.session_state.get(pk, 1) > pages:
        st.session_state[pk] = 1
    if pages > 1:
        c1, c2 = st.columns([1, 5])
        page = c1.number_input(f"页码（共 {pages} 页）", 1, pages, key=pk)
        c2.caption(f"共 {n} 项，每页 {page_size} 项")
    else:
        page = 1
    start = (page - 1) * page_size
    return df.iloc[start:start + page_size], page


def _version_key(pid: str) -> str:
    return f"sel_tbl_ver_{pid}"


def render_table(df: pd.DataFrame, pid: str, lookups: dict, page_size: int) -> None:
    if df.empty:
        empty_state("没有符合条件的任务")
        return
    page_df, page = paginate(df, f"sel_tbl_{pid}", page_size)
    pri_names = [p["name"] for p in lookups["priorities"]]
    st_names = [s["name"] for s in lookups["statuses"]]
    pri_id = {p["name"]: p["id"] for p in lookups["priorities"]}
    st_id = {s["name"]: s["id"] for s in lookups["statuses"]}
    page_df = page_df.reset_index(drop=True)
    view = pd.DataFrame({
        "选择": False,
        "编号": page_df["task_code"].fillna(""),
        "任务名称": ["　" * int(d) + t for d, t in zip(page_df["depth"], page_df["title"])]
        if st.session_state.get(f"flt_{pid}_list_sort") == "custom" else page_df["title"],
        "类别": page_df["category"].fillna(""),
        "章节": page_df["chapter"].fillna(""),
        "优先级": page_df["priority"],
        "负责人": page_df["assignee"].fillna(""),
        "状态": page_df["status"],
        "完成度": page_df["progress"].round(1),
        "截止日期": [to_date(d) for d in page_df["due_date"]],
        "逾期": page_df["overdue"].map(lambda x: "逾期" if x else ""),
    })
    view.index = page_df["id"].values  # 先按位置构造，再以任务 ID 作为索引（避免索引错位）
    ver = st.session_state.get(_version_key(pid), 0)
    edited = st.data_editor(
        view, key=f"sel_tbl_editor_{pid}_{page}_{ver}", hide_index=True, width="stretch",
        height=min(38 * len(view) + 40, 720),
        disabled=["编号", "任务名称", "类别", "章节", "负责人", "逾期"],
        column_config={
            "选择": st.column_config.CheckboxColumn("选择", width=48),
            "编号": st.column_config.TextColumn(width=60),
            "任务名称": st.column_config.TextColumn(width=300),
            "类别": st.column_config.TextColumn(width=120),
            "章节": st.column_config.TextColumn(width=70),
            "负责人": st.column_config.TextColumn(width=95),
            "优先级": st.column_config.SelectboxColumn(options=pri_names, width=62),
            "状态": st.column_config.SelectboxColumn(options=st_names, width=74),
            "完成度": st.column_config.NumberColumn("完成度", min_value=0, max_value=100, step=5,
                                                 format="%.0f%%", width=62),
            "截止日期": st.column_config.DateColumn(format="YYYY-MM-DD", width=96),
            "逾期": st.column_config.TextColumn(width=44),
        })
    # ---- 行内修改 ----
    changes: dict[str, dict] = {}
    locked = set(page_df.loc[page_df["has_children"] & (page_df["progress_mode"] == "auto"), "id"])
    for tid in view.index:
        a, b = view.loc[tid], edited.loc[tid]
        ch = {}
        if b["优先级"] != a["优先级"] and b["优先级"] in pri_id:
            ch["priority_id"] = pri_id[b["优先级"]]
        if b["状态"] != a["状态"] and b["状态"] in st_id:
            ch["status_id"] = st_id[b["状态"]]
        if not pd.isna(b["完成度"]) and abs(float(b["完成度"]) - float(a["完成度"])) > 1e-9 and tid not in locked:
            ch["progress"] = float(b["完成度"])
        if (b["截止日期"] or None) != (a["截止日期"] or None):
            ch["due_date"] = b["截止日期"]
        if ch:
            changes[tid] = ch
    selected = [tid for tid in edited.index if edited.loc[tid, "选择"]]
    c1, c2, c3, c4 = st.columns([1.3, 1.1, 1.3, 3])
    if c1.button(f"保存表格修改（{len(changes)}）", type="primary", disabled=not changes, key=f"sel_save_{pid}"):
        def _do(conn):
            for tid, ch in changes.items():
                ts.update_task(conn, tid, ch)
            st.session_state[_version_key(pid)] = ver + 1
        safe_write(_do, f"已保存 {len(changes)} 项任务的修改")
    if locked & set(edited.index[edited["完成度"] != view["完成度"]]):
        st.caption("提示：自动计算进度的父任务不能直接修改完成度，已忽略。")
    if c2.button("查看详情", disabled=len(selected) != 1, key=f"sel_detail_{pid}",
                 help="勾选一项任务后查看详情"):
        open_detail(selected[0])
        st.rerun()
    with c3.popover(f"批量操作（{len(selected)}）", disabled=not selected, width="stretch"):
        bulk_panel(selected, df, pid, lookups)
    if changes:
        c4.caption(f"有 {len(changes)} 项未保存的修改")


BULK_LABELS = {"status_id": "状态", "priority_id": "优先级", "assignee": "负责人",
               "due_date": "截止日期", "category_id": "工作类别", "chapter_id": "论文章节",
               "progress": "完成度", "_delete": "删除"}


def bulk_panel(selected: list[str], df: pd.DataFrame, pid: str, lookups: dict) -> None:
    sub = df[df["id"].isin(selected)]
    st.markdown(f"**已选 {len(sub)} 项任务**")
    st.caption("、".join((sub["task_code"].fillna("") + " " + sub["title"]).str.strip().head(15))
               + ("…" if len(sub) > 15 else ""))
    action = st.selectbox("操作", list(BULK_LABELS), format_func=BULK_LABELS.get, key=f"sel_bulk_act_{pid}")
    if action == "_delete":
        st.warning(f"将删除 {len(sub)} 项任务，下一步会显示影响范围并要求确认。")
        if st.button("继续", key=f"sel_bulk_del_{pid}"):
            open_delete(selected)
            st.rerun()
        return
    value, label = None, ""
    if action in ("status_id", "priority_id", "category_id", "chapter_id"):
        table = {"status_id": "statuses", "priority_id": "priorities", "category_id": "categories",
                 "chapter_id": "chapters"}[action]
        items = lookups[table]
        opts = ([None] if action in ("category_id", "chapter_id") else []) + [i["id"] for i in items]
        names = {i["id"]: i["name"] for i in items}
        value = st.selectbox("改为", opts, format_func=lambda x: names.get(x, "（置空）"), key=f"sel_bulk_v_{pid}_{action}")
        label = names.get(value, "（置空）")
    elif action == "assignee":
        value = st.text_input("负责人（多人用 + 分隔）", key=f"sel_bulk_v_{pid}_a")
        label = value or "（置空）"
    elif action == "due_date":
        value = st.date_input("截止日期（可清空）", value=None, key=f"sel_bulk_v_{pid}_d")
        label = str(value) if value else "（置空）"
    elif action == "progress":
        value = st.slider("完成度（%）", 0, 100, 0, key=f"sel_bulk_v_{pid}_p")
        label = f"{value}%"
        locked = int((sub["has_children"] & (sub["progress_mode"] == "auto")).sum())
        if locked:
            st.caption(f"其中 {locked} 项为自动计算进度的父任务，不会被修改。")
    st.info(f"影响范围：{len(sub)} 项任务的“{BULK_LABELS[action]}”将改为 {label}")
    if st.button("确认批量修改", type="primary", key=f"sel_bulk_go_{pid}"):
        ids = selected
        if action == "progress":
            ids = list(sub.loc[~(sub["has_children"] & (sub["progress_mode"] == "auto")), "id"])

        def _do(conn):
            n = ts.bulk_update(conn, ids, action, value)
            st.session_state[_version_key(pid)] = st.session_state.get(_version_key(pid), 0) + 1
            return n
        safe_write(_do, f"批量修改完成")
