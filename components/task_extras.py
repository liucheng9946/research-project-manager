"""任务详情中的 V1.1 分区：6 关联文件与参考资料、7 工作进展记录、8 阶段性成果。"""
from __future__ import annotations

import html

import streamlit as st

from core import activity_service as act
from core import attachment_service as att
from core import daily_log_service as dl
import config
from core.database import read_conn
from components.ui import badge, flash, safe_write
from utils.validators import ValidationError

STATUS_COLORS = {"ok": "#16A34A", "missing": "#DC2626", "url": "#2563EB"}
TYPE_ICONS = {"PDF": "📕", "Word": "📘", "Excel": "📗", "PowerPoint": "📙", "Python": "🐍", "MATLAB": "📐",
              "图片": "🖼️", "数据文件": "🗃️", "网页链接": "🔗"}


LOCAL_ONLY = "云端演示版运行在服务器上，无法访问你电脑上的文件（本地运行时可用）"


def _esc(s) -> str:
    return html.escape(str(s or ""))


# ---------------------------------------------------------------------------
# 6 关联文件与参考资料
# ---------------------------------------------------------------------------
def open_attachment(a: dict, where=st) -> None:
    """打开本地文件（安全方式）；失败时给出中文提示。"""
    try:
        msg = att.open_local(a["file_path"])
        where.success(msg)
    except ValidationError as e:
        where.error(str(e))
    except OSError as e:
        where.error(f"无法调用系统程序打开文件：{e}")


def reveal_attachment(a: dict, where=st) -> None:
    try:
        where.success(att.reveal_in_folder(a["file_path"]))
    except (ValidationError, OSError) as e:
        where.error(f"无法打开文件夹：{e}")


def attachment_actions(a: dict, key: str, show_task: bool = False) -> None:
    """单个关联记录的操作按钮（任务详情与科研资料库共用）。"""
    c = st.columns([1, 1, 1, 1, 1])
    if a["storage_type"] == "url":
        c[0].link_button("打开链接", a["url"], width="stretch")
    else:
        demo = config.is_demo()
        if c[0].button("打开", key=f"{key}_open", width="stretch", disabled=demo or a["status"] != "ok",
                       help=LOCAL_ONLY if demo else "用系统默认程序打开；脚本/可执行文件只在文件夹中定位"):
            open_attachment(a)
        if c[1].button("所在文件夹", key=f"{key}_reveal", width="stretch", disabled=demo,
                       help=LOCAL_ONLY if demo else None):
            reveal_attachment(a)
    with c[2].popover("编辑", width="stretch"):
        with read_conn() as conn:
            types = att.type_names(conn)
        n = st.text_input("显示名称", a["name"], key=f"{key}_n")
        t = st.selectbox("文件类型", types, index=types.index(a["attachment_type"]) if a["attachment_type"] in types else 0,
                         key=f"{key}_t")
        d = st.text_area("文件说明", a["description"] or "", key=f"{key}_d", height=68)
        u = st.text_input("链接地址", a["url"], key=f"{key}_u") if a["storage_type"] == "url" else None
        if st.button("保存", key=f"{key}_save", type="primary"):
            safe_write(lambda conn: att.update(conn, a["id"], n, d, t, u), "关联信息已更新")
    if a["storage_type"] == "local":
        with c[3].popover("重新定位", width="stretch"):
            pk = f"{key}_newpath"
            if st.button("浏览…", key=f"{key}_browse", disabled=config.is_demo(),
                         help=LOCAL_ONLY if config.is_demo() else "在本机弹出文件选择窗口"):
                p = att.pick_file_dialog("重新定位文件")
                if p is None:
                    st.warning("无法打开文件选择窗口，请直接粘贴完整路径")
                elif p:
                    st.session_state[pk] = p
            newp = st.text_input("新的完整路径", key=pk, placeholder=r"D:\Research\Project\Figures\Fig05.png")
            if st.button("确定", key=f"{key}_reloc", type="primary"):
                safe_write(lambda conn: att.relocate(conn, a["id"], newp), "已重新定位")
    with c[4].popover("移除", width="stretch"):
        st.caption("只移除系统中的关联记录，不会删除你的原始文件。")
        missing = a["status"] == "missing"
        if missing:
            st.warning("该路径已失效。建议先尝试“重新定位”；确认不再需要时再手动移除。")
        ok = st.checkbox("确认手动移除该失效记录" if missing else "确认移除关联", key=f"{key}_rmok")
        if st.button("移除关联", key=f"{key}_rm", disabled=not ok):
            safe_write(lambda conn: att.remove(conn, a["id"], confirm_missing=missing), "已移除关联（原文件未改动）")


