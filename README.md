# 科研项目管理系统（演示版）

一个本地运行的科研项目任务管理工具（Streamlit + SQLite），以“项目”为最高管理单元，管理任务、子任务、进度、依赖、负责人、截止日期、关联文件、工作历史和科研日志。

本仓库用于**展示与试用**：部署到 Streamlit Community Cloud 后，会自动生成一个虚构的示例项目“示例：结构试验研究计划（演示）”。云端的修改不会长期保存，应用重启后恢复为示例数据。

## 功能

- **工作空间**：项目总览（统计卡片、三种进度口径、工作模块与章节进度、近期任务、分布图）、全部任务（搜索、组合筛选、排序、表格 / 卡片视图、行内修改、批量操作、导出）、任务树、日历视图、统计分析
- **科研资料**：科研资料库、科研日志
- **项目管理**：工作模块、系统设置（项目、优先级、状态、章节、类别、标签、负责人）、数据管理（Excel 导入 / 导出、SQLite 与 JSON 备份恢复）
- 规则：状态与完成度相互独立；父任务进度按子任务加权自动计算；整体进度只统计叶子任务；禁止循环层级与循环依赖；删除父任务必须选择处理子任务的方式；所有写操作在事务中执行。

## 部署到 Streamlit Community Cloud（演示模式）

1. 打开 https://share.streamlit.io ，用 GitHub 账号登录，点 **Create app** →“Deploy a public app from GitHub”。
2. 选择本仓库，分支 `main`，主文件 `app.py`。
3. 展开 **Advanced settings**：Python 版本选 3.12 或 3.13；在 **Secrets** 中填写：
   ```toml
   RPM_DEMO = "1"
   ```
4. 点 **Deploy**，几分钟后即可访问。

演示模式下：数据库为空时自动写入示例项目；页面顶部显示“演示版”提示；“打开文件”“在文件夹中显示”“浏览…”等只能在本机使用的按钮被停用（服务器无法访问你电脑上的文件）。

## 在本机运行

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\initialize.py --demo
.venv\Scripts\python -m streamlit run app.py
```
`--demo` 会在空数据库中写入示例项目（不加则从空数据库开始）。数据保存在 `data/database/research_manager.db`，该文件已被 `.gitignore` 排除，不会上传。

## 目录

```
app.py              入口：页面配置、数据库检查、导航、侧栏
config.py           路径与常量（RPM_DEMO 演示模式开关）
pages/              10 个页面
components/         界面组件
core/               数据访问与业务逻辑（SQLite）
utils/              校验、日期、格式化
scripts/            初始化、命令行备份、示例数据（demo_seed.py）
tests/              示例数据测试
```
