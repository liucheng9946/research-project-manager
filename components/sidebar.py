"""应用启动与左侧栏：数据库初始化、自动备份、项目切换。"""
from __future__ import annotations

import sqlite3
from datetime import date

import streamlit as st

import config
from core import backup_service as bk
from core.database import (DatabaseError, check_database, get_global_setting,
                           init_database, read_conn)
from core.project_service import (get_current_project_id, get_project,
                                  list_projects, set_current_project)
from components.ui import safe_write


def bootstrap() -> bool:
    """确保数据库可用（不存在则初始化、旧版本则迁移前备份后升级）。返回是否可继续。"""
    if st.session_state.get("_db_ready"):
        return True
    info = check_database()
    if info["exists"] and not info["ok"]:
        st.error(f"数据库无法使用：{info['message']}")
        st.info(f"数据库位置：{info['path']}\n\n可以在“数据管理”页面从备份恢复，"
                "或手动把 backups 目录中的备份文件复制为上述文件。程序不会自动覆盖或删除现有数据库。")
        return False
    try:
        res = init_database(backup_func=bk.pre_migration_backup)
    except DatabaseError as e:
        st.error(f"数据库初始化或升级失败（已回滚）：{e}")
        return False
    except (sqlite3.Error, OSError) as e:  # 如数据库被其他程序占用/只读/磁盘不可写
        st.error(f"无法访问数据库（可能被其他程序占用，或所在位置不可写）：{e}\n\n"
                 f"数据库位置：{config.get_db_path()}。请关闭占用该文件的程序（如 DB Browser、云盘同步）后刷新页面。数据未被修改。")
        return False
    if res.get("applied") and res.get("from"):
        st.session_state.setdefault("_flash", []).append(
            ("info", f"数据库已从版本 {res['from']} 升级到 {res['to']}，升级前备份：{res['backup']}"))
    if config.is_demo():  # 云端演示版：空库时写入示例数据；临时磁盘上不做备份
        try:
            from scripts.demo_seed import seed_demo
            seed_demo()
        except Exception as e:  # noqa: BLE001
            st.session_state.setdefault("_flash", []).append(("warning", f"示例数据写入失败：{e}"))
        st.session_state["_db_ready"] = True
        return True
    try:
        p = bk.auto_backup_if_due()
        if p:
            st.session_state.setdefault("_flash", []).append(("info", "已完成每日自动备份"))
    except Exception as e:  # 自动备份失败不阻止使用，但要提示
        st.session_state.setdefault("_flash", []).append(("warning", f"自动备份失败：{e}"))
    st.session_state["_db_ready"] = True
    return True


def _clear_project_scoped_state() -> None:
    """切换项目时清除与具体项目相关的界面状态（筛选条件、选中任务等）。"""
    for k in list(st.session_state.keys()):
        if k.startswith(("flt_", "sel_", "tree_", "cal_", "detail_", "edit_", "lib_", "log_")):
            del st.session_state[k]


def project_selector() -> str | None:
    with read_conn() as conn:
        projects = list_projects(conn)
        current = get_current_project_id(conn)
    st.sidebar.markdown(f"**{config.APP_TITLE}**")
    if not projects:
        st.sidebar.info("还没有项目。请在“系统设置 → 项目管理”中新建，或运行初始化脚本导入初始数据。")
        st.session_state["pid"] = None
        return None
    ids = [p["id"] for p in projects]
    names = {p["id"]: p["name"] + ("" if p["is_active"] else "（已停用）") for p in projects}
    if current not in ids:
        current = ids[0]
    # 数据库中的“当前项目”是唯一依据：其他页面（如新建项目后切换）修改它时，侧栏随之同步
    if st.session_state.get("project_switch") != current:
        st.session_state["project_switch"] = current

    def _on_switch():
        new = st.session_state["project_switch"]
        safe_write(lambda c: set_current_project(c, new), rerun=False)

    chosen = st.sidebar.selectbox("当前项目", ids, format_func=lambda i: names[i], key="project_switch",
                                  on_change=_on_switch)
    if st.session_state.get("pid") not in (None, chosen):
        _clear_project_scoped_state()   # 切换项目：清除上一项目的筛选条件、选择状态与对话框
        st.session_state.pop("dlg", None)
    st.session_state["pid"] = chosen
    st.sidebar.caption(f"今天：{date.today().isoformat()}")
    return chosen


def require_project() -> dict:
    """页面入口调用：返回当前项目，没有项目时给出提示并停止页面。"""
    pid = st.session_state.get("pid")
    if not pid:
        st.title("科研项目管理系统")
        st.info("当前没有项目。请先到“系统设置 → 项目管理”新建项目。")
        st.stop()
    with read_conn() as conn:
        p = get_project(conn, pid)
    if not p:
        st.session_state["pid"] = None
        st.rerun()
    return p


def recovery_panel() -> None:
    """数据库无法打开时的恢复界面：从备份恢复（原文件先另存，不会被删除）。"""
    st.markdown("### 从备份恢复")
    backups = [b for b in bk.list_backups() if bk.validate_backup(b["path"])[0]]
    if not backups:
        st.caption(f"备份目录（{config.get_backups_dir()}）中没有有效备份。")
        return
    labels = {b["path"]: f"{b['created']}｜{b['kind_label']}｜{b['name']}" for b in backups}
    chosen = st.selectbox("选择备份", list(labels), format_func=labels.get)
    if st.checkbox("我确认用所选备份替换当前无法打开的数据库（原文件会另存到备份目录）"):
        if st.button("恢复", type="primary"):
            try:
                saved = bk.restore_over_broken(chosen)
            except Exception as e:  # noqa: BLE001
                st.error(f"恢复失败：{e}")
                return
            st.session_state.pop("_db_ready", None)
            st.success(f"已恢复。原数据库文件另存为：{saved}")
            st.button("重新加载")


def global_setting(key: str, default=None):
    with read_conn() as conn:
        return get_global_setting(conn, key, default)
