"""通用小工具：空值判断、文本清洗、负责人拆分等。"""
from __future__ import annotations

import math
import re
from typing import Any

# 负责人原文中的分隔符：+ / 、 , ， ; ；和 “和”
_PERSON_SPLIT = re.compile(r"\s*(?:\+|/|、|,|，|;|；|＋|／)\s*")


def is_blank(v: Any) -> bool:
    """None、空字符串、纯空白、NaN、'nan'/'None' 字符串都视为空。"""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        import pandas as pd  # 局部导入，避免工具模块强依赖
        if v is pd.NaT:
            return True
    except Exception:  # pragma: no cover
        pass
    if isinstance(v, str):
        s = v.strip()
        return s == "" or s.lower() in {"nan", "none", "nat", "null"}
    return False


def clean_text(v: Any) -> str | None:
    """把单元格值转为去首尾空白的字符串；空值返回 None（绝不返回 'nan'）。"""
    if is_blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


def split_people(text: str | None) -> list[str]:
    """把负责人原文拆成人员列表，例如 '张三+李四' -> ['张三', '李四']。"""
    if is_blank(text):
        return []
    seen, out = set(), []
    for p in _PERSON_SPLIT.split(str(text)):
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


# 不用 \b：中文字符在 Python 中属于“单词字符”，“D02审计”中 D02 与“审”之间没有 \b 边界
_TASK_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3}\d{2,3})(?![0-9])")


def find_task_code_refs(text: str | None) -> list[str]:
    """从文本中找出形如 D04、R09、F01 的任务编号引用。"""
    if is_blank(text):
        return []
    return list(dict.fromkeys(_TASK_CODE_RE.findall(str(text))))


def chapter_sort_key(name: str) -> tuple[int, int, str]:
    """章节排序键：全篇 → 第1章… → 补充材料 → 其他。仅用于初始排序。"""
    s = (name or "").strip()
    if s == "全篇":
        return (0, 0, s)
    m = re.match(r"第\s*(\d+)(?:\.(\d+))?", s)
    if m:
        return (10 + int(m.group(1)), int(m.group(2) or 0), s)
    if "补充" in s:
        return (900, 0, s)
    return (950, 0, s)


def chapter_group(name: str) -> str:
    """推断章节所属的“章”，用于按章汇总：'第6.1节' -> '第6章'；其余保持原文。"""
    s = (name or "").strip()
    m = re.match(r"^第\s*(\d+)\.\d+\s*节$", s)
    if m:
        return f"第{m.group(1)}章"
    return s
