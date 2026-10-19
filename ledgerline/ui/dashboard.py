"""Dashboard page: filters, KPI cards, charts, recent invoices and the actions panel.

Performance decisions
* The whole body is an ``st.fragment``: touching a filter reruns only this fragment, not
  authentication, the sidebar or the page shell, so there is no full-page flicker.
* The invoice frame is cached with ``st.cache_data`` and keyed by ``data_stamp()``; changing
  filters is pure in-memory pandas work and never hits the database.
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from ..formatting import fdate
from ..i18n import t
from ..calc import DISPLAY_STATUSES
from ..services import analytics
from ..services.analytics import Filters
from . import actions, charts, safe
from .components import (PAGE_INVOICE, can, card_header, empty_card, empty_state, html_table, kpi_card, money_html,
                         settings, stamp, status_pill, who_cell)

from .state import DEFAULT_PERIOD, PERIODS
TABLE_ROWS = 50


@st.cache_data(show_spinner=False)
def cached_frame(today_iso: str, stamp: tuple) -> pd.DataFrame:
    """Every invoice as a DataFrame. Keyed by the data stamp, so it is reloaded only when
    something actually changed; filter changes never reach the database."""
    return analytics.load_invoice_frame(date.fromisoformat(today_iso))


def _trend(change):
    if change is None:
        return t("No prior data"), "neutral"
    return f"{'▲' if change >= 0 else '▼'} {abs(change):.1f}%", "up" if change >= 0 else "down"


def _filters(df: pd.DataFrame, today: date) -> Filters:
    st.session_state.setdefault("period", DEFAULT_PERIOD)
    st.session_state.setdefault("custom_range", (today - timedelta(days=29), today))
    st.session_state.setdefault("statuses", list(DISPLAY_STATUSES))
    data_min = df["issue_date"].min().date() if not df.empty else today
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.1, 1.5, 2.4], gap="medium")
        period = c1.selectbox(t("Period"), PERIODS, key="period", format_func=t)
        custom = c2.date_input(t("Custom range"), min_value=min(data_min, today - timedelta(days=5 * 365)), max_value=today,
                               key="custom_range", disabled=period != "Custom Date Range")
        query = c3.text_input(t("Search"), placeholder=t("Client name or invoice number"), key="query")
        # Status chips get a full-width row so every option stays readable (a multiselect
        # squeezed into one column cut off "Overdue").
        if hasattr(st, "pills"):
            statuses = st.pills(t("Status"), list(DISPLAY_STATUSES), selection_mode="multi", key="statuses", format_func=t) or []
        else:  # very old Streamlit
            statuses = st.multiselect(t("Status"), list(DISPLAY_STATUSES), key="statuses", format_func=t)
    start, end = analytics.resolve_period(period, custom, data_min, today)
    return Filters(start, end, tuple(statuses) or tuple(DISPLAY_STATUSES), query, period)  # none selected = all


def _kpis(cur: analytics.Kpis, prev: analytics.Kpis) -> None:
    rev_t, rev_c = _trend(analytics.pct_change(cur.revenue, prev.revenue))
    pro_t, pro_c = _trend(analytics.pct_change(cur.profit, prev.profit))
    ord_t, ord_c = _trend(analytics.pct_change(cur.orders, prev.orders))
    growth = analytics.pct_change(cur.revenue, prev.revenue)
    overdue = cur.overdue_count > 0
    cards = [
        kpi_card(t("Total revenue"), money_html(cur.revenue), "💰", "#2563EB", rev_t, rev_c, t("excl. tax")),
        kpi_card(t("Net profit"), money_html(cur.profit), "📈", "#0F9D74", pro_t, pro_c, t("{margin:.1f}% margin", margin=cur.margin)),
        kpi_card(t("Total orders"), f"{cur.orders:,}", "🧾", "#0891B2", ord_t, ord_c, t("{paid_count:,} paid", paid_count=cur.paid_count)),
        kpi_card(t("Outstanding dues"), money_html(cur.outstanding), "⏳", "#E5484D" if overdue else "#E0A100",
                 t("{n} overdue", n=cur.overdue_count) if overdue else t("No overdue"), "down" if overdue else "up",
                 t("{open_count} open", open_count=cur.open_count)),
        kpi_card(t("Revenue growth"), t("N/A") if growth is None else f"{growth:+.1f}%", "🚀",
                 "#8B95A5" if growth is None else ("#0F9D74" if growth >= 0 else "#E5484D"),
                 t("No prior period") if growth is None else t("vs last period"), "neutral" if growth is None else ("up" if growth >= 0 else "down"),
                 t("{collection_rate:.0f}% of invoiced collected", collection_rate=cur.collection_rate)),
    ]
    for col, html in zip(st.columns(5, gap="medium"), cards):
        col.markdown(html, unsafe_allow_html=True)


def _charts(current: pd.DataFrame, flt: Filters, currency: str) -> None:
    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True):
        card_header(t("Revenue and profit trend"), t("Daily for short ranges, monthly for longer ones"))
        if current.empty:
            empty_state(t("No invoices match these filters."))
        else:
            st.plotly_chart(charts.trend_chart(current, flt.start, flt.end, currency), config={"displayModeBar": False})
    with right, st.container(border=True):
        head, toggle = st.columns([1, 1.3])
        with head:
            card_header(t("Revenue mix"), t("Share of total revenue"))
        dimension = toggle.radio(t("Group by"), ["Category", "Payment Method"], horizontal=True, format_func=t,
                                 label_visibility="collapsed", key="donut_dim")
        if current["revenue"].sum() <= 0:
            empty_state(t("No revenue to break down for this selection."))
        else:
            st.plotly_chart(charts.donut_chart(current, dimension, currency), config={"displayModeBar": False})

    left, right = st.columns([2, 3], gap="medium")
    with left, st.container(border=True):
        card_header(t("Invoice status"), t("Invoiced total by status"))
        if current.empty:
            empty_state(t("Nothing to show yet."))
        else:
            st.plotly_chart(charts.status_chart(current, currency), config={"displayModeBar": False})
    with right, st.container(border=True):
        card_header(t("Recent invoices"), t("Latest {TABLE_ROWS} matching your filters", TABLE_ROWS=TABLE_ROWS))
        recent = current.sort_values("issue_date", ascending=False).head(TABLE_ROWS)
        rows = [[f'<span class="strong">{escape(str(r.number))}</span>', who_cell(r.client),
                 f'<span class="muted">{fdate(r.issue_date, "%b %d, %Y")}</span>', f'<span class="muted">{fdate(r.due_date, "%b %d, %Y")}</span>',
                 status_pill(r.display_status), money_html(r.total, 2), money_html(r.balance, 2)]
                for r in recent.itertuples()]
        html_table([t("Invoice"), t("Client"), t("Issued"), t("Due"), t("Status"), t("Total"), t("Balance")], rows, numeric=(5, 6),
                   max_height=charts.CHART_HEIGHT - 10, empty=t("No invoices match your filters."))


def _onboarding() -> None:
    empty_card("🧾", t("No invoices yet"),
               t("Create your first invoice and your revenue, profit and outstanding dues will appear here."))
    if can("invoice.create"):
        _, middle, _ = st.columns([1, 1, 1])
        if middle.button(t("➕ Create the first invoice"), type="primary", key="first_invoice", width="stretch"):
            st.session_state["_goto"] = PAGE_INVOICE
            st.rerun()
    if can("data.import"):
        st.caption(t("Administrators can also load sample data or import v2.0 data under Administration → Data & system."))


@st.fragment
def render() -> None:
    """Only this fragment reruns when a filter changes: no full-page reload, no flicker."""
    with safe.guarded("the dashboard", "dashboard"):
        today = date.today()
        current_stamp = stamp()  # fresh on every fragment run (cached 3 s), not frozen at page load
        df = cached_frame(today.isoformat(), current_stamp)
        if df.empty:
            _onboarding()
            return
        flt = _filters(df, today)
        base = analytics.apply_filters(df, flt, with_dates=False)
        current = base[(base["issue_date"] >= flt.start) & (base["issue_date"] <= flt.end)]
        prev_start, prev_end = analytics.previous_window(flt.start, flt.end)
        previous = base[(base["issue_date"] >= prev_start) & (base["issue_date"] <= prev_end)]

        span_text = t("{start} to {end}", start=f"{fdate(flt.start, '%b %d, %Y')}", end=f"{fdate(flt.end, '%b %d, %Y')}")
        count_text = t("{n} invoices in view", n=f"{len(current):,}")
        st.markdown(f'<div class="dash-header"><div><h1 class="dash-title">{t("Business performance")}</h1>'
                    f'<div class="dash-sub">{span_text} &nbsp;|&nbsp; {count_text}'
                    f'</div></div><span class="live-pill"><span class="live-dot"></span>{t("Live data")}</span></div>',
                    unsafe_allow_html=True)
        _kpis(analytics.compute_kpis(current), analytics.compute_kpis(previous))
        _charts(current, flt, settings().currency)
        actions.render(current, current_stamp)
        from . import state
        state.sync_query_params(st.session_state.get("nav_page", ""))
