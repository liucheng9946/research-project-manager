"""进度计算。

* 父任务自动模式：P_parent = Σ(w_i·P_i) / Σw_i，P_i 为直接子任务的（有效）进度，
  递归计算，保证多层级正确汇总；已取消类子任务不参与。
* 自动模式但没有子任务的任务按其自身进度计算（相当于叶子）。
* 整体进度只统计叶子任务，父任务不重复计入：
    A 任务完成率   = 已完成叶子数 / 有效叶子数
    B 平均完成度   = 有效叶子完成度平均值（默认口径）
    C 加权完成度   = Σ(w·P) / Σw（叶子）
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

import pandas as pd

from core.database import now_str

METRIC_LABELS = {
    "A": "任务完成率（已完成叶子任务数 / 有效叶子任务数）",
    "B": "平均完成度（有效叶子任务完成度的平均值）",
    "C": "加权完成度（Σ权重×完成度 / Σ权重，仅叶子任务）",
}


def compute_progress(tasks: Iterable[dict], excluded_ids: set[str] | None = None) -> dict[str, float]:
    """纯函数：根据任务列表计算每个任务的有效进度。

    tasks 中每项需包含 id、parent_id、progress、progress_mode、weight。
    excluded_ids：不参与父任务汇总的任务（如已取消类）。
    """
    tasks = list(tasks)
    excluded_ids = excluded_ids or set()
    by_id = {t["id"]: t for t in tasks}
    children: dict[str, list[str]] = {}
    for t in tasks:
        p = t.get("parent_id")
        if p and p in by_id:
            children.setdefault(p, []).append(t["id"])

    result: dict[str, float] = {}

    # 迭代后序遍历，避免深层级时递归过深
    def resolve(root: str) -> None:
        stack = [(root, False)]
        while stack:
            tid, expanded = stack.pop()
            if tid in result:
                continue
            kids = [k for k in children.get(tid, []) if k not in excluded_ids]
            t = by_id[tid]
            if t.get("progress_mode") == "auto" and kids:
                if not expanded:
                    stack.append((tid, True))
                    stack.extend((k, False) for k in kids if k not in result)
                    continue
                total_w = sum(float(by_id[k].get("weight") or 1) for k in kids)
                val = sum(float(by_id[k].get("weight") or 1) * result[k] for k in kids) / total_w
                result[tid] = round(val, 4)
            else:
                result[tid] = float(t.get("progress") or 0)

    for t in tasks:
        resolve(t["id"])
    return result


def recalc_project(conn: sqlite3.Connection, project_id: str) -> list[tuple[str, float, float]]:
    """重新计算项目内所有自动模式任务的进度并写回数据库。返回 (id, 旧值, 新值) 列表。"""
    rows = [dict(r) for r in conn.execute(
        "SELECT t.id, t.parent_id, t.progress, t.progress_mode, t.weight, s.kind "
        "FROM tasks t LEFT JOIN statuses s ON s.id=t.status_id WHERE t.project_id=?",
        (project_id,))]
    excluded = {r["id"] for r in rows if r["kind"] == "cancelled"}
    computed = compute_progress(rows, excluded)
    changed = []
    ts = now_str()
    for r in rows:
        new = computed[r["id"]]
        if r["progress_mode"] == "auto" and abs(new - float(r["progress"])) > 1e-6:
            conn.execute("UPDATE tasks SET progress=?, updated_at=? WHERE id=?",
                         (new, ts, r["id"]))
            changed.append((r["id"], float(r["progress"]), new))
    return changed


def effective_leaves(df: pd.DataFrame, exclude_paused: bool = False) -> pd.DataFrame:
    """有效叶子任务：没有子任务、且不是已取消类（可选排除暂缓类）。"""
    if df.empty:
        return df
    mask = df["is_leaf"] & (df["status_kind"] != "cancelled")
    if exclude_paused:
        mask &= df["status_kind"] != "paused"
    return df[mask]


def overall_metrics(df: pd.DataFrame, exclude_paused: bool = False,
                    completion_basis: str = "status") -> dict:
    """三种整体进度口径，分别给出；无任务时全部为 0。"""
    leaves = effective_leaves(df, exclude_paused)
    n = int(len(leaves))
    if n == 0:
        return {"A": 0.0, "B": 0.0, "C": 0.0, "leaf_count": 0, "done_leaf_count": 0}
    if completion_basis == "progress":
        done = int((leaves["progress"] >= 100).sum())
    else:
        done = int((leaves["status_kind"] == "done").sum())
    w = leaves["weight"].astype(float)
    p = leaves["progress"].astype(float)
    return {
        "A": round(done / n * 100, 2),
        "B": round(float(p.mean()), 2),
        "C": round(float((w * p).sum() / w.sum()), 2) if w.sum() > 0 else 0.0,
        "leaf_count": n,
        "done_leaf_count": done,
    }
