"""统计分析：优先级、状态、类别、章节完成率、负责人、完成度分布、逾期、创建与完成趋势。"""
from datetime import date, timedelta

import streamlit as st

from components import charts
from components.sidebar import require_project
from components.task_form import load_lookups
from components.ui import empty_state
from core import statistics_service as stx
from core.database import get_project_settings, read_conn
from core.progress_service import METRIC_LABELS, overall_metrics
from core.project_service import list_projects
from core.task_service import list_tasks_df

proj = require_project()
with read_conn() as conn:
    projects = list_projects(conn)
st.title("统计分析")

f1, f2, f3, f4 = st.columns([2, 1.4, 2.2, 1.2])
names = {p["id"]: p["name"] for p in projects}
pid = f1.selectbox("项目", list(names), index=list(names).index(proj["id"]), format_func=names.get,
                   key="stats_project")
field_lbl = {"created_at": "创建时间", "updated_at": "更新时间", "due_date": "截止日期"}
date_field = f2.selectbox("日期范围依据", list(field_lbl), format_func=field_lbl.get, key=f"flt_{pid}_stats_field")
use_range = f4.toggle("启用日期筛选", key=f"flt_{pid}_stats_use")
rng = f3.date_input("日期范围", value=(date.today() - timedelta(days=90), date.today()),
                    key=f"flt_{pid}_stats_range", disabled=not use_range)
with read_conn() as conn:
    df_all = list_tasks_df(conn, pid)
    lk = load_lookups(conn, pid)
    ps = get_project_settings(conn, pid)
ex_p = bool(ps.get("stats_exclude_paused"))
start, end = (rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else (None, None))
df = stx.filter_by_date_range(df_all, start, end, date_field) if use_range else df_all
st.caption(f"统计范围：{len(df)} 项任务" + (f"（{field_lbl[date_field]}在 {start} 至 {end}，"
           "该字段为空的任务不计入）" if use_range else "（全部任务）") +
           f"。平均完成度只统计有效叶子任务{'（排除暂缓）' if ex_p else ''}。")

if df.empty:
    empty_state("当前范围内没有任务")
    st.stop()

m = overall_metrics(df, ex_p, ps.get("completion_basis", "status"))
k = st.columns(5)
k[0].metric("任务数", len(df))
k[1].metric("A 任务完成率", f"{m['A']:g}%", help=METRIC_LABELS["A"])
k[2].metric("B 平均完成度", f"{m['B']:g}%", help=METRIC_LABELS["B"])
k[3].metric("C 加权完成度", f"{m['C']:g}%", help=METRIC_LABELS["C"])
k[4].metric("逾期任务", int(df["overdue"].sum()))

r1 = st.columns(2)
with r1[0]:
    charts.count_bar(stx.priority_counts(df, lk["priorities"]), "1 优先级分布", key="s_pri")
with r1[1]:
    charts.count_bar(stx.status_counts(df, lk["statuses"]), "2 状态分布", key="s_st")
r2 = st.columns(2)
with r2[0]:
    charts.count_bar(stx.category_counts(df, lk["categories"]), "3 工作类别分布", horizontal=True, key="s_cat")
with r2[1]:
    by_group = st.toggle("按章汇总", value=True, key=f"flt_{pid}_stats_grp")
    charts.progress_bars(stx.chapter_progress(df, lk["chapters"], by_group, ex_p), "4 章节完成率", key="s_ch")
r3 = st.columns(2)
with r3[0]:
    wl = stx.assignee_workload(df, ex_p)
    charts.workload_bar(wl, "5 负责人任务数量（组合负责人拆分后分别计数）", key="s_wl")
with r3[1]:
    charts.simple_bar(stx.progress_distribution(df), "完成度区间", "任务数", "6 完成度分布（叶子任务）",
                      key="s_dist", pct_col="占比")
r4 = st.columns(2)
with r4[0]:
    od = stx.overdue_by_group(df, "priority")
    st.markdown(f"**7 逾期任务数量：{int(df['overdue'].sum())} 项**")
    if od.empty:
        empty_state("没有逾期任务")
    else:
        charts.simple_bar(od, "名称", "逾期任务数", "按优先级", color="#DC2626", key="s_od")
with r4[1]:
    freq = st.segmented_control("粒度", ["D", "W"], default="W", format_func={"D": "按日", "W": "按周"}.get,
                                key=f"flt_{pid}_stats_freq") or "W"
    charts.trend_chart(stx.creation_completion_trend(df, start if use_range else None,
                                                     end if use_range else None, freq),
                       "8 任务创建与完成趋势", key="s_trend")

with st.expander("负责人明细表"):
    st.dataframe(wl, hide_index=True, width="stretch",
                 column_config={"平均完成度": st.column_config.NumberColumn(format="%.1f%%")})
