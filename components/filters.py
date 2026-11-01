"""任务搜索、组合筛选与排序。筛选条件保存在以项目 ID 区分的 session_state 键中，
切换项目后不会把上一项目的条件带过来；同一项目内交互刷新不会丢失条件。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

SORT_OPTIONS = {
    "priority": "优先级",
    "due_date": "截止日期",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "progress": "完成度",
    "custom": "自定义顺序（树形）",
    "task_code": "编号",
}

_MULTI = [  # (键, 标签, df 列)
    ("priority", "优先级", "priority"),
    ("chapter", "所属章节", "chapter"),
    ("category", "工作类别", "category"),
    ("status", "完成状态", "status"),
]
NONE_LABEL = "（未设置）"


def _k(pid: str, scope: str, name: str) -> str:
    return f"flt_{pid}_{scope}_{name}"


def _options(df: pd.DataFrame, col: str, ordered: list[str] | None = None) -> list[str]:
    vals = list(dict.fromkeys([v for v in (ordered or []) if v]))
    present = df[col].dropna().unique().tolist() if not df.empty else []
    vals += [v for v in sorted(present) if v not in vals]
    if not df.empty and df[col].isna().any():
        vals.append(NONE_LABEL)
    return vals


def active_count(pid: str, scope: str) -> int:
    n = 0
    for key, _, _ in _MULTI + [("people", "", ""), ("tags", "", "")]:
        if st.session_state.get(_k(pid, scope, key)):
            n += 1
    for key in ("overdue", "children", "due_mode"):
        if st.session_state.get(_k(pid, scope, key), "全部") != "全部":
            n += 1
    return n


def render_filters(df: pd.DataFrame, pid: str, scope: str, lookups: dict,
                   default_sort: str = "priority", show_sort: bool = True) -> pd.DataFrame:
    """渲染搜索框 + 高级筛选 + 排序，返回筛选并排序后的 DataFrame。"""
    c1, c2, c3, c4 = st.columns([3.2, 1.3, 1.6, 0.9]) if show_sort else (*st.columns([4, 1.3]), None, None)
    q = c1.text_input("搜索", key=_k(pid, scope, "q"), placeholder="编号、名称、描述、负责人、备注…",
                      label_visibility="collapsed")
    n_active = active_count(pid, scope)
    with c2.popover(f"高级筛选{f'（{n_active}）' if n_active else ''}", width="stretch"):
        cols = st.columns(2)
        for i, (key, label, col) in enumerate(_MULTI):
            cols[i % 2].multiselect(label, _options(df, col, lookups.get(key)), placeholder="请选择（可多选）", key=_k(pid, scope, key))
        all_people = sorted({p for ps in df["people"] for p in ps}) if not df.empty else []
        cols[0].multiselect("负责人", all_people, placeholder="请选择（可多选）", key=_k(pid, scope, "people"))
        all_tags = list(dict.fromkeys((lookups.get("tags") or []) +
                                      (sorted({t for ts in df["tags"] for t in ts}) if not df.empty else [])))
        cols[1].multiselect("标签", all_tags, placeholder="请选择（可多选）", key=_k(pid, scope, "tags"))
        cols[0].selectbox("是否逾期", ["全部", "仅逾期", "未逾期"], key=_k(pid, scope, "overdue"))
        cols[1].selectbox("是否有子任务", ["全部", "有子任务", "无子任务"], key=_k(pid, scope, "children"))
        mode = cols[0].selectbox("截止日期", ["全部", "指定范围", "无截止日期", "有截止日期"],
                                 key=_k(pid, scope, "due_mode"))
        if mode == "指定范围":
            cols[1].date_input("日期范围", value=(date.today(), date.today()),
                               key=_k(pid, scope, "due_range"))
        if st.button("清除全部筛选", key=_k(pid, scope, "clear")):
            for k in list(st.session_state.keys()):
                if k.startswith(f"flt_{pid}_{scope}_") and not k.endswith(("_sort", "_desc")):
                    del st.session_state[k]
            st.rerun()
    if show_sort:
        sk = _k(pid, scope, "sort")
        if sk not in st.session_state:
            st.session_state[sk] = default_sort if default_sort in SORT_OPTIONS else "priority"
        c3.selectbox("排序", list(SORT_OPTIONS), format_func=lambda x: f"按{SORT_OPTIONS[x]}",
                     key=sk, label_visibility="collapsed")
        c4.toggle("降序", key=_k(pid, scope, "desc"))
    return apply_filters(df, pid, scope)


def apply_filters(df: pd.DataFrame, pid: str, scope: str) -> pd.DataFrame:
    if df.empty:
        return df
    g = lambda name, default=None: st.session_state.get(_k(pid, scope, name), default)  # noqa: E731
    m = pd.Series(True, index=df.index)
    q = (g("q") or "").strip().lower()
    if q:
        hay = (df["task_code"].fillna("") + " " + df["title"].fillna("") + " " +
               df["description"].fillna("") + " " + df["assignee"].fillna("") + " " +
               df["notes"].fillna("") + " " + df["deliverable"].fillna("")).str.lower()
        for word in q.split():
            m &= hay.str.contains(word, regex=False)
    for key, _, col in _MULTI:
        sel = g(key)
        if sel:
            vals = df[col].fillna(NONE_LABEL)
            m &= vals.isin(sel)
    if g("people"):
        sel = set(g("people"))
        m &= df["people"].map(lambda ps: bool(sel & set(ps)))
    if g("tags"):
        sel = set(g("tags"))
        m &= df["tags"].map(lambda ts: bool(sel & set(ts)))
    ov = g("overdue", "全部")
    if ov == "仅逾期":
        m &= df["overdue"]
    elif ov == "未逾期":
        m &= ~df["overdue"]
    ch = g("children", "全部")
    if ch == "有子任务":
        m &= df["has_children"]
    elif ch == "无子任务":
        m &= ~df["has_children"]
    dm = g("due_mode", "全部")
    if dm == "无截止日期":
        m &= df["due_date"].isna()
    elif dm == "有截止日期":
        m &= df["due_date"].notna()
    elif dm == "指定范围":
        rng = g("due_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            d = pd.to_datetime(df["due_date"], errors="coerce").dt.date
            m &= d.notna() & (d >= rng[0]) & (d <= rng[1])
    out = df[m]
    return sort_df(out, g("sort", "priority"), bool(g("desc", False)))


def sort_df(df: pd.DataFrame, key: str, desc: bool = False) -> pd.DataFrame:
    if df.empty:
        return df
    if key == "custom":
        return df.sort_values("tree_index", ascending=not desc)
    if key == "priority":
        return df.sort_values(["priority_order", "task_code", "title"], ascending=not desc,
                              na_position="last")
    if key == "due_date":
        return df.sort_values(["due_date", "priority_order"], ascending=[not desc, True], na_position="last")
    if key in ("created_at", "updated_at", "progress", "task_code"):
        return df.sort_values([key, "title"], ascending=[not desc, True], na_position="last")
    return df