def attachments_section(t: dict) -> None:
    tid = t["id"]
    with read_conn() as conn:
        items = att.list_for_task(conn, tid)
        types = att.type_names(conn)
    n_bad = sum(1 for a in items if a["status"] == "missing")
    st.markdown(f"##### 关联文件与参考资料（{len(items)}）" + (f"　<span style='color:#DC2626;font-size:13px'>"
                f"{n_bad} 个路径失效</span>" if n_bad else ""), unsafe_allow_html=True)
    for a in items:
        with st.container(border=True):
            loc = a["file_path"] if a["storage_type"] == "local" else a["url"]
            st.markdown(
                f"{TYPE_ICONS.get(a['attachment_type'], '📄')} **{_esc(a['name'])}**　"
                + badge(a["attachment_type"], "#6B7280") + " "
                + badge(att.STATUS_LABELS[a["status"]], STATUS_COLORS[a["status"]])
                + f"<br><span class='rpm-muted' style='word-break:break-all'>{_esc(loc)}</span>"
                + (f"<br><span style='font-size:13px'>{_esc(a['description'])}</span>" if a["description"] else "")
                + f"<br><span class='rpm-muted'>添加于 {a['created_at']}</span>", unsafe_allow_html=True)
            attachment_actions(a, f"dlgw_att_{a['id']}")
    tab_local, tab_url = st.tabs(["＋ 添加本地文件", "＋ 添加网页链接"])
    with tab_local:
        pk = f"dlgw_att_path_{tid}"
        c1, c2 = st.columns([1, 4])
        if c1.button("浏览…", key=f"dlgw_att_browse_{tid}", width="stretch", disabled=config.is_demo(),
                     help=LOCAL_ONLY if config.is_demo() else "在本机弹出文件选择窗口"):
            p = att.pick_file_dialog()
            if p is None:
                st.warning("当前环境无法打开文件选择窗口，请把文件的完整路径粘贴到右侧输入框")
            elif p:
                st.session_state[pk] = p
        path = c2.text_input("文件完整路径", key=pk, placeholder=r"D:\Research\Project\Figures\Fig05.png",
                             label_visibility="collapsed")
        c1, c2, c3 = st.columns([2, 1.3, 2])
        name = c1.text_input("显示名称（可留空，默认文件名）", key=f"dlgw_att_name_{tid}")
        auto = "（按扩展名自动识别）"
        typ = c2.selectbox("文件类型", [auto] + types, key=f"dlgw_att_type_{tid}")
        desc = c3.text_input("文件说明", key=f"dlgw_att_desc_{tid}")
        allow = st.checkbox("文件当前不存在也添加（如移动硬盘未连接）", key=f"dlgw_att_allow_{tid}")
        if st.button("添加文件", key=f"dlgw_att_add_{tid}", type="primary"):
            def _do(conn):
                att.add_local(conn, tid, path, name or None, None if typ == auto else typ, desc, allow)
                for k in (pk, f"dlgw_att_name_{tid}", f"dlgw_att_desc_{tid}"):
                    st.session_state.pop(k, None)
            safe_write(_do, "已关联本地文件")
        st.caption("只记录文件路径，不会复制、移动或修改你的文件。")
    with tab_url:
        with st.form(f"dlgw_att_url_{tid}", clear_on_submit=True, border=False):
            u = st.text_input("链接地址", placeholder="https://github.com/…　或　DOI：10.1155/2020/8858945")
            c1, c2 = st.columns(2)
            un = c1.text_input("显示名称（可选）")
            ud = c2.text_input("说明（可选）")
            if st.form_submit_button("添加链接", type="primary"):
                safe_write(lambda conn: att.add_url(conn, tid, u, un or None, None, ud), "已关联网页链接")


