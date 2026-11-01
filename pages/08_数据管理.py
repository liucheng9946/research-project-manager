"""数据管理：Excel 导入（映射/验证/预览/确认）、Excel 导出、SQLite 备份恢复、JSON 备份恢复、数据库信息。"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from components.sidebar import require_project
from components.ui import empty_state, flash, safe_write
from core import backup_service as bk
from core import category_service as cs
from core import export_service as ex
from core import import_service as imp
from core.database import DatabaseError, read_conn, transaction
from core.project_service import project_task_count
from utils.formatters import fmt_size

proj = require_project()
pid = proj["id"]
st.title("数据管理")
tabs = st.tabs(["Excel 导入", "Excel 导出", "数据库备份", "JSON 备份", "数据库信息"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
stamp = lambda: datetime.now().strftime("%Y%m%d_%H%M")  # noqa: E731

# ---------------------------------------------------------------- 导入
with tabs[0]:
    st.caption(f"导入到当前项目“{proj['name']}”。流程：上传 → 选择工作表 → 字段映射 → 验证与预览 → 确认导入。"
               "导入在一个事务中完成，失败不会留下部分数据；导入前自动备份数据库。")
    up = st.file_uploader("上传 Excel 文件（.xlsx）", type=["xlsx"], key=f"lib_imp_file_{pid}")
    if up is not None:
        raw = up.getvalue()
        try:
            sheets = imp.list_sheets(raw)
        except Exception as e:  # noqa: BLE001
            st.error(f"无法读取该文件：{e}")
            st.stop()
        c1, c2 = st.columns([2, 1])
        default_sheet = "任务清单" if "任务清单" in sheets else ("全部任务" if "全部任务" in sheets else sheets[0])
        sheet = c1.selectbox("工作表", sheets, index=sheets.index(default_sheet))
        hdr = c2.number_input("表头行号（0 = 自动识别）", 0, 50, 0)
        try:
            sd = imp.read_sheet(raw, sheet, int(hdr) or None)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        st.caption(f"表头位于第 {sd.header_row} 行；表头下方共 {len(sd.rows)} 行非空数据。"
                   + (f"工作表标题：{sd.title}" if sd.title else ""))
        auto = imp.auto_mapping(sd.columns)
        st.markdown("**字段映射**（左：系统字段；右：Excel 中的列）")
        mapping = {}
        cols = st.columns(3)
        for i, (f, (lbl, _)) in enumerate(imp.FIELD_SPECS.items()):
            opts = ["（不导入）"] + sd.columns
            cur = auto.get(f)
            v = cols[i % 3].selectbox(lbl + (" *" if f in imp.REQUIRED_FIELDS else ""), opts,
                                      index=opts.index(cur) if cur in opts else 0, key=f"lib_map_{pid}_{sheet}_{f}")
            mapping[f] = None if v == "（不导入）" else v
        with read_conn() as conn:
            plan = imp.build_plan(sd, mapping, conn, pid)
            cats = cs.list_items(conn, "categories", pid)
        cat_names = {c["id"]: c["name"] for c in cats}
        d1, d2 = st.columns(2)
        default_cat = d1.selectbox("未指定工作类别的任务归入", [None] + list(cat_names),
                                   format_func=lambda x: cat_names.get(x, "（不设置）"))
        policy = d2.radio("重复编号处理方式", list(imp.DUPLICATE_POLICIES), format_func=imp.DUPLICATE_POLICIES.get)
        n_dup = sum(1 for r in plan.records if r.get("_duplicate"))
        m = st.columns(4)
        m[0].metric("有效任务", len(plan.records))
        m[1].metric("与已有编号重复", n_dup)
        m[2].metric("错误", len(plan.errors))
        m[3].metric("警告", len(plan.warnings))
        for i in plan.errors:
            st.error(i.message)
        if plan.warnings:
            with st.expander(f"警告（{len(plan.warnings)}）", expanded=len(plan.warnings) <= 5):
                for i in plan.warnings:
                    st.warning(i.message)
        if plan.records:
            prev = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_") or k == "_row"}
                                 for r in plan.records]).rename(columns={"_row": "Excel行号"})
            prev = prev.rename(columns={k: v[0] for k, v in imp.FIELD_SPECS.items()})
            prev.insert(1, "重复", ["是" if r.get("_duplicate") else "" for r in plan.records])
            st.markdown("**导入预览**")
            st.dataframe(prev, hide_index=True, width="stretch", height=320)
        if plan.records and not plan.errors:
            if n_dup:
                act = {"skip": f"跳过 {n_dup} 项重复任务", "update": f"更新 {n_dup} 项已有任务",
                       "new": f"另外新建 {n_dup} 项（编号重复）"}[policy]
                st.info(f"将导入 {len(plan.records)} 项，其中重复编号：{act}。")
            ok = st.checkbox("我已检查预览，确认导入", key=f"lib_imp_ok_{pid}")
            if st.button("确认导入", type="primary", disabled=not ok):
                try:
                    bk.backup_database("pre_import")
                    with transaction() as conn:
                        res = imp.commit_plan(conn, pid, plan, default_cat, policy, up.name)
                except Exception as e:  # noqa: BLE001
                    st.error(f"导入失败，已回滚，数据库未改变：{e}")
                else:
                    flash(f"导入完成：新建 {res['created']}，更新 {res['updated']}，跳过 {res['skipped']}"
                          + (f"；未找到的父任务：{'、'.join(res['unresolved_parents'])}" if res["unresolved_parents"] else ""))
                    st.session_state.pop(f"lib_imp_file_{pid}", None)
                    st.rerun()

# ---------------------------------------------------------------- 导出
with tabs[1]:
    st.caption("导出的 Excel 已设置列宽、冻结窗格、筛选、日期与百分比格式、条件格式，可直接查看、编辑并重新导入。"
               "“导出当前筛选结果”位于“全部任务”页面。")
    with read_conn() as conn:
        n = project_task_count(conn, pid)

    def _mk(fn, *args):
        """返回点击下载时才生成文件的函数（dest=None 时导出函数返回字节）。"""
        def run():
            with read_conn() as conn:
                return fn(conn, *args, None)
        return run

    e = st.columns(2)
    e[0].download_button(f"导出当前项目全部任务（{n} 项）", _mk(ex.export_project_excel, pid),
                         f"{proj['name']}_全部任务_{stamp()}.xlsx", XLSX, width="stretch")
    e[1].download_button("导出全部项目", _mk(ex.export_all_projects_excel),
                         f"全部项目_{stamp()}.xlsx", XLSX, width="stretch")
    e[0].download_button("导出任务层级", _mk(ex.export_hierarchy_excel, pid),
                         f"{proj['name']}_任务层级_{stamp()}.xlsx", XLSX, width="stretch")
    e[1].download_button("导出统计汇总", _mk(ex.export_statistics_excel, pid),
                         f"{proj['name']}_统计汇总_{stamp()}.xlsx", XLSX, width="stretch")
    st.markdown("**保存到本机导出目录**")
    if st.button("把当前项目完整导出保存到 data/exports", disabled=config.is_demo(),
                 help="云端演示版请使用上面的下载按钮" if config.is_demo() else None):
        p = config.EXPORTS_DIR / f"{proj['name']}_全部任务_{stamp()}.xlsx"
        with read_conn() as conn:
            ex.export_project_excel(conn, pid, p)
        st.success(f"已保存：{p}")

# ---------------------------------------------------------------- SQLite 备份
with tabs[2]:
    st.caption("使用 SQLite 官方在线备份接口；应用每天首次启动时自动备份一次（保留最近 "
               f"{config.AUTO_BACKUP_KEEP} 份自动备份）。恢复前会自动备份当前数据库，恢复失败时保留原数据。")
    if st.button("立即备份", type="primary"):
        try:
            p = bk.backup_database("manual")
            st.success(f"备份完成：{p}")
        except DatabaseError as e2:
            st.error(str(e2))
    backups = bk.list_backups()
    if not backups:
        empty_state("还没有备份")
    else:
        st.dataframe(pd.DataFrame([{"文件名": b["name"], "类型": b["kind_label"], "时间": b["created"],
                                    "大小": fmt_size(b["size"])} for b in backups]),
                     hide_index=True, width="stretch", height=min(36 * len(backups) + 40, 360))
        labels = {b["path"]: f"{b['created']}｜{b['kind_label']}｜{b['name']}" for b in backups}
        chosen = st.selectbox("选择备份", list(labels), format_func=labels.get, key="lib_bk_sel")
        ok, msg, info = bk.validate_backup(chosen)
        st.caption(f"校验：{msg}" + (f"（{info['projects']} 个项目，{info['tasks']} 项任务，版本 {info['version']}）" if ok else ""))
        b1, b2, b3 = st.columns(3)
        b1.download_button("下载该备份", Path(chosen).read_bytes(), Path(chosen).name, width="stretch")
        with b2.popover("恢复该备份", width="stretch", disabled=not ok):
            st.warning("当前数据库将被替换为该备份（会先自动备份当前数据库）。")
            if st.checkbox("确认恢复", key="lib_bk_restore_ok") and st.button("执行恢复", type="primary"):
                try:
                    res = bk.restore_backup(chosen)
                    st.session_state.pop("_db_ready", None)
                    flash(f"已恢复：{res['projects']} 个项目，{res['tasks']} 项任务。恢复前备份：{Path(res['pre_restore_backup']).name}")
                    st.session_state.pop("pid", None)
                    st.rerun()
                except DatabaseError as e2:
                    st.error(str(e2))
        with b3.popover("删除备份", width="stretch"):
            olds = st.multiselect("选择要删除的备份", list(labels), placeholder="请选择（可多选）", format_func=labels.get, key="lib_bk_del")
            if st.checkbox("确认删除所选备份", key="lib_bk_del_ok") and st.button("删除", disabled=not olds):
                for p in olds:
                    bk.delete_backup(p)
                flash(f"已删除 {len(olds)} 份备份")
                st.rerun()
    st.markdown("**从外部文件恢复**")
    ext = st.file_uploader("上传 .db 备份文件", type=["db", "sqlite", "sqlite3"], key="lib_bk_upload")
    if ext is not None:
        tmp = config.get_backups_dir() / f"uploaded_{stamp()}_{Path(ext.name).name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(ext.getvalue())
        ok, msg, info = bk.validate_backup(tmp)
        if not ok:
            tmp.unlink(missing_ok=True)
            st.error(f"文件无效，拒绝恢复：{msg}")
        else:
            st.info(f"文件有效：{info['projects']} 个项目，{info['tasks']} 项任务。已保存到备份目录：{tmp.name}，"
                    "可在上方列表中选择并恢复。")

# ---------------------------------------------------------------- JSON
with tabs[3]:
    st.caption("JSON 全量备份包含全部项目、任务、分类、章节、优先级、状态、标签、负责人、依赖关系、"
               "进展记录及配置，用于完整保存 Excel 无法表达的层级与关联。")
    st.download_button("导出 JSON 全量备份", lambda: bk.export_json_bytes(), f"research_manager_{stamp()}.json",
                       "application/json", type="primary")
    jf = st.file_uploader("上传 JSON 备份以恢复", type=["json"], key="lib_json_up")
    if jf is not None:
        try:
            data = json.loads(jf.getvalue().decode("utf-8"))
            ok, msg = bk.validate_json(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as e2:
            ok, msg, data = False, f"不是有效的 JSON 文件：{e2}", None
        if not ok:
            st.error(f"验证失败，拒绝恢复：{msg}")
        else:
            t = data["tables"]
            st.success(f"结构验证通过：{len(t['projects'])} 个项目，{len(t['tasks'])} 项任务，"
                       f"导出时间 {data.get('exported_at')}")
            if st.checkbox("确认用该 JSON 替换当前全部数据（会先自动备份当前数据库）", key="lib_json_ok") \
                    and st.button("执行恢复", type="primary", key="lib_json_go"):
                try:
                    res = bk.restore_json(data)
                    flash(f"JSON 恢复完成：{res['projects']} 个项目，{res['tasks']} 项任务")
                    st.session_state.pop("pid", None)
                    st.rerun()
                except DatabaseError as e2:
                    st.error(str(e2))

# ---------------------------------------------------------------- 信息
with tabs[4]:
    dbp = config.get_db_path()
    with read_conn() as conn:
        ver = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        nproj = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        ncur = project_task_count(conn, pid)
        logs = [dict(r) for r in conn.execute("SELECT kind, source, task_count, created_at FROM import_log "
                                              "WHERE project_id=? ORDER BY created_at DESC", (pid,))]
    info = {"数据库路径": str(dbp), "数据库版本": ver, "数据库大小": fmt_size(dbp.stat().st_size),
            "当前项目": proj["name"], "当前项目任务数": ncur,
            "全部项目数": nproj, "全部任务数": total, "最近备份时间": bk.latest_backup_time() or "无",
            "备份目录": str(config.get_backups_dir())}
    for k, v in info.items():
        st.markdown(f"<div class='rpm-label'>{k}</div><div class='rpm-value'>{v}</div>", unsafe_allow_html=True)
    if logs:
        st.markdown("**导入记录**")
        st.dataframe(pd.DataFrame(logs).rename(columns={"kind": "类型", "source": "来源", "task_count": "任务数",
                                                        "created_at": "时间"}), hide_index=True)
