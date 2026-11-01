"""系统设置：项目管理、项目级配置（优先级/状态/章节/类别/标签/负责人/排序/统计口径）、全局设置。"""
import streamlit as st

import config
from components.filters import SORT_OPTIONS
from components.lookup_manager import lookup_manager
from components.sidebar import require_project
from components.ui import safe_write
from core import project_service as prj
from core.database import (get_global_setting, get_project_settings, read_conn,
                           set_global_setting, set_project_setting)
from core.progress_service import METRIC_LABELS

proj = require_project() if st.session_state.get("pid") else None
pid = proj["id"] if proj else None
st.title("系统设置")
if proj:
    st.caption(f"项目级设置只作用于当前项目“{proj['name']}”；全局设置作用于整个软件。")

tabs = st.tabs(["项目管理", "优先级", "状态", "章节", "工作类别", "标签", "负责人", "排序与统计口径", "全局设置"])

with tabs[0]:
    with read_conn() as conn:
        projects = prj.list_projects(conn)
        counts = {p["id"]: prj.project_task_count(conn, p["id"]) for p in projects}
    st.markdown("#### 项目列表")
    for p in projects:
        st.markdown(f"- **{p['name']}**{'（当前）' if p['id'] == pid else ''}{'（已停用）' if not p['is_active'] else ''}"
                    f"　<span class='rpm-muted'>{counts[p['id']]} 项任务 · 创建于 {p['created_at']}</span>",
                    unsafe_allow_html=True)
    st.markdown("#### 新建项目")
    with st.form("edit_new_project", clear_on_submit=False):
        name = st.text_input("项目名称 *")
        desc = st.text_area("项目描述", height=68)
        mode = st.radio("初始配置", list(prj.CREATE_MODES), format_func=prj.CREATE_MODES.get, index=1)
        src_names = {p["id"]: p["name"] for p in projects}
        src = st.selectbox("复制配置的源项目（仅“基于现有项目”时使用）", list(src_names) or [None],
                           format_func=lambda x: src_names.get(x, "—"))
        switch = st.checkbox("创建后切换到新项目", value=True)
        if st.form_submit_button("创建项目", type="primary"):
            def _do(conn):
                new = prj.create_project(conn, name, desc, mode, src if mode == "copy" else None)
                if switch:
                    prj.set_current_project(conn, new)
                return new
            safe_write(_do, f"项目“{name}”已创建")
    if proj:
        st.markdown("#### 当前项目信息")
        with st.form("edit_cur_project"):
            n2 = st.text_input("项目名称", value=proj["name"])
            d2 = st.text_area("项目描述", value=proj["description"] or "", height=68)
            act = st.checkbox("启用", value=bool(proj["is_active"]))
            if st.form_submit_button("保存"):
                safe_write(lambda c: prj.update_project(c, pid, n2, d2, act), "项目信息已更新")
        with st.expander("删除当前项目（危险操作）"):
            st.warning(f"将永久删除项目“{proj['name']}”及其 {counts.get(pid, 0)} 项任务和全部配置。"
                       "建议先在“数据管理”中备份。")
            conf = st.text_input("请输入完整项目名称以确认", key="edit_del_proj_confirm")
            if st.button("删除项目"):
                if conf != proj["name"]:
                    st.error("输入的名称与项目名称不一致，未删除")
                else:
                    safe_write(lambda c: prj.delete_project(c, pid, conf), "项目已删除")

