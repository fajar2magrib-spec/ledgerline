"""Reports page: receivables aging, best clients, tax summary and profit by category."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from ..formatting import fdate
from ..i18n import t
from .. import reportcalc
from . import charts
from .components import (cached_expenses, can, card_header, empty_card, empty_state, html_table, kpi_card, kpi_row, money_html, page_header,
                         plural, settings, stamp, who_cell)
from .dashboard import cached_frame

PERIODS = ("All time", "This year", "Last 12 months")
BUCKET_TONE = {"Not yet due": "info", "1-30 days": "warn", "31-60 days": "warn", "61-90 days": "down", "90+ days": "down"}


def _period(df: pd.DataFrame, label: str, today: date) -> pd.DataFrame:
    if label == "This year":
        return df[df["issue_date"] >= pd.Timestamp(today.replace(month=1, day=1))]
    if label == "Last 12 months":
        return df[df["issue_date"] >= pd.Timestamp(today - timedelta(days=365))]
    return df


def _download(frame: pd.DataFrame, name: str, key: str) -> None:
    if can("data.export") and not frame.empty:
        st.download_button(t("📥 Download CSV"), data=frame.to_csv(index=False).encode("utf-8"),
                           file_name=f"{name}_{date.today():%Y-%m-%d}.csv", mime="text/csv", key=key)


def _aging(df: pd.DataFrame, today: date, currency: str) -> None:
    detail = reportcalc.aging_detail(df, today)
    if detail.empty:
        empty_card("🎉", t("Nothing is owed to you"), t("Every invoice is paid. Unpaid invoices will appear here, grouped by how late they are."))
        return
    summary = reportcalc.aging_summary(detail)
    late = detail[detail["days_overdue"] > 0]
    oldest = int(late["days_overdue"].max()) if not late.empty else 0
    kpi_row([
        kpi_card(t("Total receivable"), money_html(detail["balance"].sum(), 0), "💼", "#2563EB", plural(len(detail), "open invoice"), "neutral", t("to collect")),
        kpi_card(t("Overdue amount"), money_html(late["balance"].sum(), 0), "⚠️", "#E5484D", t("{v1} late", v1=len(late)), "down" if len(late) else "up",
                 t("past due date")),
        kpi_card(t("Oldest overdue"), t("{n} days", n=oldest) if oldest else t("None"), "⏳", "#E0A100", t("Chase this first") if oldest else t("All on time"),
                 "warn" if oldest else "up", t("since due date")),
        kpi_card(t("Not yet due"), money_html(summary.loc[0, "balance"], 0), "🟢", "#0F9D74", plural(int(summary.loc[0, "invoices"]), "invoice"), "up",
                 t("still within terms")),
    ])
    left, right = st.columns([2, 3], gap="medium")
    with left, st.container(border=True):
        card_header(t("How late is the money?"), t("Unpaid balance by days past due"))
        st.plotly_chart(charts.aging_chart(summary, currency), config={"displayModeBar": False})
    with right, st.container(border=True):
        card_header(t("Unpaid invoices"), t("Oldest first. Start at the top."))
        html_table([t("Invoice"), t("Client"), t("Due"), t("Late by"), t("Age"), t("Balance")],
                   [[f'<span class="strong">{escape(str(r.number))}</span>', who_cell(r.client), f"{fdate(r.due_date, '%b %d, %Y')}",
                     t("{n} days", n=r.days_overdue) if r.days_overdue else "-",
                     f'<span class="pill pill--{BUCKET_TONE[r.bucket]}">{escape(t(r.bucket))}</span>', money_html(r.balance, 2)]
                    for r in detail.itertuples()], numeric=(5,), max_height=300)
    _download(detail.assign(due_date=detail["due_date"].dt.strftime("%Y-%m-%d")), "receivables_aging", "rep_aging_csv")


def _clients(df: pd.DataFrame, currency: str) -> None:
    summary = reportcalc.client_summary(df)
    if summary.empty:
        empty_state(t("No sales in this period."))
        return
    top = summary.head(10)
    left, right = st.columns([2, 3], gap="medium")
    with left, st.container(border=True):
        card_header(t("Top clients"), t("By amount billed"))
        st.plotly_chart(charts.bar_chart(top["client"], top["billed"], currency, horizontal=True, height=max(240, 44 * len(top))),
                        config={"displayModeBar": False})
    with right, st.container(border=True):
        card_header(t("Client totals"), t("{v1:,} clients", v1=len(summary)))
        html_table([t("Client"), t("Invoices"), t("Billed"), t("Collected"), t("Outstanding")],
                   [[who_cell(r.client), str(r.invoices), money_html(r.billed, 0), money_html(r.collected, 0), money_html(r.outstanding, 0)]
                    for r in summary.itertuples()], numeric=(1, 2, 3, 4), max_height=420)
    _download(summary.assign(last_invoice=summary["last_invoice"].dt.strftime("%Y-%m-%d")), "clients", "rep_clients_csv")


def _tax(df: pd.DataFrame) -> None:
    tax_label = settings().tax_label
    tax = reportcalc.monthly_tax(df)
    if tax.empty:
        empty_state(t("No invoices in this period."))
        return
    kpi_row([
        kpi_card(t("{tax_label} collected", tax_label=tax_label), money_html(tax["tax"].sum(), 0), "🧾", "#7C3AED", plural(len(tax), "month"), "neutral", t("charged on invoices")),
        kpi_card(t("Taxable sales"), money_html(tax["taxable"].sum(), 0), "🏷️", "#2563EB", t("After discounts"), "neutral", t("before tax")),
        kpi_card(t("Invoiced total"), money_html(tax["total"].sum(), 0), "💼", "#0F9D74", plural(int(tax["invoices"].sum()), "invoice"), "neutral", t("incl. tax")),
    ])
    with st.container(border=True):
        card_header(t("{tax_label} by month", tax_label=tax_label), t("Based on the invoice issue date. Cancelled invoices are excluded."))
        html_table([t("Month"), t("Invoices"), t("Taxable sales"), tax_label, t("Invoice total")],
                   [[f"{fdate(r.month, '%B %Y')}", str(r.invoices), money_html(r.taxable, 2), money_html(r.tax, 2), money_html(r.total, 2)]
                    for r in tax.itertuples()], numeric=(1, 2, 3, 4), max_height=420)
    _download(tax.assign(month=tax["month"].dt.strftime("%Y-%m")), "tax_summary", "rep_tax_csv")


def _categories(df: pd.DataFrame, currency: str) -> None:
    cats = reportcalc.category_summary(df)
    if cats.empty:
        empty_state(t("No sales in this period."))
        return
    left, right = st.columns([2, 3], gap="medium")
    with left, st.container(border=True):
        card_header(t("Profit by category"), t("Revenue minus your internal expenses"))
        st.plotly_chart(charts.bar_chart(cats["category"], cats["profit"], currency, horizontal=True, height=max(240, 44 * len(cats))),
                        config={"displayModeBar": False})
    with right, st.container(border=True):
        card_header(t("Category performance"), t("Which products and services make you money"))
        html_table([t("Category"), t("Invoices"), t("Revenue"), t("Expenses"), t("Profit"), t("Margin")],
                   [[f'<span class="strong">{escape(str(r.category))}</span>', str(r.invoices), money_html(r.revenue, 0),
                     money_html(r.expenses, 0), money_html(r.profit, 0),
                     f'<span class="pill pill--{"up" if r.margin >= 50 else ("warn" if r.margin >= 20 else "down")}">{r.margin:.0f}%</span>']
                    for r in cats.itertuples()], numeric=(1, 2, 3, 4, 5), max_height=380)
    _download(cats, "profit_by_category", "rep_cat_csv")


def _pnl(df: pd.DataFrame, exp: pd.DataFrame, currency: str) -> None:
    pl = reportcalc.profit_and_loss(df, exp)
    if pl.empty:
        empty_state(t("No sales or expenses in this period."))
        return
    net = float(pl["net_profit"].sum())
    kpi_row([
        kpi_card(t("Revenue"), money_html(pl["revenue"].sum(), 0), "💰", "#2563EB", t("Invoiced, before tax"), "neutral", t("what you sold")),
        kpi_card(t("Costs on invoices"), money_html(pl["invoice_costs"].sum(), 0), "🧾", "#E0A100", t("Entered per invoice"), "neutral", t("direct costs")),
        kpi_card(t("Other expenses"), money_html(pl["other_costs"].sum(), 0), "🧮", "#E5484D", t("From the Expenses page"), "neutral", t("running costs")),
        kpi_card(t("Net profit"), money_html(net, 0), "📈", "#0F9D74" if net >= 0 else "#E5484D", t("You keep this") if net >= 0 else t("A loss"), "up" if net >= 0 else "down",
                 t("after everything")),
    ])
    left, right = st.columns([2, 3], gap="medium")
    ordered = pl.sort_values("month")
    with left, st.container(border=True):
        card_header(t("Net profit by month"), t("Revenue minus all costs"))
        st.plotly_chart(charts.bar_chart(ordered["month"].map(lambda d: fdate(d, "%b %Y")), ordered["net_profit"], currency, height=300),
                        config={"displayModeBar": False})
    with right, st.container(border=True):
        card_header(t("Profit and loss"), t("Month by month"))
        html_table([t("Month"), t("Revenue"), t("Invoice costs"), t("Other expenses"), t("Net profit")],
                   [[f"{fdate(r.month, '%B %Y')}", money_html(r.revenue, 0), money_html(r.invoice_costs, 0), money_html(r.other_costs, 0),
                     f'<span class="pill pill--{"up" if r.net_profit >= 0 else "down"}">{money_html(r.net_profit, 0)}</span>']
                    for r in pl.itertuples()], numeric=(1, 2, 3, 4), max_height=300)
    _download(pl.assign(month=pl["month"].dt.strftime("%Y-%m")), "profit_and_loss", "rep_pnl_csv")


def render() -> None:
    page_header(t("Reports"), t("Understand who owes you, who pays you, and what makes you money"))
    today = date.today()
    df = cached_frame(today.isoformat(), stamp())
    if df.empty:
        empty_card("📑", t("No data to report yet"), t("Create your first invoice and your reports will appear here."))
        return
    currency = settings().currency
    label = st.selectbox(t("Period"), PERIODS, key="rep_period", format_func=t,
                         help=t("Applies to Best clients, Tax summary and Profit by category. “Who owes me” always shows today's unpaid invoices."))
    scoped = _period(df, label, today)
    tabs = st.tabs([t("⏳ Who owes me"), t("🏆 Best clients"), t("🧾 Tax summary"), t("📦 Profit by category"), t("📈 Profit & loss")])
    with tabs[0]:
        _aging(df, today, currency)
    with tabs[1]:
        _clients(scoped, currency)
    with tabs[2]:
        _tax(scoped)
    with tabs[3]:
        _categories(scoped, currency)
    with tabs[4]:
        exp = cached_expenses()
        if not exp.empty and label != "All time":
            since = pd.Timestamp(today.replace(month=1, day=1)) if label == "This year" else pd.Timestamp(today - timedelta(days=365))
            exp = exp[exp["spent_on"] >= since]
        _pnl(scoped, exp, currency)