# ---------------------------------------------------------------------------
# 7 工作进展记录
# ---------------------------------------------------------------------------
def _log_item(lg: dict, key: str) -> None:
    color = {"milestone": "#EA580C", "note": "#2563EB"}.get(lg["activity_type"], "#9CA3AF")
    edited = (lg.get("updated_at") and lg["updated_at"] != lg["created_at"] and lg["editable"])
    st.markdown(f"<div class='rpm-row'><span class='rpm-muted'>{lg['created_at'][:16]}</span>　"
                + badge(lg["type_label"], color)
                + (" <span class='rpm-muted'>（已编辑）</span>" if edited else "")
                + f"<div style='white-space:pre-wrap;margin-top:2px'>{_esc(lg['content'])}</div></div>",
                unsafe_allow_html=True)
    if lg["editable"]:
        c = st.columns([1, 1.6, 1, 4])
        with c[0].popover("编辑"):
            txt = st.text_area("记录内容", lg["content"], key=f"{key}_txt", height=100)
            if st.button("保存", key=f"{key}_save", type="primary"):
                safe_write(lambda conn: act.update_log(conn, lg["id"], content=txt), "记录已更新")
        if lg["activity_type"] == "note":
            if c[1].button("标记为阶段性成果", key=f"{key}_ms", type="tertiary"):
                safe_write(lambda conn: act.update_log(conn, lg["id"], activity_type="milestone"), "已标记为阶段性成果")
        else:
            if c[1].button("取消阶段性成果", key=f"{key}_unms", type="tertiary"):
                safe_write(lambda conn: act.update_log(conn, lg["id"], activity_type="note"), "已改为普通工作记录")
        with c[2].popover("删除"):
            if st.button("确认删除这条记录", key=f"{key}_del"):
                safe_write(lambda conn: act.delete_log(conn, lg["id"]), "记录已删除")


def progress_log_section(t: dict) -> None:
    tid = t["id"]
    st.markdown("##### 工作进展记录")
    with st.form(f"dlgw_log_add_{tid}", clear_on_submit=True, border=True):
        txt = st.text_area("新增记录（时间自动记录）", height=80,
                           placeholder="例如：完成 Fig.5 的初步绘制，采用统一统计口径重新计算各结构组样本数量，目前需要进一步核查缺失数据。")
        ms = st.checkbox("这是一项阶段性成果")
        if st.form_submit_button("添加记录", type="primary"):
            safe_write(lambda conn: act.add_log(conn, tid, txt, "milestone" if ms else "note"),
                       "已添加阶段性成果" if ms else "已添加工作记录")
    f1, f2 = st.columns([3, 1])
    scope = f1.segmented_control("显示", ["all", "manual", "auto"], default="all", key=f"dlgw_log_scope_{tid}",
                                 format_func={"all": "全部历史", "manual": "手动记录", "auto": "自动变更记录"}.get,
                                 label_visibility="collapsed") or "all"
    newest = f2.toggle("最新在前", value=True, key=f"dlgw_log_order_{tid}")
    types = None
    if scope == "manual":
        types = ("note", "milestone")
    elif scope == "auto":
        types = ("progress_update", "status_change", "priority_change", "due_date_change", "description_update")
    with read_conn() as conn:
        logs = act.list_logs(conn, tid, types=types, newest_first=newest)
        related = dl.logs_for_task(conn, tid)
    if not logs:
        st.caption("暂无记录")
    limit_key = f"dlgw_log_limit_{tid}"
    limit = st.session_state.get(limit_key, 15)
    for lg in logs[:limit]:
        _log_item(lg, f"dlgw_log_{lg['id']}")
    if len(logs) > limit:
        if st.button(f"查看完整历史（另有 {len(logs) - limit} 条）", key=f"{limit_key}_more", type="tertiary"):
            st.session_state[limit_key] = len(logs)
            st.rerun(scope="fragment")
    if related:
        st.caption("相关科研日志：" + "；".join(f"{r['log_date']} {r['title']}" for r in related[:5]))


# ---------------------------------------------------------------------------
# 8 阶段性成果
# ---------------------------------------------------------------------------
def milestone_section(t: dict) -> None:
    with read_conn() as conn:
        ms = act.list_logs(conn, t["id"], types=("milestone",), newest_first=False)
    st.markdown(f"##### 阶段性成果（{len(ms)}）")
    if not ms:
        st.caption("尚无阶段性成果。可在上方添加记录时勾选“这是一项阶段性成果”，或把已有工作记录标记为阶段性成果。")
        return
    for m in ms:
        st.markdown(f"<div style='border-left:3px solid #EA580C;padding:2px 10px;margin:4px 0'>"
                    f"<span class='rpm-muted'>{m['created_at'][:10]}</span>　{_esc(m['content'])}</div>",
                    unsafe_allow_html=True)


def register() -> None:
    """注册到任务详情（顺序：6 关联文件、7 工作进展记录、8 阶段性成果）。"""
    from components import task_form
    if not task_form.DETAIL_EXTRA_SECTIONS:
        task_form.DETAIL_EXTRA_SECTIONS.extend([attachments_section, progress_log_section, milestone_section])
