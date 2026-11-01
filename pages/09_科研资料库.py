"""科研资料库：当前项目所有已关联的文件与链接（索引，不存储文件本身）。"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from components.sidebar import require_project
from components.task_extras import attachment_actions
from components.task_form import open_detail
from components.ui import empty_state
from core import attachment_service as att
from core.database import read_conn

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    items = att.list_for_project(conn, pid)
    types = att.type_names(conn)

st.title("科研资料库")
st.caption(f"项目“{proj['name']}”中所有任务关联的文件与参考资料。系统只记录路径与链接，不复制、不移动、不删除你的文件。"
           "在任务详情的“关联文件与参考资料”中添加。")

n_local = sum(1 for a in items if a["storage_type"] == "local")
n_bad = sum(1 for a in items if a["status"] == "missing")
m = st.columns(4)
m[0].metric("关联总数", len(items))
m[1].metric("本地文件", n_local)
m[2].metric("网页链接", len(items) - n_local)
m[3].metric("失效路径", n_bad)

if not items:
    empty_state("当前项目还没有关联任何文件或链接。打开任一任务详情，在“关联文件与参考资料”中添加。")
    st.stop()

k = f"lib_{pid}"
f = st.columns([2.2, 1.6, 2.2, 1.4, 1.4])
q = f[0].text_input("按名称搜索", key=f"{k}_q", placeholder="文件名称、说明、路径…")
sel_types = f[1].multiselect("文件类型", types, placeholder="请选择（可多选）", key=f"{k}_types")
task_opts = {}
for a in items:
    task_opts.setdefault(a["task_id"], (f"{a['task_code']} " if a["task_code"] else "") + a["task_title"])
sel_tasks = f[2].multiselect("所属任务", list(task_opts), placeholder="请选择（可多选）", format_func=task_opts.get, key=f"{k}_tasks")
status = f[3].selectbox("状态", ["全部", "可用", "失效路径", "网页链接"], key=f"{k}_status")
recent = f[4].selectbox("添加时间", ["全部", "最近 7 天", "最近 30 天"], key=f"{k}_recent")

rows = items
if q.strip():
    words = q.lower().split()
    rows = [a for a in rows if all(w in f"{a['name']} {a['description'] or ''} {a['location']}".lower() for w in words)]
if sel_types:
    rows = [a for a in rows if a["attachment_type"] in sel_types]
if sel_tasks:
    rows = [a for a in rows if a["task_id"] in sel_tasks]
smap = {"可用": "ok", "失效路径": "missing", "网页链接": "url"}
if status != "全部":
    rows = [a for a in rows if a["status"] == smap[status]]
if recent != "全部":
    since = (date.today() - timedelta(days=7 if "7" in recent else 30)).isoformat()
    rows = [a for a in rows if a["created_at"][:10] >= since]

st.caption(f"显示 {len(rows)} / {len(items)} 项（按添加时间从新到旧）。勾选一行查看操作。")
if not rows:
    empty_state("没有符合条件的资料")
    st.stop()

df = pd.DataFrame([{
    "名称": a["name"], "类型": a["attachment_type"], "状态": att.STATUS_LABELS[a["status"]],
    "所属任务": task_opts[a["task_id"]], "说明": a["description"] or "",
    "路径或链接": a["location"], "添加时间": a["created_at"][:16],
} for a in rows])
ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                  key=f"{k}_table", height=min(36 * len(df) + 40, 560),
                  column_config={"路径或链接": st.column_config.TextColumn(width="large"),
                                 "名称": st.column_config.TextColumn(width="medium")})
picked = ev.selection.rows if ev and ev.selection else []
if picked:
    a = rows[picked[0]]
    with st.container(border=True):
        st.markdown(f"**{a['name']}**　·　{a['attachment_type']}　·　{att.STATUS_LABELS[a['status']]}")
        st.caption(a["location"])
        attachment_actions(a, f"{k}_act_{a['id']}")
        if st.button(f"打开所属任务：{task_opts[a['task_id']]}", key=f"{k}_task_{a['id']}", type="tertiary"):
            open_detail(a["task_id"])
            st.rerun()