if pid:
    with tabs[1]:
        lookup_manager("priorities", pid, "排序靠前表示优先级更高（用于“按优先级排序”）。")
    with tabs[2]:
        lookup_manager("statuses", pid, "状态与完成度相互独立。“统计类别”决定状态在统计中的含义"
                       "（例如“已完成类”计入任务完成率，“已取消类”不计入任何统计）。")
    with tabs[3]:
        lookup_manager("chapters", pid, "章节数量不固定，也可以不使用章节。“归属章”用于把小节汇总到章。")
    with tabs[4]:
        lookup_manager("categories", pid, "工作类别即工作模块。")
    with tabs[5]:
        lookup_manager("tags", pid)
    with tabs[6]:
        lookup_manager("people", pid, "负责人会根据任务中的“负责人”文字自动建立（如 张三+李四 拆为两人）。"
                       "改名会同步更新相关任务的负责人文字。")
    with tabs[7]:
        with read_conn() as conn:
            ps = get_project_settings(conn, pid)
        with st.form("edit_proj_settings"):
            ds = st.selectbox("默认排序", list(SORT_OPTIONS), format_func=SORT_OPTIONS.get,
                              index=list(SORT_OPTIONS).index(ps.get("default_sort", "priority")))
            om = st.radio("首页“整体完成率”采用的口径", list(METRIC_LABELS), format_func=lambda k: f"{k}　{METRIC_LABELS[k]}",
                          index=list(METRIC_LABELS).index(ps.get("overall_metric", "B")))
            ex = st.checkbox("统计时排除“暂缓类”任务", value=bool(ps.get("stats_exclude_paused")))
            cb = st.radio("任务完成率中“已完成”的判定", ["status", "progress"],
                          format_func={"status": "状态属于“已完成类”", "progress": "完成度达到 100%"}.get,
                          index=0 if ps.get("completion_basis", "status") == "status" else 1)
            st.caption("三个指标都只统计有效叶子任务（无子任务、非已取消类），父任务不重复计入。")
            if st.form_submit_button("保存", type="primary"):
                def _do(c):
                    set_project_setting(c, pid, "default_sort", ds)
                    set_project_setting(c, pid, "overall_metric", om)
                    set_project_setting(c, pid, "stats_exclude_paused", ex)
                    set_project_setting(c, pid, "completion_basis", cb)
                    for k in list(st.session_state.keys()):
                        if k.endswith("_list_sort"):
                            del st.session_state[k]
                safe_write(_do, "项目设置已保存")

with tabs[8]:
    with read_conn() as conn:
        g = {k: get_global_setting(conn, k) for k in ("default_view", "page_size", "date_format",
                                                       "calendar_week_start", "upcoming_days")}
    with st.form("edit_global"):
        dv = st.selectbox("任务列表默认视图", ["table", "card"], format_func={"table": "表格", "card": "卡片"}.get,
                          index=0 if g["default_view"] == "table" else 1)
        psz = st.number_input("每页显示任务数", 10, 500, int(g["page_size"] or 50), step=10)
        wk = st.selectbox("日历每周起始", [0, 6], format_func={0: "周一", 6: "周日"}.get,
                          index=0 if int(g["calendar_week_start"] or 0) == 0 else 1)
        up = st.number_input("“即将到期”的天数范围", 1, 120, int(g["upcoming_days"] or 14))
        if st.form_submit_button("保存", type="primary"):
            def _do(c):
                set_global_setting(c, "default_view", dv)
                set_global_setting(c, "page_size", int(psz))
                set_global_setting(c, "calendar_week_start", int(wk))
                set_global_setting(c, "upcoming_days", int(up))
            safe_write(_do, "全局设置已保存")
    st.markdown("#### 关联文件类型")
    st.caption("用于“关联文件与参考资料”。按扩展名自动识别类型；可以增加新的类型（如 OpenSees 脚本 .tcl）。")
    from core import attachment_service as att
    with read_conn() as conn:
        atypes = att.list_types(conn)
    for t in atypes:
        a, b, c = st.columns([1.2, 4, 0.8])
        a.markdown(f"**{t['name']}**")
        ext = b.text_input("扩展名", t["extensions"] or "", key=f"edit_atype_{t['id']}", label_visibility="collapsed",
                           placeholder="多个扩展名用逗号分隔，如 .tcl,.out")
        if ext != (t["extensions"] or "") and c.button("保存", key=f"edit_atype_save_{t['id']}"):
            safe_write(lambda conn, tid=t["id"], e=ext: att.update_type(conn, tid, e), "文件类型已更新")
    with st.form("edit_atype_new", clear_on_submit=True):
        a, b = st.columns([1, 2])
        nn = a.text_input("新文件类型名称")
        ne = b.text_input("扩展名（逗号分隔）")
        if st.form_submit_button("添加文件类型"):
            safe_write(lambda conn: att.add_type(conn, nn, ne), f"已添加文件类型“{nn}”")
    st.markdown("#### 路径（只读）")
    st.code(f"数据库：{config.get_db_path()}\n备份目录：{config.get_backups_dir()}\n导出目录：{config.EXPORTS_DIR}",
            language=None)
    st.caption("界面语言：简体中文。日期统一以 YYYY-MM-DD 显示和存储。")
