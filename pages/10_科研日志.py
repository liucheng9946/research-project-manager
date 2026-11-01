"""科研日志：项目级工作记录。按日期查看、关键词搜索、关联任务并跳转。
不要求每天填写，没有打卡或连续签到。"""
from datetime import date, timedelta

import streamlit as st

from components.sidebar import require_project
from components.task_form import open_detail
from components.ui import badge, empty_state, safe_write
from core import activity_service as act
from core import daily_log_service as dl
from core import task_service as ts
from core.database import read_conn
from utils.date_utils import to_date

proj = require_project()
pid = proj["id"]
with read_conn() as conn:
    opts = ts.task_options(conn, pid)
    dates = dl.dates_with_logs(conn, pid)
labels = {o["id"]: o["label"].strip() for o in opts}

st.title("科研日志")
st.caption("记录当天的工作内容、完成的工作、遇到的问题和下一步计划，并关联到任务。随时记录即可，不需要每天填写。")
k = f"log_{pid}"

top = st.columns([1.4, 3])
day = top[0].date_input("日期", value=st.session_state.get(f"{k}_day_default", date.today()), key=f"{k}_day",
                        format="YYYY-MM-DD")
mode = top[1].segmented_control("查看方式", ["day", "history", "search"], default="day", key=f"{k}_mode",
                                format_func={"day": "当天日志", "history": "全部历史（按日期）", "search": "搜索"}.get)
mode = mode or "day"


def log_form(prefix: str, log: dict | None = None):
    lg = log or {}
    with st.form(f"{prefix}_form", clear_on_submit=log is None):
        c1, c2 = st.columns([1, 3])
        d = c1.date_input("日期", value=to_date(lg.get("log_date")) or day, format="YYYY-MM-DD")
        title = c2.text_input("标题 *", value=lg.get("title", ""), placeholder="例如：Fig.5 初步绘制与数据核查")
        content = st.text_area("当日工作内容", value=lg.get("content") or "", height=80)
        c1, c2, c3 = st.columns(3)
        done = c1.text_area("完成的工作", value=lg.get("done_work") or "", height=90)
        probs = c2.text_area("遇到的问题", value=lg.get("problems") or "", height=90)
        nxt = c3.text_area("下一步计划", value=lg.get("next_steps") or "", height=90)
        rel = st.multiselect("关联任务", list(labels), placeholder="请选择（可多选）", default=[t for t in lg.get("related_task_ids", []) if t in labels],
                             format_func=labels.get)
        if st.form_submit_button("保存日志" if log else "添加日志", type="primary"):
            if log:
                safe_write(lambda c: dl.update(c, log["id"], log_date=d, title=title, content=content,
                                               done_work=done, problems=probs, next_steps=nxt,
                                               related_task_ids=rel), "日志已更新")
            else:
                st.session_state[f"{k}_day_default"] = d
                safe_write(lambda c: dl.create(c, pid, d, title, content, done, probs, nxt, rel), "日志已添加")


def show_log(lg: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**{lg['title']}**　<span class='rpm-muted'>{lg['log_date']}　·　记录于 {lg['created_at'][:16]}"
                    f"{'　·　已编辑' if lg['updated_at'] != lg['created_at'] else ''}</span>", unsafe_allow_html=True)
        if lg["content"]:
            st.markdown(lg["content"])
        cols = st.columns(3)
        for col, f, lbl, color in ((cols[0], "done_work", "完成的工作", "#16A34A"),
                                   (cols[1], "problems", "遇到的问题", "#DC2626"),
                                   (cols[2], "next_steps", "下一步计划", "#2563EB")):
            if lg[f]:
                col.markdown(badge(lbl, color), unsafe_allow_html=True)
                col.markdown(lg[f])
        if lg["related_tasks"]:
            st.markdown("<span class='rpm-muted'>关联任务：</span>", unsafe_allow_html=True)
            tc = st.columns(min(len(lg["related_tasks"]), 4))
            for i, t in enumerate(lg["related_tasks"]):
                tc[i % len(tc)].button((f"{t['task_code']} " if t["task_code"] else "") + t["title"],
                                       key=f"{k}_jump_{lg['id']}_{t['id']}", type="tertiary",
                                       on_click=open_detail, args=(t["id"],))
        a, b, _ = st.columns([1, 1, 6])
        with a.popover("编辑"):
            log_form(f"{k}_edit_{lg['id']}", lg)
        with b.popover("删除"):
            if st.checkbox("确认删除这条日志", key=f"{k}_delok_{lg['id']}") and \
                    st.button("删除", key=f"{k}_del_{lg['id']}"):
                safe_write(lambda c: dl.delete(c, lg["id"]), "日志已删除")


with st.expander("＋ 新增日志", expanded=mode == "day"):
    log_form(f"{k}_new")

if mode == "day":
    with read_conn() as conn:
        logs = dl.list_by_date(conn, pid, day)
        acts = act.project_logs(conn, pid, date_from=day.isoformat(), date_to=day.isoformat())
    st.markdown(f"### {day.isoformat()} 的日志（{len(logs)}）")
    if not logs:
        empty_state("这一天没有日志。")
    for lg in logs:
        show_log(lg)
    with st.expander(f"当天的任务动态（{len(acts)}）：进度、状态变更与任务工作记录", expanded=bool(acts) and not logs):
        if not acts:
            st.caption("当天没有任务变更或工作记录")
        for x in acts:
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"<span class='rpm-muted'>{x['created_at'][11:16]}</span>　"
                        + badge(x["type_label"], "#EA580C" if x["activity_type"] == "milestone" else "#6B7280")
                        + f"　{x['content']}", unsafe_allow_html=True)
            c2.button((f"{x['task_code']} " if x["task_code"] else "") + x["task_title"], key=f"{k}_act_{x['id']}",
                      type="tertiary", on_click=open_detail, args=(x["task_id"],))
    if dates:
        st.caption("有日志的日期：" + "、".join(dates[:15]) + ("…" if len(dates) > 15 else ""))
elif mode == "history":
    c1, c2 = st.columns(2)
    rng = c1.date_input("日期范围", value=(date.today() - timedelta(days=30), date.today()), key=f"{k}_hist_rng")
    task_f = c2.selectbox("关联任务（可选）", [None] + list(labels), format_func=lambda x: labels.get(x, "全部"),
                          key=f"{k}_hist_task")
    a, b = (rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else (None, None))
    with read_conn() as conn:
        logs = dl.search(conn, pid, a, b, task_id=task_f)
    if not logs:
        empty_state("该范围内没有日志")
    current = None
    for lg in logs:
        if lg["log_date"] != current:
            current = lg["log_date"]
            st.markdown(f"#### {current}")
        show_log(lg)
else:
    c1, c2 = st.columns([3, 2])
    kw = c1.text_input("关键词（空格分隔多个词，同时匹配）", key=f"{k}_kw",
                       placeholder="在标题、内容、完成的工作、问题、计划中搜索")
    use_rng = c2.toggle("限定日期范围", key=f"{k}_kw_use")
    rng = c2.date_input("日期范围", value=(date.today() - timedelta(days=90), date.today()), key=f"{k}_kw_rng",
                        disabled=not use_rng)
    a, b = (rng if use_rng and isinstance(rng, (list, tuple)) and len(rng) == 2 else (None, None))
    if kw.strip() or use_rng:
        with read_conn() as conn:
            logs = dl.search(conn, pid, a, b, keyword=kw)
        st.caption(f"找到 {len(logs)} 条日志")
        for lg in logs:
            show_log(lg)
    else:
        st.caption("输入关键词开始搜索")
