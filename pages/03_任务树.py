"""任务树：展开/折叠、多层级、父任务进度、子任务状态、快速新增子任务、上移/下移、调整父子关系。"""
import streamlit as st

from components.sidebar import require_project
from components.task_form import open_detail, open_new
from components.ui import badge, empty_state, progress_html, safe_write
from core import category_service, statistics_service as stx
from core import task_service as ts
from core.database import read_conn
from utils.formatters import fmt_text

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    df = ts.list_tasks_df(conn, pid)

h1, h2 = st.columns([4, 1])
h1.title("任务树")
h2.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
h2.button("＋ 新增顶层任务", type="primary", on_click=open_new, width="stretch")

if df.empty:
    empty_state("还没有任务")
    st.stop()

ck = f"tree_collapsed_{pid}"
collapsed: set = st.session_state.setdefault(ck, set())
c1, c2, c3, c4 = st.columns([1, 1, 2.2, 3])
if c1.button("全部展开"):
    collapsed.clear()
    st.rerun()
if c2.button("全部折叠"):
    collapsed.update(df.loc[df["has_children"], "id"])
    st.rerun()
group = c3.toggle("按工作模块分组显示顶层任务", value=False, key=f"tree_group_{pid}")
c4.caption(f"共 {len(df)} 项，其中父任务 {int(df['has_children'].sum())} 项。▸/▾ 展开或折叠；↑↓ 调整同级顺序。")

df = df.sort_values("tree_index")

# ---- 按章节查看：只保留该章的任务及其上级任务（保持树结构完整） ----
_grp = df["chapter_group"].where(df["chapter_group"].notna(), df["chapter"])
with read_conn() as _conn:
    _chapters = category_service.list_items(_conn, "chapters", pid)
_present = set(_grp.dropna())
_chs = [n for n in stx.chapter_progress(df, _chapters, True)["名称"] if n in _present]  # 与总览页同一章节顺序
NO_CH = "（未设置章节）"
if _chs:
    opts_ch = ["全部章节", *_chs, *([NO_CH] if _grp.isna().any() else [])]
    sel_ch = st.selectbox("按章节查看", opts_ch, key=f"tree_chapter_{pid}",
                          help="选择某一章后，只显示该章（含其各小节）的任务及它们的上级任务")
    if sel_ch != "全部章节":
        _all = df.set_index("id")
        keep = set(df.loc[_grp.isna() if sel_ch == NO_CH else (_grp == sel_ch), "id"])
        n_match = len(keep)
        for tid in list(keep):
            p = _all.loc[tid, "parent_id"]
            while p and p in _all.index and p not in keep:
                keep.add(p)
                p = _all.loc[p, "parent_id"]
        df = df[df["id"].isin(keep)]
        st.caption(f"“{sel_ch}”：{n_match} 项任务"
                   + (f"（另显示 {len(df) - n_match} 项上级任务以保持层级）" if len(df) > n_match else ""))
by_id = df.set_index("id")


def visible(row) -> bool:
    p = row.parent_id
    while p and p in by_id.index:
        if p in collapsed:
            return False
        p = by_id.loc[p, "parent_id"]
    return True


def toggle(tid):
    if tid in collapsed:
        collapsed.discard(tid)
    else:
        collapsed.add(tid)


def move(tid, d):
    safe_write(lambda c: ts.move_order(c, tid, d), rerun=False)


rows = [r for r in df.itertuples(index=False) if visible(r)]
current_cat = object()
hdr = st.columns([5, 1.1, 2.2, 1.2, 1.6])
for col, name in zip(hdr, ["任务", "状态", "完成度", "负责人", "操作"]):
    col.markdown(f"<span class='rpm-muted'>{name}</span>", unsafe_allow_html=True)
for r in rows:
    if group and r.depth == 0 and r.category != current_cat:
        current_cat = r.category
        st.markdown(f"<div style='margin-top:8px;font-weight:600;color:{r.category_color or '#374151'}'>"
                    f"■ {fmt_text(r.category, '未分类')}</div>", unsafe_allow_html=True)
    cols = st.columns([5, 1.1, 2.2, 1.2, 1.6], vertical_alignment="center")
    with cols[0]:
        inner = st.columns([0.06 * r.depth + 0.001, 0.07, 1]) if r.depth else st.columns([0.001, 0.07, 1])
        if r.has_children:
            inner[1].button("▸" if r.id in collapsed else "▾", key=f"tree_t_{r.id}", type="tertiary",
                            on_click=toggle, args=(r.id,))
        label = (f"{r.task_code} " if r.task_code else "") + r.title
        if r.has_children:
            label += f"（{r.n_children}）" + (" ⟳" if r.progress_mode == "auto" else "")
        inner[2].button(label, key=f"tree_o_{r.id}", type="tertiary", on_click=open_detail, args=(r.id,),
                        help="⟳ 表示进度按子任务自动计算" if r.progress_mode == "auto" else None)
    cols[1].markdown(badge(r.status, r.status_color), unsafe_allow_html=True)
    cols[2].markdown(progress_html(r.progress, r.status_color or "#2563EB"), unsafe_allow_html=True)
    cols[3].markdown(f"<span class='rpm-muted'>{fmt_text(r.assignee)}</span>", unsafe_allow_html=True)
    with cols[4]:
        a, b, c = st.columns(3)
        a.button("↑", key=f"tree_up_{r.id}", on_click=move, args=(r.id, -1), help="上移", type="tertiary")
        b.button("↓", key=f"tree_dn_{r.id}", on_click=move, args=(r.id, 1), help="下移", type="tertiary")
        c.button("＋", key=f"tree_add_{r.id}", on_click=open_new,
                 args=(r.id, {"category_id": r.category_id, "chapter_id": r.chapter_id}), help="新增子任务", type="tertiary")

# ---- 调整父子关系 ----
st.divider()
st.markdown("### 调整父子关系")
with read_conn() as conn:
    opts = ts.task_options(conn, pid)
labels = {o["id"]: o["label"] for o in opts}
m1, m2, m3 = st.columns([3, 3, 1])
src = m1.selectbox("要移动的任务", list(labels), format_func=labels.get, key=f"tree_mv_src_{pid}")
with read_conn() as conn:
    banned = {src, *ts.descendants(conn, src)} if src else set()
targets = {"__root__": "（移到顶层）", **{k: v for k, v in labels.items() if k not in banned}}
dst = m2.selectbox("新的父任务", list(targets), format_func=targets.get, key=f"tree_mv_dst_{pid}",
                   help="已自动排除任务自身及其子孙任务，防止循环层级")
m3.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
if m3.button("移动", type="primary"):
    safe_write(lambda c: ts.update_task(c, src, {"parent_id": None if dst == "__root__" else dst}),
               "层级已调整")
