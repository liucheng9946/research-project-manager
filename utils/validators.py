"""输入校验：失败时抛出 ValidationError（中文信息）。"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from utils.helpers import is_blank


class ValidationError(ValueError):
    pass


_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def require_text(v: Any, field: str, max_len: int = 500) -> str:
    if is_blank(v):
        raise ValidationError(f"{field}不能为空")
    s = str(v).strip()
    if len(s) > max_len:
        raise ValidationError(f"{field}过长（最多 {max_len} 个字符）")
    return s


def optional_text(v: Any, max_len: int = 20000) -> str | None:
    if is_blank(v):
        return None
    s = str(v).strip()
    if len(s) > max_len:
        raise ValidationError(f"文本过长（最多 {max_len} 个字符）")
    return s


def validate_progress(v: Any) -> float:
    if is_blank(v):
        return 0.0
    try:
        p = float(v)
    except (TypeError, ValueError):
        raise ValidationError(f"完成度必须是 0–100 之间的数字，当前为“{v}”") from None
    if not 0 <= p <= 100:
        raise ValidationError(f"完成度必须在 0–100 之间，当前为 {p:g}")
    return round(p, 2)


def validate_weight(v: Any) -> float:
    if is_blank(v):
        return 1.0
    try:
        w = float(v)
    except (TypeError, ValueError):
        raise ValidationError(f"工作量权重必须是正数，当前为“{v}”") from None
    if w <= 0:
        raise ValidationError("工作量权重必须大于 0")
    return w


def validate_color(v: Any, default: str = "#6B7280") -> str:
    if is_blank(v):
        return default
    s = str(v).strip()
    if not _COLOR_RE.match(s):
        raise ValidationError(f"颜色格式应为 #RRGGBB，当前为“{s}”")
    return s.upper()


_DOI_RE = re.compile(r"^(?:doi:\s*)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)


def normalize_url(v: Any) -> str:
    """校验并规范化网页链接；裸 DOI（10.xxxx/...）自动转为 https://doi.org/...。"""
    if is_blank(v):
        raise ValidationError("链接不能为空")
    s = str(v).strip()
    m = _DOI_RE.match(s)
    if m:
        return "https://doi.org/" + m.group(1)
    if s.lower().startswith("www."):
        s = "https://" + s
    p = urlparse(s)
    if p.scheme.lower() not in ("http", "https"):
        raise ValidationError("链接必须以 http:// 或 https:// 开头（或填写 DOI，如 10.1000/xyz）")
    host = p.hostname or ""
    if not host or (("." not in host) and host != "localhost"):
        raise ValidationError(f"链接的域名无效：“{s}”")
    if any(c.isspace() for c in s):
        raise ValidationError("链接中不能包含空格")
    return s
