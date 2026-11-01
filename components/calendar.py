"""日历视图：月视图、周视图、列表视图。没有截止日期的任务不会被安排到任何一天。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from components.task_form import open_detail
from components.ui import empty_state
from utils.date_utils import month_matrix, to_date, week_dates, weekday_headers


def _color(r, color_by: str) -> str:
    if r.overdue:
        return "#DC2626"
    return (r.priority_color if color_by == "priority" else r.status_color) or "#6B7280"


def _task_chip(r, color_by: str, key: str) -> None:
    c = _color(r, color_by)
    label = (f"{r.task_code} " if r.task_code else "") + r.title
    label = label if len(label) <= 18 else label[:17] + "…"
    st.markdown(f"<div style='border-left:3px solid {c};padding-left:4px;margin:1px 0'></div>",
                unsafe_allow_html=True)
    st.button(label, key=key, type="tertiary", on_click=open_detail, args=(r.id,),
              help=f"{r.title}｜{r.priority or ''}｜{r.status or ''}｜{r.progress:g}%")


def _by_day(df: pd.DataFrame) -> dict[date, list]:
    out: dict[date, list] = {}
    for r in df[df["due_date"].notna()].itertuples(index=False):
        out.setdefault(to_date(r.due_date), []).append(r)
    return out


def month_view(df: pd.DataFrame, year: int, month: int, week_start: int, color_by: str, pid: str) -> None:
    days = _by_day(df)
    heads = st.columns(7)
    for h, name in zip(heads, weekday_headers(week_start)):
        h.markdown(f"<div style='text-align:center' class='rpm-muted'>{name}</div>", unsafe_allow_html=True)
    today = date.today()
    for week in month_matrix(year, month, week_start):
        cols = st.columns(7)
        for col, d in zip(cols, week):
            with col.container(border=True, height=128):
                if d is None:
                    continue
                style = "color:#2563EB;font-weight:600" if d == today else ""
                items = days.get(d, [])
                st.markdown(f"<span class='rpm-cal-num' style='{style}'>{d.day}"
                            f"{'　今天' if d == today else ''}</span>", unsafe_allow_html=True)
                for r in items[:4]:
                    _task_chip(r, color_by, f"cal_m_{pid}_{d}_{r.id}")
                if len(items) > 4:
                    if st.button(f"另有 {len(items) - 4} 项", key=f"cal_more_{pid}_{d}", type="tertiary"):
                        st.session_state[f"cal_day_{pid}"] = d
                        st.rerun()


def week_view(df: pd.DataFrame, ref: date, week_start: int, color_by: str, pid: str) -> None:
    days = _by_day(df)
    dates = week_dates(ref, week_start)
    cols = st.columns(7)
    for col, d, name in zip(cols, dates, weekday_headers(week_start)):
        with col.container(border=True):
            hl = "color:#2563EB;font-weight:600" if d == date.today() else ""
            st.markdown(f"<div style='{hl}'>{name}<br><span class='rpm-muted'>{d.isoformat()}</span></div>",
                        unsafe_allow_html=True)
            items = days.get(d, [])
            if not items:
                st.caption("—")
            for r in items:
                _task_chip(r, color_by, f"cal_w_{pid}_{d}_{r.id}")


def day_list(df: pd.DataFrame, d: date, color_by: str, pid: str) -> None:
    items = _by_day(df).get(d, [])
    st.markdown(f"**{d.isoformat()} 的任务（{len(items)}）**")
    if not items:
        st.caption("当天没有到期任务")
    for r in items:
        _task_chip(r, color_by, f"cal_d_{pid}_{d}_{r.id}")
