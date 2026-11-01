"""初始化/检查数据库（可重复运行，幂等）。

* 数据库不存在 → 创建；版本较旧 → 迁移前自动备份后升级；
* 已经存在数据时绝不覆盖；
* 加参数 --demo 时，若数据库中还没有任何项目，写入虚构的示例项目。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.backup_service import pre_migration_backup  # noqa: E402
from core.database import DatabaseError, check_database, init_database  # noqa: E402


def main() -> int:
    db = config.get_db_path()
    info = check_database(db)
    if info["exists"] and not info["ok"]:
        print(f"[错误] {info['message']}\n数据库：{db}\n请在软件“数据管理”页从备份恢复，程序不会覆盖该文件。")
        return 2
    try:
        res = init_database(db, backup_func=pre_migration_backup)
    except DatabaseError as e:
        print(f"[错误] 数据库初始化/升级失败（已回滚）：{e}")
        return 2
    except (sqlite3.Error, OSError) as e:
        print(f"[错误] 无法访问数据库（可能被其他程序占用或所在位置不可写）：{e}\n数据库：{db}\n数据未被修改。")
        return 2
    if res["applied"]:
        print(f"[数据库] 已{'创建' if not res['from'] else '升级'}到版本 {res['to']}"
              + (f"（升级前备份：{res['backup']}）" if res.get("backup") else ""))
    else:
        print(f"[数据库] 版本 {res['to']}，无需升级：{db}")
    if "--demo" in sys.argv:
        from scripts.demo_seed import seed_demo
        print("[示例] 已写入示例项目" if seed_demo(db) else "[示例] 数据库中已有项目，未写入示例数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
