"""Expenses page: business costs that are not tied to one invoice, and the real profit after them."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from html import escape

import pandas as pd
import streamlit as st

from ..formatting import fdate
from ..i18n import t
from .. import reportcalc
from ..services import expenses as svc
from . import charts
from .components import fail
from .components import (form_lists, actor, cached_expenses, can, card_header, empty_card, empty_state, flash, html_table, invalidate, kpi_card,
                         kpi_row, money_html, page_header, plural, settings, stamp)
from .dashboard import cached_frame

RANGES = ("This month", "Last 30 days", "This year", "All time")


def _window(label: str, today: date, first: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = {"This month": today.replace(day=1), "Last 30 days": today - timedelta(days=29),
             "This year": today.replace(month=1, day=1)}.get(label, first)
    return pd.Timestamp(start), pd.Timestamp(today)


def _run(action, success: str) -> None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001  business errors and surprises alike are shown in plain words
        fail(exc)
        return
    invalidate()
    flash(success)
    st.rerun()


def _add_form() -> None:
    n = st.session_state.setdefault("expense_form_nonce", 0)
    with st.container(border=True):
        card_header(t("Add an expense"), t("Money you spent on the business"))
        with st.form(f"add_expense_{n}", border=False):
            a, b = st.columns(2, gap="medium")
            spent_on = a.date_input(t("Date"), value=date.today(), max_value=date.today(), key=f"ex_date_{n}")
            vendor = b.text_input(t("Paid to *"), placeholder=t("Facebook Ads, Canva, Namecheap ..."), max_chars=160, key=f"ex_vendor_{n}")
            c, d = st.columns(2, gap="medium")
            category = c.selectbox(t("Category"), list(form_lists()["expense_categories"]), key=f"ex_cat_{n}", format_func=t)
            amount = d.number_input(t("Amount *"), min_value=0.0, step=1.0, format="%.2f", value=0.0, key=f"ex_amt_{n}")
            note = st.text_input(t("Note (optional)"), key=f"ex_note_{n}")
            if st.form_submit_button(t("Save expense"), type="primary"):
                data = svc.ExpenseInput(spent_on, vendor, category, Decimal(str(amount)), note)

                def _save() -> None:
                    svc.add_expense(actor(), data)
                    st.session_state["expense_form_nonce"] = n + 1

                _run(_save, t("Expense saved"))


def render() -> None:
    page_header(t("Expenses"), t("Track what you spend, so you see your real profit"))
    today = date.today()
    exp = cached_expenses()
    frame = cached_frame(today.isoformat(), stamp())
    currency = settings().currency

    if exp.empty:
        empty_card("🧮", t("No expenses yet"), t("Add your costs (ads, tools, fees). Your profit then shows what you really keep."))
        if can("expense.manage"):
            _add_form()
        return

    label = st.selectbox(t("Period"), RANGES, index=1, key="exp_range", format_func=t)
    firsts = [exp["spent_on"].min()] + ([frame["issue_date"].min()] if not frame.empty else [])
    start, end = _window(label, today, min(firsts).date())   # "All time" must also cover the oldest invoice
    view = exp[(exp["spent_on"] >= start) & (exp["spent_on"] <= end)]
    inv_view = frame[(frame["issue_date"] >= start) & (frame["issue_date"] <= end) & (frame["status"] != "Cancelled")] if not frame.empty else frame

    spent = float(view["amount"].sum())
    revenue = float(inv_view["revenue"].sum()) if not inv_view.empty else 0.0
    inv_costs = float(inv_view["expenses"].sum()) if not inv_view.empty else 0.0
    net = revenue - inv_costs - spent
    cats = reportcalc.expense_by_category(view)
    kpi_row([
        kpi_card(t("Total spent"), money_html(spent, 0), "🧮", "#E5484D", plural(len(view), "expense"), "neutral", t(label).lower()),
        kpi_card(t("Biggest cost"), escape(str(cats.iloc[0]["category"])) if not cats.empty else "-", "📌", "#E0A100",
                 money_html(cats.iloc[0]["amount"], 0) if not cats.empty else t("No data"), "neutral", t("by category")),
        kpi_card(t("Revenue"), money_html(revenue, 0), "💰", "#2563EB", t("Invoiced, before tax"), "neutral", t("same period")),
        kpi_card(t("Profit after expenses"), money_html(net, 0), "📈", "#0F9D74" if net >= 0 else "#E5484D",
                 t("You are in profit") if net >= 0 else t("Spending is higher"), "up" if net >= 0 else "down", t("revenue minus all costs")),
    ])

    left, right = st.columns([2, 3], gap="medium")
    with left:
        if can("expense.manage"):
            _add_form()
        else:
            st.info(t("You can view expenses. Ask a manager to add them."))
    with right, st.container(border=True):
        card_header(t("Where the money goes"), t("Spending by category"))
        if cats.empty:
            empty_state(t("No expenses in this period."))
        else:
            st.plotly_chart(charts.bar_chart(cats["category"], cats["amount"], currency, horizontal=True,
                                             height=max(240, 44 * len(cats))), config={"displayModeBar": False})

    with st.container(border=True):
        card_header(t("All expenses"), t("{v1:,} in this period, newest first", v1=len(view)))
        html_table([t("Date"), t("Paid to"), t("Category"), t("Note"), t("Amount")],
                   [[f"{fdate(r.spent_on, '%b %d, %Y')}", f'<span class="strong">{escape(r.vendor)}</span>',
                     f'<span class="pill pill--neutral">{escape(t(r.category))}</span>', f'<span class="muted">{escape(r.note or "-")}</span>',
                     money_html(r.amount, 2)] for r in view.head(200).itertuples()], numeric=(4,), max_height=420,
                   empty=t("No expenses in this period."))
        if can("data.export") and not view.empty:
            out = view.assign(spent_on=view["spent_on"].dt.strftime("%Y-%m-%d")).drop(columns=["id"])
            st.download_button(t("📥 Download expenses (CSV)"), data=out.to_csv(index=False).encode("utf-8"),
                               file_name=f"expenses_{today:%Y-%m-%d}.csv", mime="text/csv", key="exp_csv")

    if can("expense.manage") and not view.empty:
        with st.expander(t("🗑️ Delete an expense")):
            by_id = {int(r.id): r for r in view.head(200).itertuples()}
            pick = st.selectbox(t("Choose"), list(by_id), key="exp_delete_pick",
                                format_func=lambda i: f"{fdate(by_id[i].spent_on, '%b %d')} · {by_id[i].vendor} · {by_id[i].amount:,.2f}")
            sure = st.checkbox(t("Yes, delete it. This cannot be undone."), key="exp_delete_sure")
            if st.button(t("Delete expense"), disabled=not sure, key="exp_delete_btn"):
                _run(lambda: svc.delete_expense(actor(), int(pick)), t("Expense deleted"))
