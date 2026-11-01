"""日历视图：月 / 周 / 列表视图；按优先级或状态着色；查看当日任务；修改截止日期；无截止日期任务单列。"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from components import calendar as cal
from components.sidebar import global_setting, require_project
from components.task_card import compact_list
from components.ui import badge, empty_state, safe_write
from core import statistics_service as stx
from core import task_service as ts
from core.database import read_conn
from utils.date_utils import to_date

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    df = ts.list_tasks_df(conn, pid)
week_start = int(global_setting("calendar_week_start", 0))

st.title("日历视图")
ck = f"cal_ref_{pid}"
ref: date = st.session_state.setdefault(ck, date.today())
t1, t2, t3, t4, t5, t6 = st.columns([1.6, 0.6, 0.6, 0.6, 1.6, 2])
mode = t1.segmented_control("视图", ["month", "week", "list"], default="month", key=f"cal_mode_{pid}",
                            format_func={"month": "月视图", "week": "周视图", "list": "列表视图"}.get,
                            label_visibility="collapsed") or "month"


def shift(n):
    r = st.session_state[ck]
    if mode == "week":
        st.session_state[ck] = r + timedelta(days=7 * n)
    else:
        m = r.month - 1 + n
        st.session_state[ck] = date(r.year + m // 12, m % 12 + 1, 1)


t2.button("‹", on_click=shift, args=(-1,), help="上一页")
t3.button("今天", on_click=lambda: st.session_state.__setitem__(ck, date.today()))
t4.button("›", on_click=shift, args=(1,), help="下一页")
color_by = t5.segmented_control("着色", ["priority", "status"], default="priority", key=f"cal_color_{pid}",
                                format_func={"priority": "按优先级", "status": "按状态"}.get,
                                label_visibility="collapsed") or "priority"
ref = st.session_state[ck]
t6.markdown(f"#### {ref.year} 年 {ref.month} 月" if mode != "week"
            else f"#### {ref.isocalendar()[0]} 年第 {ref.isocalendar()[1]} 周")

dated = df[df["due_date"].notna()] if not df.empty else df
legend_items = (df[["priority", "priority_color"]] if color_by == "priority" else df[["status", "status_color"]]) \
    if not df.empty else pd.DataFrame()
if not legend_items.empty:
    pairs = legend_items.dropna().drop_duplicates().values.tolist()
    st.markdown("图例：" + " ".join(badge(n, c) for n, c in pairs) + " " + badge("逾期", "#DC2626"),
                unsafe_allow_html=True)

if mode == "month":
    cal.month_view(df if not df.empty else df, ref.year, ref.month, week_start, color_by, pid)
    sel = st.date_input("查看某一天的任务", value=st.session_state.get(f"cal_day_{pid}", date.today()),
                        key=f"cal_pick_{pid}")
    cal.day_list(df, sel, color_by, pid)
elif mode == "week":
    cal.week_view(df, ref, week_start, color_by, pid)
else:
    if dated.empty:
        empty_state("没有设置截止日期的任务")
    else:
        lst = dated.sort_values("due_date").reset_index(drop=True)
        view = pd.DataFrame({"编号": lst["task_code"].fillna(""), "任务名称": lst["title"],
                             "优先级": lst["priority"], "状态": lst["status"],
                             "完成度": lst["progress"], "截止日期": [to_date(d) for d in lst["due_date"]],
                             "逾期": lst["overdue"].map(lambda x: "逾期" if x else "")})
        view.index = lst["id"].values
        ed = st.data_editor(view, hide_index=True, width="stretch", key=f"cal_list_{pid}",
                            disabled=["编号", "任务名称", "优先级", "状态", "完成度", "逾期"],
                            column_config={"截止日期": st.column_config.DateColumn(format="YYYY-MM-DD"),
                                           "完成度": st.column_config.NumberColumn(format="%.0f%%")})
        changed = {i: ed.loc[i, "截止日期"] for i in view.index if ed.loc[i, "截止日期"] != view.loc[i, "截止日期"]}
        if st.button(f"保存截止日期修改（{len(changed)}）", disabled=not changed, type="primary"):
            def _do(c):
                for i, d in changed.items():
                    ts.update_task(c, i, {"due_date": d})
            safe_write(_do, "截止日期已更新")

# ---- 修改截止日期 / 逾期 / 无截止日期 ----
st.divider()
a, b = st.columns(2)
with a:
    st.markdown("### 修改截止日期")
    with read_conn() as conn:
        opts = ts.task_options(conn, pid)
    if opts:
        labels = {o["id"]: o["label"].strip() for o in opts}
        tid = st.selectbox("任务", list(labels), format_func=labels.get, key=f"cal_edit_task_{pid}")
        cur = to_date(df.set_index("id").loc[tid, "due_date"]) if tid else None
        nd = st.date_input("新的截止日期（可清空）", value=cur, key=f"cal_edit_date_{pid}_{tid}")
        if st.button("保存日期"):
            safe_write(lambda c: ts.update_task(c, tid, {"due_date": nd}), "截止日期已更新")
    st.markdown(f"### 已逾期任务（{int(df['overdue'].sum()) if not df.empty else 0}）")
    compact_list(stx.overdue_tasks(df), "cal_od", "due", limit=20)
with b:
    nodate = df[df["due_date"].isna()] if not df.empty else df
    st.markdown(f"### 无截止日期的任务（{len(nodate)}）")
    st.caption("这些任务没有被安排到日历中的任何一天。")
    compact_list(nodate.sort_values(["priority_order", "task_code"]) if not nodate.empty else nodate,
                 "cal_nd", "status", limit=100)
