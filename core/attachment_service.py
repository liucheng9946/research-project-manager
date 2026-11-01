"""任务关联文件与参考资料（索引式管理，不复制、不移动、不删除用户文件）。

* 本地文件：只保存绝对路径（pathlib 处理，兼容 Windows 盘符路径与 UNC 路径）；
  显示时实时检查是否存在，不存在标记为“失效”，可重新定位；
* 网页链接：校验 http/https 格式，裸 DOI 自动转换为 https://doi.org/...；
* 删除关联只删除数据库记录；失效路径的记录需显式确认才能移除；
* 打开文件通过系统默认程序（os.startfile / open / xdg-open，参数列表调用，不经过 shell），
  脚本与可执行文件（.py .m .bat .exe 等）不会被运行，改为在文件夹中定位。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from core.database import new_id, now_str, rows_to_dicts
from utils.helpers import is_blank
from utils.validators import ValidationError, normalize_url, optional_text, require_text

# 打开时可能执行代码的扩展名：只在文件夹中定位，不直接打开
UNSAFE_EXTENSIONS = {
    ".py", ".pyw", ".pyc", ".ipynb", ".m", ".mlx", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".exe", ".com", ".msi", ".msp", ".scr", ".pif", ".cpl", ".jar",
    ".sh", ".bash", ".zsh", ".command", ".app", ".lnk", ".url", ".hta", ".reg", ".dll", ".sys",
    ".docm", ".xlsm", ".pptm", ".appref-ms", ".application",
}


# ---------------------------------------------------------------------------
# 文件类型（全局，可增加）
# ---------------------------------------------------------------------------
def list_types(conn: sqlite3.Connection) -> list[dict]:
    return rows_to_dicts(conn.execute("SELECT * FROM attachment_types ORDER BY sort_order, name"))


def type_names(conn: sqlite3.Connection) -> list[str]:
    return [t["name"] for t in list_types(conn)]


def add_type(conn: sqlite3.Connection, name: str, extensions: str | None = None) -> str:
    name = require_text(name, "文件类型名称", 50)
    if conn.execute("SELECT 1 FROM attachment_types WHERE name=?", (name,)).fetchone():
        raise ValidationError(f"文件类型“{name}”已存在")
    exts = ",".join(_norm_ext(e) for e in (extensions or "").replace("，", ",").split(",") if e.strip())
    n = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM attachment_types").fetchone()[0]
    tid = new_id()
    ts = now_str()
    conn.execute("INSERT INTO attachment_types(id, name, extensions, sort_order, created_at, updated_at) "
                 "VALUES(?,?,?,?,?,?)", (tid, name, exts, n, ts, ts))
    return tid


def update_type(conn: sqlite3.Connection, type_id: str, extensions: str) -> None:
    exts = ",".join(_norm_ext(e) for e in (extensions or "").replace("，", ",").split(",") if e.strip())
    conn.execute("UPDATE attachment_types SET extensions=?, updated_at=? WHERE id=?", (exts, now_str(), type_id))


def _norm_ext(e: str) -> str:
    e = e.strip().lower()
    return e if e.startswith(".") else "." + e


def guess_type(conn: sqlite3.Connection, path_or_url: str, is_url: bool = False) -> str:
    if is_url:
        return "网页链接"
    ext = _suffix(path_or_url)
    for t in list_types(conn):
        exts = {x.strip() for x in (t["extensions"] or "").split(",") if x.strip()}
        if ext and ext in exts:
            return t["name"]
    return "其他"


# ---------------------------------------------------------------------------
# 路径处理
# ---------------------------------------------------------------------------
def _is_windows_style(p: str) -> bool:
    pw = PureWindowsPath(p)
    return bool(pw.drive) or p.startswith("\\\\")


def normalize_path(raw: str) -> str:
    """规范化并校验为绝对路径（不访问文件系统）。"""
    if is_blank(raw):
        raise ValidationError("文件路径不能为空")
    s = str(raw).strip().strip('"').strip("'")
    if _is_windows_style(s):
        pw = PureWindowsPath(s)
        if not pw.is_absolute():
            raise ValidationError(f"请填写完整的绝对路径（如 D:/Research/Fig05.png），当前为“{s}”")
        return str(pw)
    p = Path(s).expanduser()
    if not p.is_absolute():
        raise ValidationError(f"请填写完整的绝对路径（如 D:/Research/Fig05.png），当前为“{s}”")
    return str(Path(os.path.normpath(p)))


def _suffix(path: str) -> str:
    return (PureWindowsPath(path) if _is_windows_style(path) else PurePosixPath(path)).suffix.lower()


def _basename(path: str) -> str:
    return (PureWindowsPath(path) if _is_windows_style(path) else PurePosixPath(path)).name


def file_exists(path: str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


def status_of(att: dict) -> str:
    """ok / missing / url"""
    if att["storage_type"] == "url":
        return "url"
    return "ok" if file_exists(att["file_path"]) else "missing"


STATUS_LABELS = {"ok": "可用", "missing": "失效（文件不存在）", "url": "网页链接"}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _check_task(conn, task_id):
    if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
        raise ValidationError("任务不存在")


def _check_type(conn, attachment_type: str | None, default: str) -> str:
    t = attachment_type or default
    if t not in type_names(conn):
        raise ValidationError(f"未知的文件类型：{t}（可在系统设置中增加）")
    return t


def add_local(conn: sqlite3.Connection, task_id: str, file_path: str, name: str | None = None,
              attachment_type: str | None = None, description: str | None = None,
              allow_missing: bool = False) -> str:
    _check_task(conn, task_id)
    path = normalize_path(file_path)
    if not allow_missing and not file_exists(path):
        raise ValidationError(f"文件不存在：{path}。如确认路径正确（如移动硬盘未连接），可选择“仍然添加”")
    atype = _check_type(conn, attachment_type, guess_type(conn, path))
    aid = new_id()
    ts = now_str()
    conn.execute("INSERT INTO task_attachments(id, task_id, name, attachment_type, storage_type, file_path, "
                 "description, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (aid, task_id, optional_text(name, 200) or _basename(path), atype, "local", path,
                  optional_text(description), ts, ts))
    return aid


def add_url(conn: sqlite3.Connection, task_id: str, url: str, name: str | None = None,
            attachment_type: str | None = None, description: str | None = None) -> str:
    _check_task(conn, task_id)
    u = normalize_url(url)
    atype = _check_type(conn, attachment_type, "网页链接")
    aid = new_id()
    ts = now_str()
    conn.execute("INSERT INTO task_attachments(id, task_id, name, attachment_type, storage_type, url, "
                 "description, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (aid, task_id, optional_text(name, 200) or u, atype, "url", u,
                  optional_text(description), ts, ts))
    return aid


def get(conn: sqlite3.Connection, att_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM task_attachments WHERE id=?", (att_id,)).fetchone()
    return dict(r) if r else None


def update(conn: sqlite3.Connection, att_id: str, name: str | None = None, description: str | None = None,
           attachment_type: str | None = None, url: str | None = None) -> None:
    a = get(conn, att_id)
    if not a:
        raise ValidationError("关联记录不存在")
    new = {
        "name": require_text(name, "显示名称", 200) if name is not None else a["name"],
        "description": optional_text(description) if description is not None else a["description"],
        "attachment_type": _check_type(conn, attachment_type, a["attachment_type"]),
        "url": normalize_url(url) if (url is not None and a["storage_type"] == "url") else a["url"],
    }
    conn.execute("UPDATE task_attachments SET name=?, description=?, attachment_type=?, url=?, updated_at=? "
                 "WHERE id=?", (new["name"], new["description"], new["attachment_type"], new["url"],
                                now_str(), att_id))


def relocate(conn: sqlite3.Connection, att_id: str, new_path: str) -> None:
    """重新定位本地文件（新位置必须存在）。"""
    a = get(conn, att_id)
    if not a or a["storage_type"] != "local":
        raise ValidationError("只有本地文件可以重新定位")
    path = normalize_path(new_path)
    if not file_exists(path):
        raise ValidationError(f"新的位置仍然找不到文件：{path}")
    conn.execute("UPDATE task_attachments SET file_path=?, updated_at=? WHERE id=?", (path, now_str(), att_id))


def remove(conn: sqlite3.Connection, att_id: str, confirm_missing: bool = False) -> None:
    """移除关联（只删除数据库记录，绝不删除实际文件）。失效路径需要显式确认。"""
    a = get(conn, att_id)
    if not a:
        return
    if a["storage_type"] == "local" and not file_exists(a["file_path"]) and not confirm_missing:
        raise ValidationError("该文件路径已失效。请先尝试“重新定位”；如确认不再需要，请勾选确认后手动移除")
    conn.execute("DELETE FROM task_attachments WHERE id=?", (att_id,))


def list_for_task(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = rows_to_dicts(conn.execute(
        "SELECT * FROM task_attachments WHERE task_id=? ORDER BY created_at DESC, rowid DESC", (task_id,)))
    for r in rows:
        r["status"] = status_of(r)
    return rows


def list_for_project(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = rows_to_dicts(conn.execute(
        "SELECT a.*, t.task_code, t.title AS task_title, t.project_id FROM task_attachments a "
        "JOIN tasks t ON t.id=a.task_id WHERE t.project_id=? ORDER BY a.created_at DESC, a.rowid DESC",
        (project_id,)))
    for r in rows:
        r["status"] = status_of(r)
        r["location"] = r["file_path"] if r["storage_type"] == "local" else r["url"]
    return rows


# ---------------------------------------------------------------------------
# 安全打开（仅在本机运行时有意义）
# ---------------------------------------------------------------------------
def is_unsafe_to_open(path: str) -> bool:
    return _suffix(path) in UNSAFE_EXTENSIONS


def _run(args: list[str] | str) -> None:
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def reveal_in_folder(path: str) -> str:
    p = Path(path)
    if not p.exists() and not p.parent.exists():
        raise ValidationError("文件及其所在文件夹都不存在")
    if sys.platform.startswith("win"):
        if p.exists():
            # explorer 要求引号只包住路径（/select,"路径"）；用列表传参时 Python 会把整个参数加引号，
            # 路径含空格就会退化为打开“文档”。Windows 文件名不能含双引号，且不经过 shell，拼接是安全的。
            _run(f'explorer.exe /select,"{p}"')
        else:
            os.startfile(str(p.parent))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        _run(["open", "-R", str(p)] if p.exists() else ["open", str(p.parent)])
    else:
        _run(["xdg-open", str(p.parent)])
    return "已在文件夹中定位该文件"


def open_local(path: str) -> str:
    """用系统默认程序打开文件；脚本/可执行文件只在文件夹中定位，不会被运行。"""
    if not file_exists(path):
        raise ValidationError("文件不存在，无法打开。请重新定位该文件")
    if is_unsafe_to_open(path):
        reveal_in_folder(path)
        return "出于安全考虑，脚本和可执行文件不会被直接打开，已在文件夹中定位该文件"
    if sys.platform.startswith("win"):
        os.startfile(str(Path(path)))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        _run(["open", "--", str(path)])
    else:
        _run(["xdg-open", str(path)])
    return "已用系统默认程序打开"


_PICKER = r"""
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    sys.exit(3)
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
p = filedialog.askopenfilename(title=sys.argv[1] if len(sys.argv) > 1 else "选择文件")
root.destroy()
sys.stdout.write(p or "")
"""


def pick_file_dialog(title: str = "选择要关联的文件", timeout: int = 600) -> str | None:
    """在本机弹出系统文件选择窗口（独立进程，避免阻塞界面线程）。返回绝对路径；取消返回 ''。
    无图形界面或 tkinter 不可用时返回 None。"""
    try:
        # -X utf8：Windows 中文系统下子进程默认按 GBK 输出，必须强制 UTF-8 才能原样取回中文路径
        r = subprocess.run([sys.executable, "-X", "utf8", "-c", _PICKER, title], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()
