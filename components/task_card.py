"""任务卡片视图。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components.task_form import open_detail
from components.ui import badge, empty_state, progress_html
from utils.formatters import fmt_text


def render_cards(df: pd.DataFrame, key: str, columns: int = 3) -> None:
    if df.empty:
        empty_state("没有符合条件的任务")
        return
    rows = [df.iloc[i:i + columns] for i in range(0, len(df), columns)]
    for chunk in rows:
        cols = st.columns(columns)
        for col, r in zip(cols, chunk.itertuples(index=False)):
            with col.container(border=True):
                label = (f"{r.task_code}　" if r.task_code else "") + r.title
                st.button(label, key=f"{key}_{r.id}", type="tertiary", on_click=open_detail, args=(r.id,))
                st.markdown(badge(r.priority, r.priority_color) + " " + badge(r.status, r.status_color)
                            + (" " + badge("逾期", "#DC2626") if r.overdue else ""), unsafe_allow_html=True)
                st.markdown(progress_html(r.progress, r.status_color or "#2563EB"), unsafe_allow_html=True)
                st.markdown(f"<span class='rpm-muted'>负责人：{fmt_text(r.assignee)}　·　截止：{fmt_text(r.due_date)}"
                            f"{'　·　子任务 ' + str(r.n_children) if r.n_children else ''}</span>",
                            unsafe_allow_html=True)


def compact_list(df: pd.DataFrame, key: str, show: str = "due", limit: int = 8) -> None:
    """紧凑任务列表（总览页使用）。show: due / updated / status。"""
    if df.empty:
        st.caption("无")
        return
    for r in df.head(limit).itertuples(index=False):
        c1, c2 = st.columns([3, 1.3])
        label = (f"{r.task_code} " if r.task_code else "") + r.title
        c1.button(label, key=f"{key}_{r.id}", type="tertiary", on_click=open_detail, args=(r.id,))
        if show == "due":
            extra = f"<span style='color:{'#DC2626' if r.overdue else '#374151'}'>{r.due_date}</span>"
        elif show == "updated":
            extra = f"<span class='rpm-muted'>{str(r.updated_at)[5:16]}</span>"
        else:
            extra = badge(r.status, r.status_color)
        c2.markdown(extra, unsafe_allow_html=True)
    if len(df) > limit:
        st.caption(f"另有 {len(df) - limit} 项…")
