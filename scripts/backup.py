"""命令行备份工具。

    python scripts/backup.py            # 立即备份数据库（SQLite 在线备份接口）
    python scripts/backup.py --json     # 同时导出 JSON 全量备份
    python scripts/backup.py --list     # 列出已有备份
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import backup_service as bk  # noqa: E402
from utils.formatters import fmt_size  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="备份科研项目管理系统数据库")
    ap.add_argument("--json", action="store_true", help="同时导出 JSON 全量备份")
    ap.add_argument("--list", action="store_true", help="列出已有备份")
    a = ap.parse_args()
    if a.list:
        for b in bk.list_backups():
            print(f"{b['created']}  {b['kind_label']:<10} {fmt_size(b['size']):>9}  {b['name']}")
        return 0
    p = bk.backup_database("manual")
    print(f"数据库备份完成：{p}")
    if a.json:
        jp = config.get_backups_dir() / f"research_manager_{datetime.now():%Y%m%d_%H%M%S}.json"
        jp.write_bytes(bk.export_json_bytes())
        print(f"JSON 备份完成：{jp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
