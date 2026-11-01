"""全部任务：搜索、组合筛选、排序、表格/卡片视图、分页、行内快速修改、批量操作、导出筛选结果。"""
from datetime import datetime

import streamlit as st

from components.filters import render_filters
from components.sidebar import global_setting, require_project
from components.task_card import render_cards
from components.task_form import load_lookups, lookup_names, open_new
from components.task_table import paginate, render_table
from core.database import get_project_settings, read_conn
from core.export_service import export_project_excel
from core.task_service import find_duplicate_codes, list_tasks_df

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    df = list_tasks_df(conn, pid)
    lk = load_lookups(conn, pid)
    ps = get_project_settings(conn, pid)
    dups = find_duplicate_codes(conn, pid)

h1, h2 = st.columns([4, 1])
h1.title("全部任务")
h2.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
h2.button("＋ 新增任务", type="primary", on_click=open_new, width="stretch")

filtered = render_filters(df, pid, "list", lookup_names(lk), default_sort=ps.get("default_sort", "priority"))
v1, v2, v3 = st.columns([2, 3, 1.4])
view_default = global_setting("default_view", "table")
view = v1.segmented_control("展示方式", ["table", "card"], format_func={"table": "表格", "card": "卡片"}.get,
                            default=view_default, key=f"flt_{pid}_list_view", label_visibility="collapsed") or "table"
v2.caption(f"显示 {len(filtered)} / {len(df)} 项任务" +
           (f"　·　注意：存在重复编号 {', '.join(d['task_code'] for d in dups)}" if dups else ""))
if not filtered.empty:
    def _export_filtered(data=filtered):
        with read_conn() as c:
            return export_project_excel(c, pid, None, df=data, title_note="当前筛选结果")

    v3.download_button("导出筛选结果", data=_export_filtered,
                       file_name=f"{proj['name']}_筛选结果_{datetime.now():%Y%m%d_%H%M}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")

page_size = int(global_setting("page_size", 50))
if view == "table":
    render_table(filtered, pid, lk, page_size)
else:
    page_df, _ = paginate(filtered, f"sel_card_{pid}", page_size)
    render_cards(page_df, f"sel_card_btn_{pid}")
