"""Payments page: every payment received, with filters, a chart and CSV export."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from ..formatting import fdate
from ..i18n import t
from ..services import reports
from . import charts
from .components import (PAGE_DASHBOARD, can, card_header, empty_card, empty_state, go_to, html_table, kpi_card, kpi_row,
                         money_html, page_header, plural, settings, stamp, who_cell)

RANGES = ("This month", "Last 30 days", "This year", "All time")
MAX_ROWS = 200


@st.cache_data(show_spinner=False)
def cached_payments(data_stamp: tuple) -> pd.DataFrame:
    """Reloaded only when the invoice/payment fingerprint changes."""
    return reports.load_payments_frame()


def _window(label: str, today: date, first: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = {"This month": today.replace(day=1), "Last 30 days": today - timedelta(days=29),
             "This year": today.replace(month=1, day=1)}.get(label, first)
    return pd.Timestamp(start), pd.Timestamp(today)


def render() -> None:
    page_header(t("Payments"), t("Money you have received, newest first"))
    df = cached_payments(stamp())
    if df.empty:
        empty_card("💳", t("No payments yet"), t("When a client pays, record it from Invoice actions on the dashboard "
                   "(or choose “Paid in full” when you create an invoice). It will show up here."))
        if st.button(t("Go to the dashboard"), key="pay_go_dash"):
            go_to(PAGE_DASHBOARD)
        return

    today = date.today()
    c1, c2, c3 = st.columns([1.2, 2, 2], gap="medium")
    label = c1.selectbox(t("Period"), RANGES, key="pay_range", format_func=t)
    methods = sorted(df["method"].unique())
    chosen = c2.multiselect(t("Method"), methods, key="pay_methods", placeholder=t("All methods"))
    query = c3.text_input(t("Search"), placeholder=t("Client, invoice number or reference"), key="pay_query")

    start, end = _window(label, today, df["paid_on"].min().date())
    view = df[(df["paid_on"] >= start) & (df["paid_on"] <= end)]
    if chosen:
        view = view[view["method"].isin(chosen)]
    if query.strip():
        q = query.strip()
        view = view[view["client"].str.contains(q, case=False, regex=False) | view["invoice"].str.contains(q, case=False, regex=False)
                    | view["reference"].str.contains(q, case=False, regex=False)]

    total, count = float(view["amount"].sum()), len(view)
    by_method = view.groupby("method")["amount"].sum().sort_values(ascending=False)
    kpi_row([
        kpi_card(t("Total received"), money_html(total, 0), "💰", "#0F9D74", plural(count, "payment"), "up", t(label).lower()),
        kpi_card(t("Average payment"), money_html(total / count if count else 0, 0), "📊", "#2563EB", t("Per payment"), "neutral", t("mean")),
        kpi_card(t("Largest payment"), money_html(view["amount"].max() if count else 0, 0), "🏅", "#7C3AED", t("Single payment"), "neutral",
                 t("biggest")),
        kpi_card(t("Top method"), escape(t(by_method.index[0])) if count else "-", "💳", "#0891B2",
                 money_html(by_method.iloc[0], 0) if count else t("No data"), "neutral", t("most used")),
    ])

    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True):
        card_header(t("Money in"), t("Daily for short ranges, monthly for longer ones"))
        if view.empty:
            empty_state(t("No payments match these filters."))
        else:
            st.plotly_chart(charts.collections_chart(view, start, end, settings().currency), config={"displayModeBar": False})
    with right, st.container(border=True):
        card_header(t("By payment method"), t("Share of money received"))
        html_table([t("Method"), t("Received"), t("Share")],
                   [[escape(t(m)), money_html(v, 0), f"{v / total * 100:.0f}%" if total else "-"] for m, v in by_method.items()],
                   numeric=(1, 2), max_height=300, empty=t("Nothing to show"))

    with st.container(border=True):
        card_header(t("All payments"), t("Showing the latest {v1:,} of {count:,}", v1=min(count, MAX_ROWS), count=count))
        html_table([t("Date"), t("Invoice"), t("Client"), t("Method"), t("Reference"), t("Amount")],
                   [[f"{fdate(r.paid_on, '%b %d, %Y')}", f'<span class="strong">{escape(r.invoice)}</span>', who_cell(r.client),
                     escape(t(r.method)), f'<span class="muted">{escape(r.reference or "-")}</span>', money_html(r.amount, 2)]
                    for r in view.head(MAX_ROWS).itertuples()], numeric=(5,), max_height=480, empty=t("No payments match these filters."))
        if can("data.export") and not view.empty:
            out = view[["paid_on", "invoice", "client", "method", "reference", "amount"]].assign(
                paid_on=lambda d: d["paid_on"].dt.strftime("%Y-%m-%d"))
            st.download_button(t("📥 Download these payments (CSV)"), data=out.to_csv(index=False).encode("utf-8"),
                               file_name=f"payments_{today:%Y-%m-%d}.csv", mime="text/csv", key="pay_csv")
