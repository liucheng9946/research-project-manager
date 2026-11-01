"""项目级配置项的通用管理界面（新增、修改、排序、删除前重新分配）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import category_service as cs
from core.database import read_conn
from core.schema import STATUS_KINDS
from components.ui import empty_state, safe_write

HAS_COLOR = {"categories", "priorities", "statuses", "tags"}
HAS_DESC = {"categories", "chapters", "priorities", "people"}


def lookup_manager(table: str, pid: str, help_text: str | None = None) -> None:
    meta = cs.LOOKUPS[table]
    label = meta["label"]
    with read_conn() as conn:
        items = cs.list_items(conn, table, pid)
        usage = {i["id"]: cs.usage_count(conn, table, i["id"]) for i in items}
    if help_text:
        st.caption(help_text)
    k = f"edit_{table}_{pid}"
    if items:
        data = {"名称": [i["name"] for i in items]}
        if table == "chapters":
            data["归属章（用于按章汇总）"] = [i.get("group_name") or "" for i in items]
        if table in HAS_DESC:
            data["说明"] = [i.get("description") or "" for i in items]
        if table == "statuses":
            data["统计类别"] = [i["kind"] for i in items]
            data["默认状态"] = [bool(i["is_default"]) for i in items]
        if table in HAS_COLOR:
            data["颜色"] = [i["color"] for i in items]
        data["使用次数"] = [usage[i["id"]] for i in items]
        view = pd.DataFrame(data, index=[i["id"] for i in items])
        cfg = {"使用次数": st.column_config.NumberColumn(disabled=True, width=70)}
        if table == "statuses":
            cfg["统计类别"] = st.column_config.SelectboxColumn(options=list(STATUS_KINDS), width=110,
                                                           help="决定统计含义：" + "；".join(f"{a}={b}" for a, b in STATUS_KINDS.items()))
        if table in HAS_COLOR:
            cfg["颜色"] = st.column_config.TextColumn(help="#RRGGBB 格式，例如 #2563EB", width=90)
        ver = st.session_state.get(f"{k}_ver", 0)
        ed = st.data_editor(view, key=f"{k}_tbl_{ver}", hide_index=True, width="stretch",
                            column_config=cfg)
        diffs = {}
        for iid in view.index:
            a, b = view.loc[iid], ed.loc[iid]
            ch = {}
            if b["名称"] != a["名称"]:
                ch["name"] = b["名称"]
            if "说明" in view and b["说明"] != a["说明"]:
                ch["description"] = b["说明"]
            if "归属章（用于按章汇总）" in view and b["归属章（用于按章汇总）"] != a["归属章（用于按章汇总）"]:
                ch["group_name"] = b["归属章（用于按章汇总）"]
            if "颜色" in view and b["颜色"] != a["颜色"]:
                ch["color"] = b["颜色"]
            if "统计类别" in view and b["统计类别"] != a["统计类别"]:
                ch["kind"] = b["统计类别"]
            if "默认状态" in view and bool(b["默认状态"]) and not bool(a["默认状态"]):
                ch["is_default"] = True
            if ch:
                diffs[iid] = ch
        c1, c2, c3 = st.columns([1.4, 1.4, 3])
        if c1.button(f"保存修改（{len(diffs)}）", key=f"{k}_save", disabled=not diffs, type="primary"):
            def _do(conn):
                for iid, ch in diffs.items():
                    cs.update_item(conn, table, iid, **ch)
                st.session_state[f"{k}_ver"] = ver + 1
            safe_write(_do, f"{label}已更新")
        with c2.popover("调整顺序", width="stretch"):
            names = {i["id"]: i["name"] for i in items}
            mv = st.selectbox(label, list(names), format_func=names.get, key=f"{k}_mv")
            a, b = st.columns(2)
            if a.button("上移", key=f"{k}_up"):
                safe_write(lambda c: cs.move_item(c, table, mv, -1))
            if b.button("下移", key=f"{k}_down"):
                safe_write(lambda c: cs.move_item(c, table, mv, 1))
        with c3.popover(f"删除{label}"):
            names = {i["id"]: f"{i['name']}（{usage[i['id']]} 项任务使用）" for i in items}
            did = st.selectbox(f"要删除的{label}", list(names), format_func=names.get, key=f"{k}_del")
            n = usage.get(did, 0)
            target = cs.UNSET
            if n:
                others = {i["id"]: i["name"] for i in items if i["id"] != did}
                link = "task_col" not in meta
                opts = ["__null__"] + list(others)
                none_lbl = "解除关联" if link else "置为空（未设置）"
                choice = st.selectbox(f"这 {n} 项任务改为", opts, key=f"{k}_re",
                                      format_func=lambda x: none_lbl if x == "__null__" else others[x])
                target = None if choice == "__null__" else choice
                st.warning(f"删除后，{n} 项任务将{'解除该关联' if choice == '__null__' and link else '重新分配'}，任务本身不会被删除。")
            ok = st.checkbox("确认删除", key=f"{k}_delok")
            if st.button("删除", key=f"{k}_delgo", disabled=not ok):
                safe_write(lambda c: cs.delete_item(c, table, did, reassign_to=target), f"{label}已删除")
    else:
        empty_state(f"尚未设置{label}")
    with st.form(f"{k}_add", clear_on_submit=True, border=True):
        st.markdown(f"**新增{label}**")
        cols = st.columns([2, 2, 1.2])
        name = cols[0].text_input("名称")
        extra = {}
        if table in HAS_DESC:
            extra["description"] = cols[1].text_input("说明")
        if table == "chapters":
            extra["group_name"] = cols[1].text_input("归属章（可选，如 第6章）")
        if table == "statuses":
            extra["kind"] = cols[1].selectbox("统计类别", list(STATUS_KINDS), format_func=STATUS_KINDS.get)
        if table in HAS_COLOR:
            extra["color"] = cols[2].color_picker("颜色", "#2563EB")
        if st.form_submit_button("添加"):
            safe_write(lambda c: cs.create_item(c, table, pid, name=name, **extra), f"已添加{label}“{name}”")
