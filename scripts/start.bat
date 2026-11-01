@echo off
chcp 65001 >nul
rem ===== Research Project Manager - Windows launcher (V1.1) =====
rem First run: creates .venv and installs requirements (needs network). Later runs start directly.
rem Never deletes or overwrites an existing database. Safe for paths with Chinese characters or spaces.
setlocal EnableExtensions
cd /d "%~dp0.." || (echo [错误] 无法进入项目目录。 & pause & exit /b 1)

set "PY="
rem --- Pick a real Python >= 3.10 (skips the Microsoft Store stub and old versions such as 3.8) ---
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [错误] 未找到可用的 Python 3.10 或更高版本。
    echo        若已安装,可能是旧版本^(如 3.8^)或 Microsoft Store 的占位程序。
    echo        请从 https://www.python.org/downloads/ 安装 Python 3.12 或 3.13,并勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)

rem --- Reuse existing virtual environment ---
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 现有虚拟环境 .venv 的 Python 版本过低或已损坏。
        echo        请手动删除项目目录下的 .venv 文件夹后重新运行本脚本 ^(不会影响 data 和 backups^)。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 正在创建虚拟环境 .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

rem --- Install dependencies only when requirements.txt changed since the last successful install ---
set "NEED_DEPS=1"
if exist ".venv\.deps_installed" (
    fc /b requirements.txt ".venv\.deps_installed" >nul 2>nul && set "NEED_DEPS="
)
if defined NEED_DEPS (
    echo [2/3] 正在安装依赖 ^(首次运行或依赖清单变化时需要联网^) ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败,请检查网络后重新运行。已安装的部分不会丢失。
        echo        若提示 "系统找不到指定的路径" 或 long path,说明项目所在目录层级过深,请把项目移到较浅的目录(如 D:\Research\^)。
        pause
        exit /b 1
    )
    copy /y requirements.txt ".venv\.deps_installed" >nul
)

rem --- Database check: creates it if missing, backs up before upgrading, never overwrites existing data ---
echo [3/3] 正在检查数据库 ...
".venv\Scripts\python.exe" -X utf8 scripts\initialize.py
if errorlevel 1 (
    echo.
    echo [错误] 数据库检查未通过,软件未启动。上方显示了具体原因;数据库文件未被修改。
    pause
    exit /b 2
)

echo.
echo 正在启动,浏览器将自动打开 http://localhost:8501
echo 关闭本窗口即可退出软件。若提示端口被占用,请先关闭已打开的本软件窗口。
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
if errorlevel 1 (
    echo.
    echo [错误] 软件异常退出,请查看上方的错误信息。
)
pause
