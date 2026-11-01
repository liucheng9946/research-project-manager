"""云端演示版的示例数据（虚构内容，不含任何真实研究数据）。

只在演示模式下、且数据库中还没有任何项目时写入；已有项目时什么也不做，因此不会影响正常数据。
截止日期按“今天”前后推算，任何时候打开演示版都能看到即将到期、已逾期等效果。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import activity_service as act  # noqa: E402
from core import attachment_service as att  # noqa: E402
from core import category_service as cs  # noqa: E402
from core import daily_log_service as dl  # noqa: E402
from core import task_service as ts  # noqa: E402
from core.database import transaction  # noqa: E402
from core.project_service import create_project, set_current_project  # noqa: E402

PROJECT = "示例：结构试验研究计划（演示）"
DESCRIPTION = "云端演示用的虚构项目。可以随意新建、修改、删除；应用重启后会恢复为这份示例数据。"

CHAPTERS = ["阶段1：资料与数据准备", "阶段2：试验与数值分析", "阶段3：结果整理与写作"]
CATEGORIES = [("数据准备", "#0EA5E9"), ("试验", "#16A34A"), ("数值分析", "#2563EB"), ("论文写作", "#7C3AED")]

# (编号, 名称, 父任务编号, 章节序号, 类别, 优先级, 状态, 完成度, 截止日期相对今天的天数, 负责人, 标签)
TASKS = [
    ("S1", "资料与数据准备", None, 0, None, "P0", "进行中", 0, None, None, []),
    ("S101", "文献调研与对比试验整理", "S1", 0, "数据准备", "P1", "已完成", 100, -20, "研究者A", ["核心论文"]),
    ("S102", "试件参数表与数据格式定义", "S1", 0, "数据准备", "P0", "已完成", 100, -12, "研究者B", []),
    ("S103", "原始数据完整性核查", "S1", 0, "数据准备", "P0", "进行中", 60, -2, "研究者B", ["数据核查"]),
    ("S2", "试验与数值分析", None, 1, None, "P0", "进行中", 0, None, None, []),
    ("S201", "缩尺试件加载试验", "S2", 1, "试验", "P0", "进行中", 40, 6, "研究者A", ["紧急"]),
    ("S2011", "加载装置调试", "S201", 1, "试验", "P0", "已完成", 100, -5, "研究者A", []),
    ("S2012", "正式加载与数据采集", "S201", 1, "试验", "P0", "待开始", 0, 6, "研究者A", []),
    ("S202", "有限元模型建立与校准", "S2", 1, "数值分析", "P1", "进行中", 30, 10, "研究者C", []),
    ("S203", "参数敏感性分析", "S2", 1, "数值分析", "P2", "待开始", 0, 25, "研究者C", ["图表"]),
    ("S204", "误差统计与不确定性分析", "S2", 1, "数值分析", "P1", "阻塞", 10, 15, "研究者B", ["需要人工确认"]),
    ("S3", "结果整理与写作", None, 2, None, "P1", "待开始", 0, None, None, []),
    ("S301", "结果图表绘制", "S3", 2, "论文写作", "P1", "待开始", 0, 30, "研究者A+研究者C", ["图表"]),
    ("S302", "论文初稿", "S3", 2, "论文写作", "P1", "待开始", 0, 45, "研究者A", ["核心论文"]),
    ("S303", "导师审阅与修改", "S3", 2, "论文写作", "P2", "暂缓", 0, None, "研究者A", ["待导师审核"]),
]
# (任务, 前置任务)
DEPENDENCIES = [("S2012", "S2011"), ("S202", "S102"), ("S204", "S103"), ("S301", "S204"), ("S302", "S301")]


def seed_demo(db_path=None) -> str | None:
    """数据库中没有任何项目时写入示例项目，返回项目 ID；已有项目时返回 None。"""
    today = date.today()
    with transaction(db_path) as conn:
        if conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]:
            return None
        pid = create_project(conn, PROJECT, DESCRIPTION, mode="blank")
        for name in ("核心论文", "图表", "数据核查", "需要人工确认", "待导师审核", "紧急"):
            cs.create_item(conn, "tags", pid, name=name)
        tag_id = {t["name"]: t["id"] for t in cs.list_items(conn, "tags", pid)}
        chap_id = [cs.create_item(conn, "chapters", pid, name=n) for n in CHAPTERS]
        cat_id = {n: cs.create_item(conn, "categories", pid, name=n, color=c) for n, c in CATEGORIES}
        pri_id = {p["name"]: p["id"] for p in cs.list_items(conn, "priorities", pid)}
        st_id = {s["name"]: s["id"] for s in cs.list_items(conn, "statuses", pid)}
        ids: dict[str, str] = {}
        for code, title, parent, ch, cat, pri, status, prog, due, who, tags in TASKS:
            ids[code] = ts.create_task(conn, pid, {
                "task_code": code, "title": title, "parent_id": ids.get(parent) if parent else None,
                "chapter_id": chap_id[ch], "category_id": cat_id.get(cat), "priority_id": pri_id[pri],
                "status_id": st_id[status], "progress": prog, "assignee": who,
                "due_date": (today + timedelta(days=due)) if due is not None else None,
                "progress_mode": "auto" if code in ("S1", "S2", "S3", "S201") else "manual",
                "description": f"（示例）{title}的工作内容说明。",
                "deliverable": f"{title}成果文件", "acceptance_criteria": "（示例）由负责人检查并确认。",
            }, tag_ids=[tag_id[t] for t in tags], recalc=False)
        for task, pre in DEPENDENCIES:
            ts.update_task(conn, ids[task], {}, dependency_ids=[ids[pre]], recalc=False)
        ts._run_progress_recalc(conn, pid)
        act.add_log(conn, ids["S103"], "（示例）已核对 60% 的原始数据，发现 3 处缺失，待补充。", "note")
        act.add_log(conn, ids["S2011"], "（示例）加载装置调试完成，可以开始正式加载。", "milestone")
        att.add_url(conn, ids["S101"], "https://doi.org/10.1155/2020/8858945", "示例参考文献（DOI 链接）")
        dl.create(conn, pid, today, "（示例）加载装置调试与数据核查", content="完成加载装置调试，继续核查原始数据。",
                  done_work="加载装置调试完成", problems="原始数据有 3 处缺失", next_steps="开始正式加载",
                  related_task_ids=[ids["S2011"], ids["S103"]])
        set_current_project(conn, pid)
    return pid


if __name__ == "__main__":
    from core.database import init_database
    init_database()
    print(seed_demo() or "数据库中已有项目，未写入示例数据")
