"""显示格式化。"""
from __future__ import annotations

from typing import Any

from utils.helpers import is_blank


def fmt_pct(v: Any, digits: int = 1) -> str:
    if is_blank(v):
        return "—"
    f = float(v)
    return f"{f:.0f}%" if f.is_integer() else f"{f:.{digits}f}%"


def fmt_text(v: Any, empty: str = "—") -> str:
    return empty if is_blank(v) else str(v)


def fmt_date(v: Any, empty: str = "—") -> str:
    return empty if is_blank(v) else str(v)[:10]


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def shorten(v: Any, n: int = 40) -> str:
    s = fmt_text(v, "")
    return s if len(s) <= n else s[: n - 1] + "…"


def badge_html(text: str, color: str) -> str:
    """紧凑的彩色标识（浅底色 + 彩色文字），用于优先级与状态。"""
    c = color or "#6B7280"
    return (f"<span style='display:inline-block;padding:1px 8px;border-radius:3px;"
            f"font-size:12px;line-height:18px;background:{c}1A;color:{c};"
            f"border:1px solid {c}55;white-space:nowrap'>{text}</span>")


def progress_bar_html(pct: float, color: str = "#2563EB", height: int = 8) -> str:
    p = max(0.0, min(100.0, float(pct or 0)))
    return (f"<div style='display:flex;align-items:center;gap:8px'>"
            f"<div style='flex:1;background:#E5E7EB;border-radius:2px;height:{height}px'>"
            f"<div style='width:{p}%;background:{color};height:{height}px;border-radius:2px'></div>"
            f"</div><span style='font-size:12px;color:#374151;min-width:42px;text-align:right'>"
            f"{fmt_pct(p)}</span></div>")
