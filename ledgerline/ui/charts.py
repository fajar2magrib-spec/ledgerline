"""Plotly charts (transparent, theme-agnostic, currency-aware)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ..formatting import fdate, prefix
from ..i18n import t

CHART_HEIGHT = 340
PALETTE = ["#0F766E", "#2563EB", "#D97706", "#9333EA", "#E11D48", "#0891B2"]
SERIES = {"Revenue": "#2563EB", "Profit": "#0F9D74"}
FILLS = {"Revenue": "rgba(37,99,235,0.14)", "Profit": "rgba(15,157,116,0.18)"}
STATUS_COLORS = {"Paid": "#0F9D74", "Partially Paid": "#3B82F6", "Pending": "#E0A100",
                 "Overdue": "#E5484D", "Cancelled": "#8B95A5"}
STATUS_ORDER = ["Paid", "Partially Paid", "Pending", "Overdue", "Cancelled"]


def _style(fig: go.Figure, height: int = CHART_HEIGHT, bottom: int = 8) -> go.Figure:
    grid = "rgba(128,128,128,0.18)"
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=bottom), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Plus Jakarta Sans, system-ui, sans-serif", size=12),
                      hoverlabel=dict(font_size=13, bordercolor="rgba(0,0,0,0)"))
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, title=None)
    fig.update_yaxes(gridcolor=grid, zeroline=False, showline=False, title=None)
    return fig


def trend_chart(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, currency: str) -> go.Figure:
    pre, monthly = prefix(currency), (end - start).days > 62
    series = df.groupby(pd.Grouper(key="issue_date", freq="MS" if monthly else "D"))[["revenue", "profit"]].sum().reset_index()
    long = series.melt(id_vars="issue_date", var_name="Metric", value_name="Amount")
    long["Metric"] = long["Metric"].str.title()
    fig = px.line(long, x="issue_date", y="Amount", color="Metric", color_discrete_map=SERIES, markers=True,
                  category_orders={"Metric": ["Revenue", "Profit"]})
    for trace in fig.data:
        trace.update(fill="tozeroy", fillcolor=FILLS[trace.name], line=dict(width=2.6, shape="spline", smoothing=0.6),
                     marker=dict(size=8, line=dict(width=2, color="white")),
                     hovertemplate=f"{trace.name}: {pre}%{{y:,.0f}}<extra></extra>")
    for trace in fig.data:
        trace.update(name=t(trace.name))          # legend and hover text in the chosen language
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None))
    days = sorted(series["issue_date"].unique())                     # month/day names in the chosen language
    step = max(1, len(days) // 10)
    fig.update_xaxes(tickmode="array", tickvals=days[::step],
                     ticktext=[fdate(pd.Timestamp(d), "%b %Y" if monthly else "%b %d") for d in days[::step]],
                     hoverformat="%Y-%m" if monthly else "%Y-%m-%d")
    fig.update_yaxes(tickprefix=pre, tickformat="~s")
    if not monthly:
        # One tick per day (no duplicated "Oct 05, Oct 05" labels) and breathing room around a lone point.
        fig.update_xaxes(dtick=None)
        if series["issue_date"].nunique() == 1:
            day = series["issue_date"].iloc[0]
            fig.update_xaxes(range=[day - pd.Timedelta(days=1), day + pd.Timedelta(days=1)])
    return _style(fig)


def donut_chart(df: pd.DataFrame, dimension: str, currency: str) -> go.Figure:
    pre = prefix(currency)
    col = "category" if dimension == "Category" else "payment_method"
    grouped = df.groupby(col, as_index=False)["revenue"].sum()
    grouped = grouped[grouped["revenue"] > 0].sort_values("revenue", ascending=False)
    fig = px.pie(grouped, names=col, values="revenue", hole=0.64, color_discrete_sequence=PALETTE)
    fig.update_traces(sort=False, textinfo="percent", textfont_size=12, marker=dict(line=dict(color="rgba(0,0,0,0)", width=2)),
                      hovertemplate=f"<b>%{{label}}</b><br>{pre}%{{value:,.0f}}<br>%{{percent}}<extra></extra>")
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.02, x=0.5, xanchor="center"),
                      annotations=[dict(text=f"<b>{pre}{grouped['revenue'].sum():,.0f}</b>", x=0.5, y=0.5,
                                        showarrow=False, font=dict(size=20))])
    return _style(fig, bottom=64)


def status_chart(df: pd.DataFrame, currency: str) -> go.Figure:
    pre = prefix(currency)
    grouped = (df.groupby("display_status").agg(amount=("total", "sum"), count=("id", "count"))
               .reindex(STATUS_ORDER, fill_value=0).reset_index())
    grouped["label"] = grouped["amount"].map(lambda v: f"{pre}{v:,.0f}")
    fig = px.bar(grouped, x="amount", y="display_status", orientation="h", color="display_status",
                 color_discrete_map=STATUS_COLORS, text="label", custom_data=["count"])
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate=f"<b>%{{y}}</b><br>{pre}%{{x:,.0f}}<br>%{{customdata[0]}} " + t("invoices") + "<extra></extra>")
    fig.update_layout(showlegend=False, bargap=0.35)
    fig.update_yaxes(categoryorder="array", categoryarray=STATUS_ORDER[::-1], showgrid=False,
                     tickmode="array", tickvals=STATUS_ORDER, ticktext=[t(s) for s in STATUS_ORDER])
    fig.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,0.18)", tickprefix=pre, tickformat="~s",
                     range=[0, max(float(grouped["amount"].max()) * 1.35, 1.0)])
    return _style(fig)


AGING_COLORS = ["#0F9D74", "#E0A100", "#F08C00", "#E5484D", "#9F1239"]


def bar_chart(labels, values, currency: str, colors=None, horizontal: bool = False, height: int = 300) -> go.Figure:
    """Simple currency bar chart with value labels (used by the Reports and Payments pages)."""
    pre = prefix(currency)
    labels, values = list(labels), [float(v) for v in values]
    text = [f"{pre}{v:,.0f}" for v in values]
    marker = dict(color=colors or "#2563EB")
    if horizontal:
        fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker=marker, text=text, textposition="outside",
                               cliponaxis=False, hovertemplate=pre + "%{x:,.2f}<extra>%{y}</extra>"))
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(showticklabels=False)
    else:
        fig = go.Figure(go.Bar(x=labels, y=values, marker=marker, text=text, textposition="outside",
                               cliponaxis=False, hovertemplate="%{x}<br>" + pre + "%{y:,.2f}<extra></extra>"))
        fig.update_yaxes(tickprefix=pre, tickformat="~s")
    fig.update_layout(showlegend=False, uniformtext_minsize=10)
    _style(fig, height=height, bottom=8)
    if horizontal:
        fig.update_layout(margin=dict(l=8, r=72, t=8, b=8))  # room for the value label
        top = max(values) if values else 0
        fig.update_xaxes(range=[0, max(top * 1.25, 1.0)])
    return fig


def aging_chart(summary: pd.DataFrame, currency: str) -> go.Figure:
    return bar_chart(summary["bucket"], summary["balance"], currency, colors=AGING_COLORS, height=300)


def collections_chart(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, currency: str) -> go.Figure:
    """Money received per day (short ranges) or per month (long ranges)."""
    monthly = (end - start).days > 62
    series = df.groupby(pd.Grouper(key="paid_on", freq="MS" if monthly else "D"))["amount"].sum().reset_index()
    series = series[series["amount"] > 0]
    labels = series["paid_on"].map(lambda d: fdate(d, "%b %Y" if monthly else "%b %d"))
    return bar_chart(labels, series["amount"], currency, colors="#0F9D74", height=300)


def apply_theme(accent: str) -> None:
    """Colour the main series with the company accent colour (Customize -> Appearance)."""
    hex_ = accent.lstrip("#")
    r, g, b = (int(hex_[i:i + 2], 16) for i in (0, 2, 4))
    SERIES["Revenue"] = accent
    FILLS["Revenue"] = f"rgba({r},{g},{b},0.14)"
    PALETTE[1] = accent
