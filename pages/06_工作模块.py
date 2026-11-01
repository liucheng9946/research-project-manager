"""工作模块：动态管理（数据库中的工作类别），展示任务数、完成度、负责人，点击查看模块任务。"""
import streamlit as st

from components.lookup_manager import lookup_manager
from components.sidebar import require_project
from components.task_card import render_cards
from components.task_form import open_new
from components.ui import empty_state, progress_html
from core import category_service as cs
from core import statistics_service as stx
from core.database import get_project_settings, read_conn
from core.task_service import list_tasks_df

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    df = list_tasks_df(conn, pid)
    cats = cs.list_items(conn, "categories", pid)
    ps = get_project_settings(conn, pid)
ex_p = bool(ps.get("stats_exclude_paused"))

st.title("工作模块")
st.caption("工作模块即任务的“工作类别”，与父子任务层级相互独立。模块数量不固定，可在下方增加、修改、删除和排序。")
gp = stx.group_progress(df, "category", cats, ex_p).set_index("名称")
sel_key = f"sel_module_{pid}"

if not cats:
    empty_state("本项目还没有工作模块")
cols = st.columns(2)
for i, c in enumerate(cats):
    r = gp.loc[c["name"]] if c["name"] in gp.index else None
    sub = df[df["category_id"] == c["id"]] if not df.empty else df
    people = sorted({p for ps_ in sub["people"] for p in ps_}) if not sub.empty else []
    with cols[i % 2].container(border=True):
        a, b = st.columns([3, 1])
        a.markdown(f"<span style='color:{c['color']}'>■</span> **{c['name']}**　"
                   f"<span class='rpm-muted'>排序 {c['sort_order'] + 1}</span>", unsafe_allow_html=True)
        if b.button("查看任务", key=f"{sel_key}_{c['id']}", width="stretch"):
            st.session_state[sel_key] = c["id"]
        st.caption(c["description"] or "（无描述）")
        st.markdown(progress_html(r["平均完成度"] if r is not None else 0, c["color"]), unsafe_allow_html=True)
        n = int(r["任务数"]) if r is not None else 0
        st.markdown(f"<span class='rpm-muted'>任务 {n} 项　已完成 {int(r['已完成']) if r is not None else 0}"
                    f"　进行中 {int(r['进行中']) if r is not None else 0}　·　负责人：{'、'.join(people) or '—'}</span>",
                    unsafe_allow_html=True)

uncat = df[df["category_id"].isna()] if not df.empty else df
if len(uncat):
    st.caption(f"另有 {len(uncat)} 项任务未设置工作模块。")

chosen = st.session_state.get(sel_key)
if chosen and any(c["id"] == chosen for c in cats):
    c = next(c for c in cats if c["id"] == chosen)
    st.divider()
    h1, h2, h3 = st.columns([3, 1, 1])
    h1.markdown(f"### {c['name']} 的任务")
    h2.button("＋ 在此模块新增任务", on_click=open_new, args=(None, {"category_id": c["id"]}))
    if h3.button("收起"):
        st.session_state.pop(sel_key, None)
        st.rerun()
    sub = df[df["category_id"] == chosen].sort_values("tree_index")
    render_cards(sub, f"sel_module_card_{pid}")

st.divider()
st.markdown("### 管理工作模块")
lookup_manager("categories", pid, "删除模块前若仍有任务，需要把这些任务重新分配到其他模块或置空。")
