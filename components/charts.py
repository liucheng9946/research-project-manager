"""Plotly 图表（中文标题；悬停同时显示数量与百分比；无数据时显示空状态）。"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.ui import empty_state

FONT = dict(family="Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif", size=12, color="#1F2937")
GRID = "#EEF0F3"


def _layout(fig: go.Figure, title: str, height: int = 300) -> go.Figure:
    fig.update_layout(title=dict(text=title, font=dict(size=14), x=0, xanchor="left"),
                      font=FONT, height=height, margin=dict(l=10, r=20, t=42, b=10),
                      plot_bgcolor="rgba(255,255,255,0.55)", paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
                      hoverlabel=dict(font=FONT, bgcolor="white"))
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def _show(fig, key: str | None = None):
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False}, key=key)


def count_bar(data: pd.DataFrame, title: str, horizontal: bool = False, key: str | None = None,
              height: int = 300) -> None:
    """data 需包含：名称、任务数、占比、颜色。柱上标注“数量（百分比）”。"""
    if data is None or data.empty or int(data["任务数"].sum()) == 0:
        st.markdown(f"**{title}**")
        empty_state("暂无数据")
        return
    text = [f"{n}（{p:g}%）" for n, p in zip(data["任务数"], data["占比"])]
    hover = "%{customdata[0]}<br>任务数：%{customdata[1]}<br>占比：%{customdata[2]}%<extra></extra>"
    cd = data[["名称", "任务数", "占比"]].values
    if horizontal:
        d = data.iloc[::-1]
        fig = go.Figure(go.Bar(y=d["名称"], x=d["任务数"], orientation="h", marker_color=d["颜色"],
                               text=text[::-1], textposition="outside", customdata=cd[::-1],
                               hovertemplate=hover, cliponaxis=False))
        fig.update_xaxes(title="任务数", range=[0, float(d["任务数"].max()) * 1.35 + 0.5])
        height = max(height, 34 * len(d) + 70)
    else:
        fig = go.Figure(go.Bar(x=data["名称"], y=data["任务数"], marker_color=data["颜色"], text=text,
                               textposition="outside", customdata=cd, hovertemplate=hover,
                               cliponaxis=False))
        fig.update_yaxes(title="任务数", range=[0, float(data["任务数"].max()) * 1.18 + 0.5])
    _show(_layout(fig, title, height), key)


def progress_bars(data: pd.DataFrame, title: str, key: str | None = None,
                  value_col: str = "平均完成度") -> None:
    """横向进度条：显示平均完成度，悬停给出任务数/已完成。"""
    if data is None or data.empty or int(data["任务数"].sum()) == 0:
        st.markdown(f"**{title}**")
        empty_state("暂无数据")
        return
    d = data.iloc[::-1]
    cd = d[["名称", "任务数", "已完成", value_col]].values
    fig = go.Figure()
    fig.add_bar(y=d["名称"], x=[100] * len(d), orientation="h", marker_color="#EEF0F3",
                hoverinfo="skip", showlegend=False)
    fig.add_bar(y=d["名称"], x=d[value_col], orientation="h", marker_color=d.get("颜色", "#2563EB"),
                text=[f"{v:g}%　（{n} 项，完成 {c}）" for v, n, c in zip(d[value_col], d["任务数"], d["已完成"])],
                textposition="outside", customdata=cd, cliponaxis=False,
                hovertemplate="%{customdata[0]}<br>平均完成度：%{customdata[3]}%<br>"
                              "任务数：%{customdata[1]}<br>已完成：%{customdata[2]}<extra></extra>")
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(range=[0, 135], title="平均完成度（%）", tickvals=[0, 25, 50, 75, 100])
    _show(_layout(fig, title, max(220, 30 * len(d) + 80)), key)


def simple_bar(data: pd.DataFrame, x: str, y: str, title: str, color: str = "#2563EB",
               key: str | None = None, pct_col: str | None = None) -> None:
    if data is None or data.empty or data[y].sum() == 0:
        st.markdown(f"**{title}**")
        empty_state("暂无数据")
        return
    if pct_col:
        text = [f"{n}（{p:g}%）" for n, p in zip(data[y], data[pct_col])]
    else:
        text = [str(v) for v in data[y]]
    fig = go.Figure(go.Bar(x=data[x], y=data[y], marker_color=color, text=text, textposition="outside",
                           cliponaxis=False, hovertemplate="%{x}<br>" + y + "：%{y}<extra></extra>"))
    fig.update_yaxes(title=y)
    _show(_layout(fig, title), key)


def workload_bar(data: pd.DataFrame, title: str, key: str | None = None) -> None:
    if data is None or data.empty:
        st.markdown(f"**{title}**")
        empty_state("暂无数据")
        return
    d = data.iloc[::-1]
    fig = go.Figure()
    other = d["任务数"] - d["已完成"] - d["进行中"] - d["阻塞"]
    for name, vals, color in (("已完成", d["已完成"], "#16A34A"), ("进行中", d["进行中"], "#2563EB"),
                              ("阻塞", d["阻塞"], "#DC2626"), ("其他", other, "#CBD5E1")):
        fig.add_bar(y=d["负责人"], x=vals, name=name, orientation="h", marker_color=color,
                    hovertemplate="%{y}<br>" + name + "：%{x} 项<extra></extra>")
    fig.add_scatter(y=d["负责人"], x=d["任务数"], mode="text", text=[f"  共 {n} 项" for n in d["任务数"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip")
    fig.update_layout(barmode="stack", showlegend=True,
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"))
    fig.update_xaxes(title="任务数")
    _layout(fig, title, max(240, 34 * len(d) + 80)).update_layout(showlegend=True)
    _show(fig, key)


def trend_chart(data: pd.DataFrame, title: str, key: str | None = None) -> None:
    if data is None or data.empty:
        st.markdown(f"**{title}**")
        empty_state("暂无数据")
        return
    fig = go.Figure()
    fig.add_bar(x=data["日期"], y=data["新建"], name="新建", marker_color="#93C5FD",
                hovertemplate="%{x}<br>新建：%{y} 项<extra></extra>")
    fig.add_bar(x=data["日期"], y=data["完成"], name="完成", marker_color="#86EFAC",
                hovertemplate="%{x}<br>完成：%{y} 项<extra></extra>")
    fig.add_scatter(x=data["日期"], y=data["累计新建"], name="累计新建", mode="lines",
                    line=dict(color="#2563EB"), yaxis="y2",
                    hovertemplate="%{x}<br>累计新建：%{y} 项<extra></extra>")
    fig.add_scatter(x=data["日期"], y=data["累计完成"], name="累计完成", mode="lines",
                    line=dict(color="#16A34A"), yaxis="y2",
                    hovertemplate="%{x}<br>累计完成：%{y} 项<extra></extra>")
    _layout(fig, title, 340)
    fig.update_layout(barmode="group", showlegend=True,
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
                      yaxis=dict(title="当期任务数"),
                      yaxis2=dict(title="累计任务数", overlaying="y", side="right", showgrid=False))
    _show(fig, key)
