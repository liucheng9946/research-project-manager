"""任务表单与对话框：新建 / 编辑 / 详情 / 删除确认。

对话框由 session_state["dlg"] 驱动，任何页面通过 open_detail()/open_new()/open_delete()
打开，app.py 在页面渲染后调用 dialog_router() 统一显示。关闭（含点击 ×）时清除状态。
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from core import category_service as cs
from core import dependency_service as deps
from core import task_service as ts
from core.database import read_conn, transaction
from components.ui import badge, empty_state, field, flash, progress_html, safe_write
from utils.date_utils import to_date
from utils.formatters import fmt_pct, fmt_text
from utils.validators import ValidationError

NONE = "__none__"
MODE_LABELS = {"manual": "手动设置", "auto": "按子任务自动计算"}


# ---------------------------------------------------------------------------
# 打开 / 关闭
# ---------------------------------------------------------------------------
def _clear_dialog_state() -> None:
    st.session_state.pop("dlg", None)
    for k in list(st.session_state.keys()):
        if k.startswith("dlgw_"):
            del st.session_state[k]


def open_detail(task_id: str) -> None:
    _clear_dialog_state()
    st.session_state["dlg"] = ("detail", task_id)


def open_new(parent_id: str | None = None, defaults: dict | None = None) -> None:
    _clear_dialog_state()
    st.session_state["dlg"] = ("new", {"parent_id": parent_id, **(defaults or {})})


def open_delete(task_ids: list[str]) -> None:
    _clear_dialog_state()
    st.session_state["dlg"] = ("delete", list(task_ids))


def close_dialog() -> None:
    _clear_dialog_state()


def load_lookups(conn, pid: str) -> dict:
    return {t: cs.list_items(conn, t, pid) for t in
            ("categories", "chapters", "priorities", "statuses", "tags", "people")}


def lookup_names(lk: dict) -> dict:
    return {"priority": [x["name"] for x in lk["priorities"]],
            "chapter": [x["name"] for x in lk["chapters"]],
            "category": [x["name"] for x in lk["categories"]],
            "status": [x["name"] for x in lk["statuses"]],
            "tags": [x["name"] for x in lk["tags"]]}


def _select(label: str, items: list[dict], current: str | None, key: str, allow_none=True, help=None):
    ids = ([NONE] if allow_none else []) + [i["id"] for i in items]
    names = {i["id"]: i["name"] for i in items}
    names[NONE] = "（未设置）"
    idx = ids.index(current) if current in ids else 0
    v = st.selectbox(label, ids, index=idx, format_func=lambda x: names.get(x, x), key=key, help=help)
    return None if v == NONE else v


# ---------------------------------------------------------------------------
# 表单
# ---------------------------------------------------------------------------
def task_form(pid: str, task: dict | None = None, defaults: dict | None = None) -> None:
    """新建（task=None）或编辑任务。提交后写入数据库。"""
    defaults = defaults or {}
    t = task or {}
    # 键中包含更新时间：任务在别处被修改后，表单自动使用数据库中的最新值
    kp = f"dlgw_form_{t.get('id', 'new')}_{t.get('updated_at', '')}_"
    with read_conn() as conn:
        lk = load_lookups(conn, pid)
        exclude = {t["id"], *ts.descendants(conn, t["id"])} if t else set()
        parent_opts = ts.task_options(conn, pid, exclude=exclude)
        dep_opts = ts.task_options(conn, pid, exclude={t["id"]} if t else set())
        has_children = bool(t) and t.get("n_children", 0) > 0
    with st.form(f"{kp}form", border=False):
        c1, c2 = st.columns([3, 1])
        title = c1.text_input("任务名称 *", value=t.get("title", ""), key=f"{kp}title")
        code = c2.text_input("任务编号（可选）", value=t.get("task_code") or "", key=f"{kp}code",
                             help="可留空；系统内部使用唯一 ID，不强制编号")
        c1, c2, c3, c4 = st.columns(4)
        cat = _select_in(c1, "工作类别", lk["categories"], t.get("category_id", defaults.get("category_id")), f"{kp}cat")
        chap = _select_in(c2, "论文章节", lk["chapters"], t.get("chapter_id", defaults.get("chapter_id")), f"{kp}chap")
        pri = _select_in(c3, "优先级", lk["priorities"], t.get("priority_id", defaults.get("priority_id")), f"{kp}pri")
        default_status = next((s["id"] for s in lk["statuses"] if s["is_default"]),
                              lk["statuses"][0]["id"] if lk["statuses"] else None)
        stat = _select_in(c4, "状态", lk["statuses"], t.get("status_id", default_status), f"{kp}stat")
        c1, c2, c3, c4 = st.columns(4)
        assignee = c1.text_input("负责人", value=t.get("assignee") or defaults.get("assignee") or "",
                                 key=f"{kp}assignee",
                                 help="多人用 + 或 、 分隔，例如 张三+李四。已有："
                                      + ("、".join(p["name"] for p in lk["people"]) or "无"))
        due = c2.date_input("截止日期", value=to_date(t.get("due_date")), key=f"{kp}due", format="YYYY-MM-DD")
        mode = c3.selectbox("进度计算模式", ["manual", "auto"], format_func=MODE_LABELS.get,
                            index=1 if t.get("progress_mode") == "auto" else 0, key=f"{kp}mode")
        weight = c4.number_input("工作量权重", min_value=0.1, value=float(t.get("weight") or 1.0),
                                 step=0.5, key=f"{kp}weight")
        auto_locked = has_children and t.get("progress_mode") == "auto"
        progress = st.slider("完成度（%）", 0, 100, int(round(float(t.get("progress") or 0))),
                             key=f"{kp}progress", disabled=auto_locked,
                             help="自动模式且有子任务时，完成度由子任务加权计算，不能手动修改")
        desc = st.text_area("任务描述", value=t.get("description") or "", key=f"{kp}desc", height=90)
        c1, c2 = st.columns(2)
        deliv = c1.text_area("交付物", value=t.get("deliverable") or "", key=f"{kp}deliv", height=68)
        accept = c2.text_area("验收标准", value=t.get("acceptance_criteria") or "", key=f"{kp}accept", height=68)
        prereq = st.text_input("前置依赖 / 数据来源（文字说明）", value=t.get("prerequisites") or "", key=f"{kp}prereq")
        c1, c2 = st.columns(2)
        dep_labels = {o["id"]: o["label"].strip() for o in dep_opts}
        dep_sel = c1.multiselect("前置依赖任务", list(dep_labels), placeholder="请选择（可多选）", default=[d for d in t.get("dependency_ids", [])
                                                                       if d in dep_labels],
                                 format_func=dep_labels.get, key=f"{kp}deps",
                                 help="本任务需要等这些任务完成；系统会阻止循环依赖")
        tag_names = {g["id"]: g["name"] for g in lk["tags"]}
        tag_sel = c2.multiselect("标签", list(tag_names), placeholder="请选择（可多选）", default=t.get("tag_ids", []),
                                 format_func=tag_names.get, key=f"{kp}tags")
        parent_labels = {NONE: "（无，作为顶层任务）", **{o["id"]: o["label"] for o in parent_opts}}
        cur_parent = t.get("parent_id") if t else defaults.get("parent_id")
        parent = st.selectbox("父任务", list(parent_labels),
                              index=list(parent_labels).index(cur_parent) if cur_parent in parent_labels else 0,
                              format_func=parent_labels.get, key=f"{kp}parent",
                              help="不能选择任务自身或其子孙任务")
        notes = st.text_area("备注", value=t.get("notes") or "", key=f"{kp}notes", height=68)
        submitted = st.form_submit_button("保存" if t else "创建任务", type="primary")

    data = {"title": title, "task_code": code, "category_id": cat, "chapter_id": chap, "priority_id": pri,
            "status_id": stat, "assignee": assignee, "due_date": due, "progress_mode": mode,
            "weight": weight, "description": desc, "deliverable": deliv, "acceptance_criteria": accept,
            "prerequisites": prereq, "parent_id": None if parent == NONE else parent, "notes": notes}
    if not auto_locked:
        data["progress"] = progress
    pend_key = f"{kp}pending_dup"
    if submitted:
        _save(pid, t, data, tag_sel, dep_sel, on_duplicate="error", pend_key=pend_key)
    if st.session_state.get(pend_key):
        msg = st.session_state[pend_key]
        st.warning(f"{msg}。是否仍然保存（允许编号重复）？")
        b1, b2 = st.columns([1, 4])
        if b1.button("仍然保存", key=f"{kp}dup_yes", type="primary"):
            _save(pid, t, data, tag_sel, dep_sel, on_duplicate="allow", pend_key=pend_key)
        if b2.button("返回修改编号", key=f"{kp}dup_no"):
            st.session_state.pop(pend_key, None)
            st.rerun(scope="fragment")


def _select_in(col, label, items, current, key):
    with col:
        return _select(label, items, current, key)


def _save(pid, t, data, tag_sel, dep_sel, on_duplicate, pend_key):
    try:
        with transaction() as conn:
            if t:
                ts.update_task(conn, t["id"], data, tag_ids=tag_sel, dependency_ids=dep_sel,
                               on_duplicate=on_duplicate)
                tid = t["id"]
            else:
                tid = ts.create_task(conn, pid, data, tag_ids=tag_sel, dependency_ids=dep_sel,
                                     on_duplicate=on_duplicate)
    except ts.DuplicateCodeError as e:
        st.session_state[pend_key] = str(e)
        return
    except ValidationError as e:
        st.error(f"未保存：{e}")
        return
    except Exception as e:  # noqa: BLE001
        st.error(f"数据库操作失败（已回滚）：{e}")
        return
    st.session_state.pop(pend_key, None)
    flash("任务已保存" if t else "任务已创建")
    open_detail(tid)
    st.rerun()


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------
# 详情页额外分区（V1.1 注册：关联文件、工作进展记录、阶段性成果）
DETAIL_EXTRA_SECTIONS: list = []


def _task_link(t: dict, key: str) -> None:
    """对话框内的任务链接。对话框内按钮默认只刷新对话框本身，因此需显式整页刷新以切换对话框。"""
    label = (f"{t['task_code']} " if t.get("task_code") else "") + t["title"]
    if st.button(label, key=key, type="tertiary"):
        open_detail(t["id"])
        st.rerun()


def render_detail(task_id: str) -> None:
    with read_conn() as conn:
        t = ts.get_task(conn, task_id)
        if not t:
            st.warning("任务不存在（可能已被删除）")
            if st.button("关闭"):
                close_dialog()
                st.rerun()
            return
        kids = ts.children(conn, task_id)
        pre = deps.predecessors(conn, task_id)
        post = deps.successors(conn, task_id)
        statuses = cs.list_items(conn, "statuses", t["project_id"])
        comments = ts.list_comments(conn, task_id)
    pid = t["project_id"]
    head = (f"`{t['task_code']}` " if t["task_code"] else "") + f"**{t['title']}**"
    st.markdown(head + "　" + badge(t["priority"], t["priority_color"]) + " " +
                badge(t["status"], t["status_color"]), unsafe_allow_html=True)
    tabs = st.tabs(["详情", "编辑", "删除"])
    with tabs[0]:
        # 1 基本信息
        st.markdown("##### 基本信息")
        c = st.columns(4)
        with c[0]:
            field("工作类别", t["category"])
            field("负责人", t["assignee"])
        with c[1]:
            field("论文章节", t["chapter"])
            due = t["due_date"]
            overdue = due and to_date(due) < date.today() and t["status_kind"] not in ("done", "cancelled")
            field("截止日期", (f"<span style='color:#DC2626'>{due}（已逾期）</span>" if overdue else due))
        with c[2]:
            field("标签", "、".join(t["tags"]))
            field("创建时间", t["created_at"])
        with c[3]:
            field("更新时间", t["updated_at"])
            field("完成时间", t["completed_at"])
        if t["parent_id"]:
            st.markdown("<div class='rpm-label'>父任务</div>", unsafe_allow_html=True)
            _task_link({"id": t["parent_id"], "task_code": t["parent_code"], "title": t["parent_title"]},
                       "dlgw_parent_link")
        # 2 任务进度
        st.markdown("##### 任务进度")
        st.markdown(progress_html(t["progress"], t["status_color"] or "#2563EB"), unsafe_allow_html=True)
        st.caption(f"进度模式：{MODE_LABELS[t['progress_mode']]}　·　工作量权重：{t['weight']:g}")
        auto_locked = t["progress_mode"] == "auto" and kids
        with st.form("dlgw_quick", border=True):
            q1, q2, q3 = st.columns([1.4, 2.4, 0.8])
            sids = [s["id"] for s in statuses]
            snames = {s["id"]: s["name"] for s in statuses}
            new_status = q1.selectbox("状态", sids, index=sids.index(t["status_id"]) if t["status_id"] in sids else 0,
                                      format_func=snames.get)
            new_prog = q2.slider("完成度（%）", 0, 100, int(round(t["progress"])), disabled=bool(auto_locked),
                                 help="自动模式下由子任务计算" if auto_locked else None)
            q3.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if q3.form_submit_button("更新", type="primary"):
                ch = {"status_id": new_status}
                if not auto_locked:
                    ch["progress"] = new_prog
                safe_write(lambda conn: ts.update_task(conn, task_id, ch), "状态与完成度已更新")
        # 3 子任务
        st.markdown(f"##### 子任务（{len(kids)}）")
        for k in kids:
            r = st.columns([3.2, 1, 2])
            with r[0]:
                _task_link(k, f"dlgw_kid_{k['id']}")
            r[1].markdown(badge(k["status"], k["status_color"]), unsafe_allow_html=True)
            r[2].markdown(progress_html(k["progress"]), unsafe_allow_html=True)
        if st.button("＋ 新增子任务", key="dlgw_addkid"):
            open_new(task_id, {"category_id": t["category_id"], "chapter_id": t["chapter_id"]})
            st.rerun()
        # 4 前置依赖
        st.markdown("##### 依赖关系")
        d1, d2 = st.columns(2)
        with d1:
            st.caption("前置依赖（本任务依赖）")
            if not pre:
                st.caption("无")
            for p in pre:
                _task_link(p, f"dlgw_pre_{p['id']}")
        with d2:
            st.caption("后续依赖（依赖本任务）")
            if not post:
                st.caption("无")
            for p in post:
                _task_link(p, f"dlgw_post_{p['id']}")
        if t["prerequisites"]:
            field("前置依赖 / 数据来源（文字说明）", t["prerequisites"])
        # 5 交付物与验收标准
        st.markdown("##### 任务内容、交付物与验收标准")
        field("任务描述", t["description"])
        e1, e2 = st.columns(2)
        with e1:
            field("交付物", t["deliverable"])
        with e2:
            field("验收标准", t["acceptance_criteria"])
        field("备注", t["notes"])
        for section in DETAIL_EXTRA_SECTIONS:
            section(t)
        if not DETAIL_EXTRA_SECTIONS:
            _comments_section(task_id, comments)
    with tabs[1]:
        task_form(pid, task=t)
    with tabs[2]:
        _delete_body([task_id], in_detail=True)


def _comments_section(task_id: str, comments: list[dict]) -> None:
    st.markdown(f"##### 历史进展记录（{len(comments)}）")
    with st.form("dlgw_comment", clear_on_submit=True, border=False):
        txt = st.text_area("新增进展记录", placeholder="记录本阶段完成的工作、发现的问题…", height=70)
        if st.form_submit_button("添加记录"):
            safe_write(lambda conn: ts.add_comment(conn, task_id, txt), "已添加进展记录")
    for c in comments:
        st.markdown(f"<div class='rpm-row'><span class='rpm-muted'>{c['created_at']}</span><br>"
                    f"{c['content']}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------
def _delete_body(task_ids: list[str], in_detail: bool = False) -> None:
    with read_conn() as conn:
        impact = ts.delete_impact(conn, task_ids)
        names = [r[0] for r in conn.execute(
            f"SELECT COALESCE(task_code || ' ', '') || title FROM tasks WHERE id IN ({','.join('?' * len(task_ids))})",
            task_ids)]
    if not names:
        st.info("任务已不存在")
        return
    st.markdown(f"将删除 **{len(names)}** 项任务：")
    st.caption("、".join(names[:20]) + ("…" if len(names) > 20 else ""))
    mode = None
    if impact["with_children"]:
        st.warning(f"其中 {len(impact['with_children'])} 项任务有子任务（共 {impact['descendants']} 项后代任务）。"
                   "请选择子任务的处理方式：")
        choice = st.radio("子任务处理方式", ["promote", "cascade"], key="dlgw_del_mode",
                          format_func={"promote": "将子任务提升一级（保留子任务）",
                                       "cascade": f"同时删除全部 {impact['descendants']} 项后代任务"}.get)
        mode = choice
    if impact["dependencies"]:
        st.caption(f"相关的 {impact['dependencies']} 条依赖关系、进展记录与标签关联将一并删除。")
    ok = st.checkbox("我已确认删除，且此操作不可撤销（可从备份恢复）", key="dlgw_del_ok")
    b1, b2 = st.columns([1, 4])
    if b1.button("确认删除", type="primary", disabled=not ok, key="dlgw_del_go"):
        def _do(conn):
            n = ts.delete_tasks(conn, task_ids, child_mode=mode)
            close_dialog()
            return n
        safe_write(_do, f"已删除任务")
    if not in_detail and b2.button("取消", key="dlgw_del_cancel"):
        close_dialog()
        st.rerun()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@st.dialog("任务详情", width="large", on_dismiss=close_dialog)
def _detail_dialog(task_id: str):
    render_detail(task_id)


@st.dialog("新建任务", width="large", on_dismiss=close_dialog)
def _new_dialog(pid: str, defaults: dict):
    if defaults.get("parent_id"):
        with read_conn() as conn:
            p = ts.get_task(conn, defaults["parent_id"])
        if p:
            st.caption(f"父任务：{fmt_text(p['task_code'], '')} {p['title']}")
    task_form(pid, None, defaults)


@st.dialog("删除任务", on_dismiss=close_dialog)
def _delete_dialog(task_ids: list[str]):
    _delete_body(task_ids)


def dialog_router() -> None:
    dlg = st.session_state.get("dlg")
    pid = st.session_state.get("pid")
    if not dlg or not pid:
        return
    kind, arg = dlg
    if kind == "detail":
        _detail_dialog(arg)
    elif kind == "new":
        _new_dialog(pid, arg)
    elif kind == "delete":
        _delete_dialog(arg)
