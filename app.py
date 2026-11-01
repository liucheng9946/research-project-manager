"""科研项目管理系统 —— Streamlit 入口。

启动：streamlit run app.py   （Windows 下双击 scripts/start.bat）
本文件只负责页面配置、数据库检查、导航与公共侧栏；各页面逻辑位于 pages/。
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

MIN_STREAMLIT = (1, 50)  # st.dialog(on_dismiss=)、按钮 width="stretch" 等需要的最低版本
_ver = tuple(int(x) for x in st.__version__.split(".")[:2] if x.isdigit())
if _ver < MIN_STREAMLIT:
    st.error(f"当前 Streamlit 版本为 {st.__version__}，本软件需要 {MIN_STREAMLIT[0]}.{MIN_STREAMLIT[1]} 或更高版本。")
    st.info("推荐：双击 scripts/start.bat，它会在项目内创建独立环境并安装正确版本（不影响 Anaconda）。\n\n"
            "或在命令行执行：pip install -U \"streamlit>=1.50\"")
    st.stop()

st.set_page_config(page_title=config.APP_TITLE, page_icon=config.APP_ICON, layout="wide",
                   initial_sidebar_state="expanded")

from components.sidebar import bootstrap, project_selector, recovery_panel  # noqa: E402
from components import task_extras  # noqa: E402
from components.task_form import dialog_router  # noqa: E402
from components.ui import inject_css, show_flash  # noqa: E402

task_extras.register()  # 任务详情：关联文件、工作进展记录、阶段性成果
inject_css()
if config.is_demo():
    st.info("**演示版**：可以随意试用，但修改不会长期保存（应用重启后恢复为示例数据）。请勿输入真实或敏感信息。"
            "“打开文件”“在文件夹中显示”“浏览…”等需要本机的功能在演示版中不可用。", icon=":material/info:")
if not bootstrap():
    recovery_panel()
    st.stop()

P = lambda f, t, icon, **kw: st.Page(f"pages/{f}", title=t, icon=icon, **kw)  # noqa: E731
SETTINGS_PAGE = P("07_系统设置.py", "系统设置", ":material/settings:")
NAV = {
    "工作空间": [
        P("01_项目总览.py", "项目总览", ":material/dashboard:", default=True),
        P("02_全部任务.py", "全部任务", ":material/checklist:"),
        P("03_任务树.py", "任务树", ":material/account_tree:"),
        P("04_日历视图.py", "日历视图", ":material/calendar_month:"),
        P("05_统计分析.py", "统计分析", ":material/bar_chart:"),
    ],
    "科研资料": [
        P("09_科研资料库.py", "科研资料库", ":material/folder_open:"),
        P("10_科研日志.py", "科研日志", ":material/edit_note:"),
    ],
    "项目管理": [
        P("06_工作模块.py", "工作模块", ":material/view_module:"),
        SETTINGS_PAGE,
        P("08_数据管理.py", "数据管理", ":material/database:"),
    ],
}
pg = st.navigation(NAV, position="sidebar", expanded=True)
project_selector()
st.sidebar.page_link(SETTINGS_PAGE, label="新建 / 删除项目", icon=":material/add_circle:")
show_flash()
pg.run()
dialog_router()
