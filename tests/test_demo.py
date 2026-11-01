"""演示模式与示例数据测试（全部使用临时数据库）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "demo.db"
    monkeypatch.setenv("RPM_DB_PATH", str(p))
    monkeypatch.setenv("RPM_BACKUPS_DIR", str(tmp_path / "backups"))
    from core.database import init_database
    init_database(p)
    return p


def test_demo_switch(monkeypatch):
    import config
    monkeypatch.delenv("RPM_DEMO", raising=False)
    assert not config.is_demo()
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("RPM_DEMO", v)
        assert config.is_demo()
    monkeypatch.setenv("RPM_DEMO", "0")
    assert not config.is_demo()


def test_seed_creates_complete_project(db):
    from core import task_service as ts
    from core.database import read_conn
    from core.progress_service import overall_metrics
    from scripts.demo_seed import DEPENDENCIES, PROJECT, TASKS, seed_demo
    pid = seed_demo(db)
    assert pid
    with read_conn(db) as c:
        df = ts.list_tasks_df(c, pid)
        name = c.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()[0]
        deps = c.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0]
        logs = c.execute("SELECT COUNT(*) FROM project_daily_logs").fetchone()[0]
    assert name == PROJECT and len(df) == len(TASKS) and deps == len(DEPENDENCIES) and logs == 1
    assert int(df["depth"].max()) == 2                       # 三级层级
    assert df["overdue"].any() and df["due_date"].notna().any()
    by = df.set_index("task_code")
    assert by.loc["S201", "progress"] == 50                  # 自动进度：(100 + 0) / 2
    assert 0 < overall_metrics(df)["B"] < 100


def test_seed_never_touches_existing_data(db):
    from core.database import transaction
    from core.project_service import create_project
    from scripts.demo_seed import seed_demo
    with transaction(db) as c:
        create_project(c, "我的项目", mode="blank")
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert seed_demo(db) is None
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM projects").fetchone()[0] == before


def test_seed_is_idempotent(db):
    from scripts.demo_seed import seed_demo
    assert seed_demo(db)
    assert seed_demo(db) is None
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 15


def test_no_private_project_content_in_repository():
    banned = "II" + "SR"  # 拼接写法，避免本测试文件自身出现该词
    hits = []
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix in (".py", ".md", ".txt", ".toml", ".bat"):
            if banned in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(p.relative_to(ROOT)))
    assert not hits, hits
    assert not list((ROOT / "data" / "imports").glob("*.xlsx"))
