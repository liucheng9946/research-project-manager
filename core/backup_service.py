"""数据库备份与恢复（SQLite 在线备份接口）以及 JSON 全量备份/恢复。

恢复流程（SQLite 与 JSON 相同）：
  1. 验证备份内容；
  2. 自动备份当前数据库（pre_restore）；
  3. 用 SQLite 备份接口把备份内容整体写入当前数据库；
  4. 验证恢复结果；
  5. 任一步失败 → 用第 2 步的备份还原，原数据不受影响。
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import config
from core import schema
from core.database import (DatabaseError, connect, ensure_v2_defaults, get_global_setting,
                           get_schema_version, init_database, migrate,
                           set_global_setting, transaction)

BACKUP_KINDS = {
    "manual": "手动备份",
    "auto": "自动备份",
    "pre_restore": "恢复前自动备份",
    "pre_migration": "升级前自动备份",
    "pre_import": "导入前自动备份",
    "pre_reset": "重置前自动备份",
}
_NAME_RE = re.compile(r"^research_manager_(?P<kind>[a-z_]+)_(?P<ts>\d{8}_\d{6}_\d{3})\.db$")
JSON_FORMAT = "research-project-manager"


def _backups_dir(dest_dir: str | Path | None = None) -> Path:
    d = Path(dest_dir) if dest_dir else config.get_backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sqlite_copy(src_path: Path, dst_path: Path) -> None:
    """使用 SQLite 官方在线备份接口复制数据库（写入期间也安全）。"""
    src = sqlite3.connect(str(src_path), timeout=15)
    dst = sqlite3.connect(str(dst_path), timeout=15)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_database(kind: str = "manual", db_path: str | Path | None = None,
                    dest_dir: str | Path | None = None) -> Path:
    if kind not in BACKUP_KINDS:
        raise ValueError(f"未知备份类型：{kind}")
    src = Path(db_path) if db_path else config.get_db_path()
    if not src.exists():
        raise DatabaseError(f"数据库文件不存在，无法备份：{src}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    dst = _backups_dir(dest_dir) / f"research_manager_{kind}_{ts}.db"
    _sqlite_copy(src, dst)
    ok, msg, _ = validate_backup(dst)
    if not ok:
        dst.unlink(missing_ok=True)
        raise DatabaseError(f"备份文件校验失败：{msg}")
    return dst


def pre_migration_backup(db_path: Path) -> Path:
    return backup_database("pre_migration", db_path)


def list_backups(dest_dir: str | Path | None = None) -> list[dict]:
    out = []
    for p in sorted(_backups_dir(dest_dir).glob("*.db"), key=lambda x: x.stat().st_mtime,
                    reverse=True):
        m = _NAME_RE.match(p.name)
        kind = m.group("kind") if m else "unknown"
        out.append({
            "path": str(p), "name": p.name, "kind": kind,
            "kind_label": BACKUP_KINDS.get(kind, "其他"),
            "size": p.stat().st_size,
            "created": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return out


def latest_backup_time(dest_dir: str | Path | None = None) -> str | None:
    b = list_backups(dest_dir)
    return b[0]["created"] if b else None


def validate_backup(path: str | Path) -> tuple[bool, str, dict]:
    """检查文件是否为本系统可用的 SQLite 备份。返回 (是否有效, 说明, 摘要)。"""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False, "文件不存在或为空", {}
    with open(p, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            return False, "不是 SQLite 数据库文件", {}
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            chk = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if chk != "ok":
                return False, f"完整性检查失败：{chk}", {}
            ver = get_schema_version(conn)
            if ver == 0:
                return False, "缺少版本信息，不是本系统的数据库", {}
            if ver > schema.SCHEMA_VERSION:
                return False, f"备份版本 {ver} 高于程序支持的版本 {schema.SCHEMA_VERSION}", {}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = {"projects", "tasks", "categories", "statuses", "priorities"} - tables
            if missing:
                return False, f"缺少必要数据表：{', '.join(sorted(missing))}", {}
            info = {
                "version": ver,
                "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return False, f"无法读取：{e}", {}
    return True, "有效", info


def _counts(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        return {"projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]}
    finally:
        conn.close()


def _replace_live_db(source: Path, db_path: Path, expected: dict) -> dict:
    """把 source 的内容整体写入当前数据库；失败时自动还原。"""
    pre = backup_database("pre_restore", db_path) if db_path.exists() else None
    try:
        _sqlite_copy(source, db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DatabaseError("恢复后完整性检查失败")
        finally:
            conn.close()
        migrate(db_path, backup_func=None)  # 旧版本备份恢复后升级到当前结构
        got = _counts(db_path)
        if got != expected:
            raise DatabaseError(f"恢复后数据核对不一致：期望 {expected}，实际 {got}")
        return {"pre_restore_backup": str(pre) if pre else None, **got}
    except Exception as e:
        if pre:
            _sqlite_copy(pre, db_path)
        raise DatabaseError(f"恢复失败，已保留原数据库：{e}") from e


def restore_backup(path: str | Path, db_path: str | Path | None = None) -> dict:
    ok, msg, info = validate_backup(path)
    if not ok:
        raise DatabaseError(f"备份文件无效，拒绝恢复：{msg}")
    target = Path(db_path) if db_path else config.get_db_path()
    return _replace_live_db(Path(path), target, {"projects": info["projects"],
                                                  "tasks": info["tasks"]})


def restore_over_broken(path: str | Path, db_path: str | Path | None = None) -> str:
    """当前数据库已损坏时使用：先把损坏文件原样另存，再用有效备份覆盖。"""
    import shutil
    ok, msg, _ = validate_backup(path)
    if not ok:
        raise DatabaseError(f"备份文件无效：{msg}")
    target = Path(db_path) if db_path else config.get_db_path()
    saved = None
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = _backups_dir() / f"damaged_{ts}_{target.name}"
        shutil.copy2(target, saved)
        target.unlink()
    _sqlite_copy(Path(path), target)
    migrate(target)
    return str(saved) if saved else ""


def delete_backup(path: str | Path, dest_dir: str | Path | None = None) -> None:
    p = Path(path).resolve()
    root = _backups_dir(dest_dir).resolve()
    if p.parent != root or p.suffix != ".db":
        raise DatabaseError("只能删除备份目录中的备份文件")
    p.unlink(missing_ok=True)


def prune_auto_backups(keep: int = config.AUTO_BACKUP_KEEP, dest_dir=None) -> int:
    autos = [b for b in list_backups(dest_dir) if b["kind"] == "auto"]
    removed = 0
    for b in autos[keep:]:
        Path(b["path"]).unlink(missing_ok=True)
        removed += 1
    return removed


def auto_backup_if_due(db_path: str | Path | None = None,
                       hours: int = config.AUTO_BACKUP_INTERVAL_HOURS) -> Path | None:
    target = Path(db_path) if db_path else config.get_db_path()
    if not target.exists():
        return None
    with transaction(target) as conn:
        last = get_global_setting(conn, "last_auto_backup")
    if last:
        try:
            if datetime.now() - datetime.fromisoformat(last) < timedelta(hours=hours):
                return None
        except ValueError:
            pass
    p = backup_database("auto", target)
    with transaction(target) as conn:
        set_global_setting(conn, "last_auto_backup", datetime.now().isoformat(timespec="seconds"))
    prune_auto_backups()
    return p


# ---------------------------------------------------------------------------
# JSON 全量备份
# ---------------------------------------------------------------------------
def export_json(db_path: str | Path | None = None) -> dict:
    conn = connect(db_path)
    try:
        tables = {}
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in schema.DATA_TABLES:
            if t in existing:
                tables[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
        return {"format": JSON_FORMAT, "schema_version": get_schema_version(conn),
                "exported_at": datetime.now().isoformat(timespec="seconds"), "tables": tables}
    finally:
        conn.close()


def export_json_bytes(db_path=None) -> bytes:
    return json.dumps(export_json(db_path), ensure_ascii=False, indent=1).encode("utf-8")


def validate_json(data) -> tuple[bool, str]:
    if not isinstance(data, dict) or data.get("format") != JSON_FORMAT:
        return False, "不是本系统导出的 JSON 备份（缺少 format 标识）"
    ver = data.get("schema_version")
    if not isinstance(ver, int) or ver < 1 or ver > schema.SCHEMA_VERSION:
        return False, f"不支持的数据版本：{ver}"
    tables = data.get("tables")
    if not isinstance(tables, dict):
        return False, "缺少 tables 数据"
    for req in ("projects", "tasks", "statuses", "priorities", "categories"):
        if req not in tables:
            return False, f"缺少必要数据表：{req}"
    for name, rows in tables.items():
        if name not in schema.DATA_TABLES:
            return False, f"包含未知数据表：{name}"
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            return False, f"数据表 {name} 的格式不正确"
    pids = {p.get("id") for p in tables["projects"]}
    for t in tables["tasks"]:
        if not t.get("id") or not t.get("title") or t.get("project_id") not in pids:
            return False, "任务记录缺少 id/title 或所属项目不存在"
    return True, "有效"


def restore_json(data: dict, db_path: str | Path | None = None) -> dict:
    ok, msg = validate_json(data)
    if not ok:
        raise DatabaseError(f"JSON 备份无效，拒绝恢复：{msg}")
    target = Path(db_path) if db_path else config.get_db_path()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "restore.db"
        init_database(tmp)
        conn = connect(tmp)
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")
            for t in schema.DATA_TABLES:  # 临时库中的种子数据由 JSON 内容取代
                if t in data["tables"]:
                    conn.execute(f"DELETE FROM {t}")
            for t in schema.DATA_TABLES:
                rows = data["tables"].get(t, [])
                if not rows:
                    continue
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
                for row in rows:
                    use = [c for c in cols if c in row]
                    conn.execute(f"INSERT INTO {t}({', '.join(use)}) VALUES({', '.join('?' * len(use))})",
                                 [row[c] for c in use])
            if int(data.get("schema_version", 0)) < 2:
                ensure_v2_defaults(conn)  # 旧版本 JSON：补齐 V1.1 默认值并迁移进展记录
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                conn.execute("ROLLBACK")
                raise DatabaseError(f"JSON 数据的关联关系不完整（{len(bad)} 处外键错误）")
            conn.execute("COMMIT")
        except sqlite3.Error as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise DatabaseError(f"JSON 数据写入失败：{e}") from e
        finally:
            conn.close()
        expected = {"projects": len(data["tables"]["projects"]), "tasks": len(data["tables"]["tasks"])}
        return _replace_live_db(tmp, target, expected)
