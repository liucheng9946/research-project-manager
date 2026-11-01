"""项目总览（首页）：统计卡片、整体进度、工作模块进度、章节进度、近期任务、分布图表。"""
from datetime import date

import streamlit as st

from components import charts
from components.sidebar import global_setting, require_project
from components.task_card import compact_list
from components.task_form import load_lookups, open_new
from components.ui import empty_state, progress_html
from core import statistics_service as stx
from core.database import get_project_settings, read_conn
from core.progress_service import METRIC_LABELS, overall_metrics
from core.task_service import list_tasks_df

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    df = list_tasks_df(conn, pid)
    lk = load_lookups(conn, pid)
    ps = get_project_settings(conn, pid)
ex_p = bool(ps.get("stats_exclude_paused"))
basis = ps.get("completion_basis", "status")
metric_key = ps.get("overall_metric", "B")

# ---- 顶部 ----
h1, h2 = st.columns([4, 1])
h1.title(proj["name"])
h1.caption(f"{proj['description'] or ''}　·　今天 {date.today().isoformat()}　·　最近更新："
           f"{df['updated_at'].max() if not df.empty else '—'}")
h2.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
h2.button("＋ 新建任务", type="primary", on_click=open_new, width="stretch")

counts = stx.basic_counts(df)
m = overall_metrics(df, ex_p, basis)
c = st.columns(5)
c[0].metric("任务总数", counts["total"])
c[1].metric("已完成", counts["done"])
c[2].metric("进行中", counts["in_progress"])
c[3].metric("阻塞", counts["blocked"])
c[4].metric("整体完成率", f"{m[metric_key]:g}%", help=METRIC_LABELS[metric_key])

# ---- 整体进度 ----
st.markdown("### 整体进度")
st.markdown(progress_html(m[metric_key]), unsafe_allow_html=True)
k = st.columns([1, 1, 1, 2.2])
k[0].metric("A 任务完成率", f"{m['A']:g}%", help=METRIC_LABELS["A"])
k[1].metric("B 平均完成度", f"{m['B']:g}%", help=METRIC_LABELS["B"])
k[2].metric("C 加权完成度", f"{m['C']:g}%", help=METRIC_LABELS["C"])
with k[3]:
    sc = stx.status_counts(df, lk["statuses"])
    st.markdown("**各状态任务数**")
    st.markdown("　".join(f"<span style='color:{r['颜色']}'>●</span> {r['名称']} <b>{r['任务数']}</b>"
                         for _, r in sc.iterrows()) or "—", unsafe_allow_html=True)
st.caption(f"统计口径：有效叶子任务 {m['leaf_count']} 项（无子任务、非已取消"
           f"{'、非暂缓' if ex_p else ''}），父任务不重复计入；已完成判定：{'状态为已完成类' if basis == 'status' else '完成度 100%'}。"
           "可在“系统设置 → 进度统计口径”修改。")

# ---- 工作模块 / 章节 ----
left, right = st.columns(2)
with left.container(key="blk_modules"):
    st.markdown("### 工作模块进度")
    gp = stx.group_progress(df, "category", lk["categories"], ex_p)
    if gp.empty:
        empty_state("暂无工作模块")
    for _, r in gp.iterrows():
        a, b = st.columns([2.2, 3])
        a.markdown(f"<span style='color:{r['颜色']}'>■</span> **{r['名称']}**<br>"
                   f"<span class='rpm-muted'>{r['任务数']} 项　已完成 {r['已完成']}　进行中 {r['进行中']}</span>",
                   unsafe_allow_html=True)
        b.markdown("<div style='height:10px'></div>" + progress_html(r["平均完成度"], r["颜色"]),
                   unsafe_allow_html=True)
with right.container(key="blk_chapters"):
    st.markdown("### 章节进度")
    by_group = st.toggle("按章汇总（第6.1节等并入第6章）", value=True, key=f"ov_grp_{pid}")
    if lk["chapters"] or (not df.empty and df["chapter"].notna().any()):
        cp = stx.chapter_progress(df, lk["chapters"], by_group, ex_p)
        _d = df.assign(_grp=(df["chapter_group"].where(df["chapter_group"].notna(), df["chapter"])
                             if by_group else df["chapter"]).fillna(stx.NO_VALUE))
        st.caption("每行即一章/节的进度；点击章节名展开/收起该章的任务，点击任务名可打开详情。")
        open_key = f"ov_open_ch_{pid}_{int(by_group)}"

        def _toggle(name):
            st.session_state[open_key] = None if st.session_state.get(open_key) == name else name

        for i, (_, r) in enumerate(cp[cp["任务数"] > 0].iterrows()):
            name = r["名称"]
            is_open = st.session_state.get(open_key) == name
            with st.container(key=f"chc{i % 6}_{pid}_{int(by_group)}_{i}"):
                a, b, c3 = st.columns([1.6, 2.4, 2.2], vertical_alignment="center")
                a.button(f"{'▾' if is_open else '▸'} {name}", key=f"ov_chbtn_{pid}_{int(by_group)}_{name}",
                         type="tertiary", on_click=_toggle, args=(name,))
                b.markdown(progress_html(r["平均完成度"], r.get("颜色", "#2563EB")), unsafe_allow_html=True)
                c3.markdown(f"<span class='rpm-muted'>{r['任务数']} 项　已完成 {r['已完成']}　进行中 {r['进行中']}</span>",
                            unsafe_allow_html=True)
            if is_open:
                sub = _d[_d["_grp"] == name]
                sub = sub.assign(_o=sub["status_kind"].isin(["done", "cancelled"]).astype(int)).sort_values(
                    "_o", kind="stable")
                with st.container(border=True):
                    compact_list(sub, f"ov_ch_{name}", "status", limit=200)
    else:
        empty_state("本项目未设置章节")

# ---- 近期任务 ----
st.markdown("### 近期任务")
days = int(global_setting("upcoming_days", 14))
q = st.columns(3)
with q[0].container(key="blk_up"):
    st.markdown(f"**即将到期（{days} 天内）**")
    compact_list(stx.upcoming_tasks(df, days), "ov_up", "due")
with q[1].container(key="blk_od"):
    st.markdown("**已逾期**")
    compact_list(stx.overdue_tasks(df), "ov_od", "due")
with q[2].container(key="blk_bl"):
    st.markdown("**阻塞任务**")
    compact_list(stx.blocked_tasks(df), "ov_bl", "status")

# ---- 图表 ----
st.markdown("### 分布")
g = st.columns(3)
with g[0].container(key="blk_d1"):
    charts.count_bar(stx.priority_counts(df, lk["priorities"]), "优先级分布", key="ov_pri")
with g[1].container(key="blk_d2"):
    charts.count_bar(sc, "状态分布", key="ov_st")
with g[2].container(key="blk_d3"):
    charts.count_bar(stx.category_counts(df, lk["categories"]), "工作类别分布", horizontal=True, key="ov_cat")
