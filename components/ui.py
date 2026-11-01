"""界面公共工具：样式、提示消息、安全写入、空状态、标识。"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

import streamlit as st

from core.database import DatabaseError, transaction
from utils.formatters import badge_html, progress_bar_html
from utils.validators import ValidationError

CSS = """
<style>
:root { --rpm-border:#E5E7EB; --rpm-muted:#6B7280; --rpm-text:#1F2937;
  --mc-pink:#FDE6EE; --mc-peach:#FFEBDD; --mc-lemon:#FFF7D6; --mc-mint:#DFF5EA; --mc-sky:#DDEFFB; --mc-lilac:#EBE5FA;
  --mc-pink-d:#F4A9C0; --mc-peach-d:#F9BE98; --mc-lemon-d:#F2D675; --mc-mint-d:#93D8B5; --mc-sky-d:#8CC6EC; --mc-lilac-d:#B9A8EC; }
[data-testid="stAppViewContainer"], [data-testid="stApp"] { background:#FBF9FF; }
[data-testid="stHeader"] { background:transparent; }
.block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1500px; }
h1, h2, h3 { color: var(--rpm-text); letter-spacing: 0; }
h1 { font-size: 1.55rem !important; font-weight: 650 !important; }
h2 { font-size: 1.2rem !important; font-weight: 600 !important; margin-top: .4rem; }
h3 { font-size: 1.02rem !important; font-weight: 600 !important; }
h3 { border-left:4px solid var(--mc-lilac-d); padding-left:8px; }
div[data-testid="stMetric"] { border:1px solid transparent; border-radius:12px; padding:10px 14px; background:var(--mc-sky); }
div[data-testid="stHorizontalBlock"] > div:nth-child(5n+1) div[data-testid="stMetric"] { background:var(--mc-pink); }
div[data-testid="stHorizontalBlock"] > div:nth-child(5n+2) div[data-testid="stMetric"] { background:var(--mc-peach); }
div[data-testid="stHorizontalBlock"] > div:nth-child(5n+3) div[data-testid="stMetric"] { background:var(--mc-mint); }
div[data-testid="stHorizontalBlock"] > div:nth-child(5n+4) div[data-testid="stMetric"] { background:var(--mc-sky); }
div[data-testid="stHorizontalBlock"] > div:nth-child(5n+5) div[data-testid="stMetric"] { background:var(--mc-lilac); }
/* 总览页分区块（container key = blk_*） */
[class*="st-key-blk_"] { border-radius:16px; padding:14px 16px 10px; border:1px solid rgba(0,0,0,.04); }
.st-key-blk_modules { background:var(--mc-mint); }
.st-key-blk_chapters { background:var(--mc-lilac); }
.st-key-blk_up { background:var(--mc-peach); }
.st-key-blk_od { background:var(--mc-pink); }
.st-key-blk_bl { background:var(--mc-lemon); }
.st-key-blk_d1 { background:var(--mc-sky); }
.st-key-blk_d2 { background:var(--mc-pink); }
.st-key-blk_d3 { background:var(--mc-mint); }
/* 章节行：马卡龙循环 */
[class*="st-key-chc"] { border-radius:12px; padding:8px 12px; margin-bottom:6px; background:#FFFFFFB3; border-left:5px solid var(--mc-pink-d); }
[class*="st-key-chc1_"] { border-left-color:var(--mc-peach-d); }
[class*="st-key-chc2_"] { border-left-color:var(--mc-lemon-d); }
[class*="st-key-chc3_"] { border-left-color:var(--mc-mint-d); }
[class*="st-key-chc4_"] { border-left-color:var(--mc-sky-d); }
[class*="st-key-chc5_"] { border-left-color:var(--mc-lilac-d); }
div[data-testid="stExpander"] details { background:#FFFFFFB3; border-radius:12px; border-color:var(--mc-lilac); }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius:12px; }
div[data-testid="stMetricLabel"] p { color: var(--rpm-muted); font-size: 13px; }
div[data-testid="stMetricValue"] { font-size: 1.55rem; }
section[data-testid="stSidebar"] { background:#F3EEFC; border-right:1px solid #E4DBF7; }
.rpm-muted { color: var(--rpm-muted); font-size: 13px; }
.rpm-row { padding: 4px 0; border-bottom: 1px solid #F3F4F6; }
.rpm-empty { color: var(--rpm-muted); padding: 18px; border:1px dashed #D1D5DB; border-radius:4px; text-align:center; font-size:14px; }
.rpm-cal-day { border:1px solid var(--rpm-border); border-radius:3px; min-height:92px; padding:4px 6px; background:#FFFFFF; }
.rpm-cal-today { border-color:#2563EB; box-shadow: inset 0 0 0 1px #2563EB; }
.rpm-cal-num { font-size:12px; color:var(--rpm-muted); }
.rpm-label { color: var(--rpm-muted); font-size: 12px; margin-bottom: 0; }
.rpm-value { font-size: 14px; margin-bottom: 6px; white-space: pre-wrap; }
button[kind="tertiary"] { padding: 0 2px !important; min-height: 0 !important; text-align:left; }
/* 文件上传控件中文化（Streamlit 内置文字为英文） */
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p { font-size: 0; }
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after { content: "选择文件"; font-size: 14px; }
[data-testid="stFileUploaderDropzoneInstructions"] span { font-size: 0; }
[data-testid="stFileUploaderDropzoneInstructions"] span::after { content: "拖放文件到此处，或点击“选择文件”（单个文件不超过 200MB）"; font-size: 13px; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def flash(msg: str, kind: str = "success") -> None:
    """记录一条提示，在下一次刷新后显示（用于 st.rerun 之后）。"""
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash() -> None:
    for kind, msg in st.session_state.pop("_flash", []):
        icon = {"success": "✅", "warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(kind, "")
        st.toast(msg, icon=icon)


def safe_write(fn: Callable[[sqlite3.Connection], Any], success: str | None = None,
               rerun: bool = True, where=None):
    """在事务中执行写操作；失败时显示中文错误并保持原数据不变。"""
    try:
        with transaction() as conn:
            result = fn(conn)
    except (ValidationError, DatabaseError, ValueError) as e:
        (where or st).error(f"操作未完成：{e}")
        return None
    except sqlite3.Error as e:
        (where or st).error(f"数据库操作失败（已回滚，数据未改变）：{e}")
        return None
    if success:
        flash(success)
    if rerun:
        st.rerun()
    return result


def empty_state(text: str) -> None:
    st.markdown(f"<div class='rpm-empty'>{text}</div>", unsafe_allow_html=True)


def badge(text: str | None, color: str | None) -> str:
    return badge_html(text or "—", color or "#9CA3AF") if text else "<span class='rpm-muted'>—</span>"


def progress_html(p: float, color: str = "#2563EB") -> str:
    return progress_bar_html(p, color)


def field(label: str, value: Any) -> None:
    v = "—" if value in (None, "", []) else value
    st.markdown(f"<div class='rpm-label'>{label}</div><div class='rpm-value'>{v}</div>",
                unsafe_allow_html=True)
