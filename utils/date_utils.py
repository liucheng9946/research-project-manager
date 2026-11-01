"""日期解析与计算。数据库统一存储 ISO 格式 YYYY-MM-DD；空日期保持为 None。"""
from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, timedelta
from typing import Any

from utils.helpers import is_blank

_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d",
            "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y")


class DateParseError(ValueError):
    pass


def parse_date(v: Any) -> str | None:
    """把各种日期输入转为 'YYYY-MM-DD'；空值返回 None；无法识别时抛出 DateParseError。"""
    if is_blank(v):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "to_pydatetime"):  # pandas.Timestamp
        return v.to_pydatetime().date().isoformat()
    if isinstance(v, (int, float)):
        # Excel 序列日期（1900 系统）
        if 20000 < float(v) < 80000:
            return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
        raise DateParseError(f"无法识别的日期数值：{v}")
    s = str(v).strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise DateParseError(f"无法识别的日期：“{s}”，请使用 YYYY-MM-DD 格式")


def to_date(s: str | None) -> date | None:
    if is_blank(s):
        return None
    return date.fromisoformat(str(s)[:10])


def today() -> date:
    return date.today()


def is_overdue(due: str | None, is_done: bool, ref: date | None = None) -> bool:
    d = to_date(due)
    if d is None or is_done:
        return False
    return d < (ref or today())


def days_until(due: str | None, ref: date | None = None) -> int | None:
    d = to_date(due)
    if d is None:
        return None
    return (d - (ref or today())).days


def month_matrix(year: int, month: int, week_start: int = 0) -> list[list[date | None]]:
    """返回某月的周矩阵（每周 7 天，不属于本月的位置为 None）。"""
    cal = _cal.Calendar(firstweekday=week_start)
    weeks = []
    for wk in cal.monthdatescalendar(year, month):
        weeks.append([d if d.month == month else None for d in wk])
    return weeks


def week_dates(ref: date, week_start: int = 0) -> list[date]:
    start = ref - timedelta(days=(ref.weekday() - week_start) % 7)
    return [start + timedelta(days=i) for i in range(7)]


WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def weekday_headers(week_start: int = 0) -> list[str]:
    return [WEEKDAY_NAMES[(week_start + i) % 7] for i in range(7)]
