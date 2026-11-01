"""统计汇总（输入为 task_service.list_tasks_df 的结果，纯 pandas 计算，不访问数据库）。

口径说明：
* “任务数”统计全部任务（含父任务）；
* “平均完成度”只统计有效叶子任务（无子任务、非已取消类，可选排除暂缓类），
  避免父任务与子任务重复计入。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from core.progress_service import effective_leaves
from utils.date_utils import to_date, today

NO_VALUE = "（未设置）"


def _leaf_mean(df: pd.DataFrame, exclude_paused: bool) -> float:
    lv = effective_leaves(df, exclude_paused)
    return round(float(lv["progress"].mean()), 1) if len(lv) else 0.0


def basic_counts(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total": 0, "done": 0, "in_progress": 0, "blocked": 0, "overdue": 0, "paused": 0}
    k = df["status_kind"]
    return {"total": int(len(df)), "done": int((k == "done").sum()),
            "in_progress": int((k == "in_progress").sum()), "blocked": int((k == "blocked").sum()),
            "paused": int((k == "paused").sum()), "overdue": int(df["overdue"].sum())}


def _group_counts(df: pd.DataFrame, name_col: str, color_col: str, order_col: str,
                  all_items: list[dict] | None = None) -> pd.DataFrame:
    cols = ["名称", "任务数", "占比", "颜色"]
    if df.empty and not all_items:
        return pd.DataFrame(columns=cols)
    g = df.assign(_n=df[name_col].fillna(NO_VALUE)).groupby("_n", dropna=False)
    counts = g.size()
    total = int(counts.sum()) or 1
    rows = []
    seen = set()
    for it in all_items or []:
        n = int(counts.get(it["name"], 0))
        rows.append([it["name"], n, round(n / total * 100, 1), it.get("color") or "#6B7280"])
        seen.add(it["name"])
    for name, n in counts.items():
        if name not in seen:
            color = df.loc[df[name_col].fillna(NO_VALUE) == name, color_col].dropna()
            rows.append([name, int(n), round(n / total * 100, 1),
                         color.iloc[0] if len(color) else "#9CA3AF"])
    return pd.DataFrame(rows, columns=cols)


def status_counts(df, statuses=None):
    return _group_counts(df, "status", "status_color", "status_order", statuses)


def priority_counts(df, priorities=None):
    return _group_counts(df, "priority", "priority_color", "priority_order", priorities)


def category_counts(df, categories=None):
    return _group_counts(df, "category", "category_color", "category_order", categories)


def group_progress(df: pd.DataFrame, key: str, items: list[dict] | None = None,
                   exclude_paused: bool = False) -> pd.DataFrame:
    """按分类/章节等汇总：任务数、已完成、进行中、阻塞、平均完成度（叶子）。"""
    cols = ["名称", "任务数", "已完成", "进行中", "阻塞", "平均完成度", "颜色"]
    names = [i["name"] for i in (items or [])]
    colors = {i["name"]: i.get("color") for i in (items or [])}
    if not df.empty:
        extra = [n for n in df[key].fillna(NO_VALUE).unique() if n not in names]
        names += sorted(extra, key=lambda x: (x == NO_VALUE, x))
    rows = []
    for n in names:
        sub = df[df[key].fillna(NO_VALUE) == n] if not df.empty else df
        rows.append([n, int(len(sub)), int((sub["status_kind"] == "done").sum()) if len(sub) else 0,
                     int((sub["status_kind"] == "in_progress").sum()) if len(sub) else 0,
                     int((sub["status_kind"] == "blocked").sum()) if len(sub) else 0,
                     _leaf_mean(sub, exclude_paused) if len(sub) else 0.0,
                     colors.get(n) or "#2563EB"])
    return pd.DataFrame(rows, columns=cols)


def chapter_progress(df: pd.DataFrame, chapters: list[dict] | None = None, by_group: bool = False,
                     exclude_paused: bool = False) -> pd.DataFrame:
    if not by_group:
        return group_progress(df, "chapter", chapters, exclude_paused)
    groups = []
    for c in chapters or []:
        g = c.get("group_name") or c["name"]
        if g not in [x["name"] for x in groups]:
            groups.append({"name": g})
    d = df.copy()
    if not d.empty:
        d["_grp"] = d["chapter_group"].where(d["chapter_group"].notna(), d["chapter"])
    else:
        d["_grp"] = pd.Series(dtype=object)
    return group_progress(d, "_grp", groups, exclude_paused)


def assignee_workload(df: pd.DataFrame, exclude_paused: bool = False) -> pd.DataFrame:
    cols = ["负责人", "任务数", "已完成", "进行中", "阻塞", "逾期", "平均完成度"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    e = df.assign(_p=df["people"].map(lambda x: x if x else [NO_VALUE])).explode("_p")
    rows = []
    for p, sub in e.groupby("_p"):
        rows.append([p, len(sub), int((sub["status_kind"] == "done").sum()),
                     int((sub["status_kind"] == "in_progress").sum()),
                     int((sub["status_kind"] == "blocked").sum()), int(sub["overdue"].sum()),
                     _leaf_mean(sub, exclude_paused)])
    return pd.DataFrame(rows, columns=cols).sort_values(["任务数", "负责人"], ascending=[False, True])


PROGRESS_BINS = [("0%", 0, 0), ("1–25%", 0.0001, 25), ("26–50%", 25.0001, 50),
                 ("51–75%", 50.0001, 75), ("76–99%", 75.0001, 99.9999), ("100%", 100, 100)]


def progress_distribution(df: pd.DataFrame, leaves_only: bool = True) -> pd.DataFrame:
    d = df[df["is_leaf"]] if (leaves_only and not df.empty) else df
    rows = []
    for label, lo, hi in PROGRESS_BINS:
        n = int(((d["progress"] >= lo) & (d["progress"] <= hi)).sum()) if len(d) else 0
        rows.append([label, n])
    out = pd.DataFrame(rows, columns=["完成度区间", "任务数"])
    total = int(out["任务数"].sum()) or 1
    out["占比"] = (out["任务数"] / total * 100).round(1)
    return out


def overdue_tasks(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["overdue"]].sort_values("due_date")


def upcoming_tasks(df: pd.DataFrame, days: int = 14, ref: date | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    ref = ref or today()
    end = ref + timedelta(days=days)
    d = df[(df["status_kind"] != "done") & (df["status_kind"] != "cancelled") & df["due_date"].notna()]
    dd = d["due_date"].map(to_date)
    return d[(dd >= ref) & (dd <= end)].sort_values("due_date")


def recently_updated(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values("updated_at", ascending=False).head(n)


def blocked_tasks(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["status_kind"] == "blocked"]


def overdue_by_group(df: pd.DataFrame, key: str = "priority") -> pd.DataFrame:
    od = overdue_tasks(df)
    if od.empty:
        return pd.DataFrame(columns=["名称", "逾期任务数"])
    return (od[key].fillna(NO_VALUE).value_counts().rename_axis("名称")
            .reset_index(name="逾期任务数"))


def creation_completion_trend(df: pd.DataFrame, start: date | None = None, end: date | None = None,
                              freq: str = "D") -> pd.DataFrame:
    """按日/周统计新建与完成任务数及累计值。完成时间取 completed_at。"""
    cols = ["日期", "新建", "完成", "累计新建", "累计完成"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    created = pd.to_datetime(df["created_at"], errors="coerce").dt.normalize()
    completed = pd.to_datetime(df["completed_at"], errors="coerce").dt.normalize()
    lo = start or min(created.min(), completed.min() if completed.notna().any() else created.min()).date()
    hi = end or today()
    if lo > hi:
        lo, hi = hi, lo
    idx = pd.date_range(lo, hi, freq="D")
    c = created.value_counts().reindex(idx, fill_value=0)
    f = completed.dropna().value_counts().reindex(idx, fill_value=0)
    out = pd.DataFrame({"新建": c, "完成": f}, index=idx)
    # 累计值包含区间开始之前的数量
    before_c = int((created < pd.Timestamp(lo)).sum())
    before_f = int((completed < pd.Timestamp(lo)).sum())
    if freq == "W":
        out = out.resample("W-MON", label="left", closed="left").sum()
    out["累计新建"] = out["新建"].cumsum() + before_c
    out["累计完成"] = out["完成"].cumsum() + before_f
    out = out.reset_index().rename(columns={"index": "日期"})
    out["日期"] = out["日期"].dt.date
    return out[cols]


def filter_by_date_range(df: pd.DataFrame, start: date | None, end: date | None,
                         field: str = "created_at") -> pd.DataFrame:
    """按日期字段过滤（created_at / updated_at / due_date）；空日期在设置范围时被排除。"""
    if df.empty or (start is None and end is None):
        return df
    d = pd.to_datetime(df[field], errors="coerce").dt.date
    m = d.notna()
    if start:
        m &= d >= start
    if end:
        m &= d <= end
    return df[m]
