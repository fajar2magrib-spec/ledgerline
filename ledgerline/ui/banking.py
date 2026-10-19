"""Banking page: accounts, receive customer payments into an account, transactions and reconciliation, business loans."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

import pandas as pd
import streamlit as st

from ..formatting import fdate
from ..i18n import t
from ..services import banking as svc
from .components import (actor, can, card_header, empty_card, fail, flash, html_table, invalidate, kpi_card, kpi_row,
                         money_html, page_header, settings, stamp, show_errors)
from .dashboard import cached_frame

KIND_LABELS = {"bank": "Bank account", "cash": "Cash", "card": "Card", "wallet": "Mobile wallet"}


def _run(action, success: str) -> None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001  shown in plain words
        fail(exc)
        return
    invalidate()
    flash(success)
    st.rerun()


def _accounts() -> pd.DataFrame:
    df = svc.accounts_frame()
    return df[df["is_active"]] if not df.empty else df


def _account_picker(label: str, accounts: pd.DataFrame, key: str, allow_none: bool = False):
    ids = ([0] if allow_none else []) + [int(i) for i in accounts["id"]]
    names = {int(r.id): r.name for r in accounts.itertuples()}
    names[0] = t("Do not record in an account")
    return st.selectbox(label, ids, key=key, format_func=lambda i: names[i])


# ------------------------------------------------------------------------------------------- accounts
def _accounts_tab(accounts: pd.DataFrame) -> None:
    currency = settings().currency
    if accounts.empty:
        empty_card("🏦", t("No accounts yet"), t("Add your bank account, cash box or mobile wallet to start tracking balances."))
    else:
        total = float(accounts["balance"].sum())
        kpi_row([
            kpi_card(t("Total balance"), money_html(total, 0), "🏦", "#2563EB", t("{n} accounts", n=len(accounts)), "neutral", t("all accounts")),
            kpi_card(t("To reconcile"), f"{int(accounts['uncleared'].sum()):,}", "🧾", "#E0A100", t("not yet matched to a statement"),
                     "neutral", t("transactions")),
        ])
        rows = [[f'<span class="strong">{escape(r.name)}</span>', escape(t(KIND_LABELS.get(r.kind, r.kind))),
                 money_html(r.balance, 2), money_html(r.cleared_balance, 2), f"{int(r.uncleared):,}"] for r in accounts.itertuples()]
        with st.container(border=True):
            card_header(t("Accounts"), currency)
            html_table([t("Account"), t("Type"), t("Balance"), t("Cleared balance"), t("To reconcile")], rows, numeric={2, 3, 4})
    if not can("bank.manage"):
        return
    with st.container(border=True):
        card_header(t("➕ Add an account"), t("Where your money is kept"))
        with st.form("add_account", border=False, clear_on_submit=True):
            a, b, c = st.columns(3, gap="medium")
            name = a.text_input(t("Account name *"), max_chars=80, placeholder=t("e.g. HBL Current, Cash in hand"))
            kind = b.selectbox(t("Type"), list(svc.ACCOUNT_KINDS), format_func=lambda k: t(KIND_LABELS[k]))
            opening = c.number_input(t("Opening balance"), step=100.0, format="%.2f", value=0.0)
            if st.form_submit_button(t("Save account"), type="primary"):
                _run(lambda: svc.create_account(actor(), name, kind, opening), t("Account added"))


# ------------------------------------------------------------------------------- receive a payment
def _receive_tab(accounts: pd.DataFrame) -> None:
    if not can("payment.record"):
        st.info(t("Ask a manager to record payments."))
        return
    frame = cached_frame(date.today().isoformat(), stamp())
    open_inv = frame[(frame["status"] != "Cancelled") & (frame["balance"] > 0.004)] if not frame.empty else frame
    with st.container(border=True):
        card_header(t("💰 Receive a payment"), t("Money a client paid you. It is added to the invoice and to your account."))
        if open_inv.empty:
            st.success(t("No unpaid invoices. Nothing to receive."))
            return
        labels = {int(r.id): f"{r.number} | {r.client} | {t('Balance')} {r.balance:,.2f}" for r in open_inv.itertuples()}
        with st.form("receive_payment", border=False, clear_on_submit=True):
            invoice_id = st.selectbox(t("Invoice"), list(labels), format_func=labels.get)
            a, b, c = st.columns(3, gap="medium")
            amount = a.number_input(t("Amount received"), min_value=0.0, step=10.0, format="%.2f", value=0.0,
                                    help=t("Type the amount the client paid. A part payment is fine."))
            paid_on = b.date_input(t("Date received"), value=date.today(), max_value=date.today())
            method = c.selectbox(t("Payment method"), ["Bank Transfer", "Cash", "Credit Card", "PayPal", "Stripe"], format_func=t)
            d, e = st.columns(2, gap="medium")
            account = 0
            if not accounts.empty:
                ids = [0] + [int(i) for i in accounts["id"]]
                names = {int(r.id): r.name for r in accounts.itertuples()}
                names[0] = t("Do not record in an account")
                account = d.selectbox(t("Deposit to"), ids, format_func=names.get)
            reference = e.text_input(t("Reference (optional)"), max_chars=80)
            if st.form_submit_button(t("Record payment"), type="primary"):
                if amount <= 0:
                    show_errors([t("Enter a payment amount greater than zero.")])
                else:
                    _run(lambda: svc.receive_payment(actor(), int(invoice_id), Decimal(str(amount)), paid_on, method,
                                                    int(account) or None, reference), t("Payment recorded"))


# ----------------------------------------------------------------------------- transactions + reconcile
def _transactions_tab(accounts: pd.DataFrame) -> None:
    if accounts.empty:
        st.info(t("Add an account first."))
        return
    if can("bank.manage"):
        left, right = st.columns(2, gap="medium")
        with left, st.container(border=True):
            card_header(t("Money in or out"), t("A deposit or a withdrawal"))
            with st.form("add_txn", border=False, clear_on_submit=True):
                account = _account_picker(t("Account"), accounts, "txn_account")
                a, b = st.columns(2, gap="medium")
                direction = a.radio(t("Type"), ["in", "out"], horizontal=True,
                                    format_func=lambda v: t("💵 Money in") if v == "in" else t("💸 Money out"))
                amount = b.number_input(t("Amount *"), min_value=0.0, step=10.0, format="%.2f", value=0.0)
                c, d = st.columns(2, gap="medium")
                when = c.date_input(t("Date"), value=date.today(), max_value=date.today())
                category = d.selectbox(t("Category"), list(svc.TXN_CATEGORIES), format_func=t)
                description = st.text_input(t("Description"), max_chars=200)
                if st.form_submit_button(t("Save transaction"), type="primary"):
                    _run(lambda: svc.add_txn(actor(), int(account), when, direction, amount, description, category), t("Transaction saved"))
        with right, st.container(border=True):
            card_header(t("Transfer between accounts"), t("Move money from one account to another"))
            if len(accounts) < 2:
                st.caption(t("You need two accounts to make a transfer."))
            else:
                with st.form("transfer", border=False, clear_on_submit=True):
                    src = _account_picker(t("From"), accounts, "tr_from")
                    dst = _account_picker(t("To"), accounts, "tr_to")
                    a, b = st.columns(2, gap="medium")
                    amount = a.number_input(t("Amount *"), min_value=0.0, step=10.0, format="%.2f", value=0.0, key="tr_amount")
                    when = b.date_input(t("Date"), value=date.today(), max_value=date.today(), key="tr_date")
                    if st.form_submit_button(t("Transfer"), type="primary"):
                        _run(lambda: svc.transfer(actor(), int(src), int(dst), when, amount), t("Transfer done"))

    with st.container(border=True):
        card_header(t("Reconcile with your bank statement"), t("Tick the transactions that appear on your statement"))
        account_id = _account_picker(t("Account"), accounts, "rec_account")
        row = accounts[accounts["id"] == account_id].iloc[0]
        txns = svc.txns_frame(int(account_id))
        statement = st.number_input(t("Balance on your statement"), step=100.0, format="%.2f", value=float(row["cleared_balance"]),
                                    key=f"rec_stmt_{account_id}")
        diff = round(float(statement) - float(row["cleared_balance"]), 2)
        if abs(diff) < 0.005:
            st.success(t("Reconciled: your ticked transactions match the statement."))
        else:
            st.warning(t("Difference: {amount}. Tick the transactions that are on your statement until this is zero.", amount=f"{diff:,.2f}"))
        if txns.empty:
            st.caption(t("No transactions yet."))
            return
        view = txns[["id", "txn_date", "description", "category", "signed", "cleared", "source"]].copy()
        view["txn_date"] = pd.to_datetime(view["txn_date"]).dt.date
        edited = st.data_editor(
            view, hide_index=True, width="stretch", key=f"rec_editor_{account_id}",
            disabled=["id", "txn_date", "description", "category", "signed", "source"] if can("bank.manage") else True,
            column_config={"id": None, "source": None,
                           "txn_date": st.column_config.DateColumn(t("Date")),
                           "description": st.column_config.TextColumn(t("Description")),
                           "category": st.column_config.TextColumn(t("Category")),
                           "signed": st.column_config.NumberColumn(t("Amount"), format="%.2f"),
                           "cleared": st.column_config.CheckboxColumn(t("Cleared"))})
        if can("bank.manage") and st.button(t("💾 Save reconciliation"), key=f"rec_save_{account_id}", type="primary"):
            changed = edited[edited["cleared"] != view["cleared"]]
            _run(lambda: svc.set_cleared(actor(), changed[changed["cleared"]]["id"].tolist(),
                                         changed[~changed["cleared"]]["id"].tolist()), t("Reconciliation saved"))
        manual = txns[txns["source"] == "manual"]
        if can("bank.manage") and not manual.empty:
            with st.expander(t("Delete a transaction you typed in")):
                labels = {int(r.id): f"{fdate(pd.Timestamp(r.txn_date), '%b %d, %Y')} | {r.description or t(r.category)} | {r.signed:,.2f}"
                          for r in manual.itertuples()}
                pick = st.selectbox(t("Transaction"), list(labels), format_func=labels.get, key=f"del_txn_{account_id}")
                if st.button(t("🗑️ Delete"), key=f"del_txn_btn_{account_id}"):
                    _run(lambda: svc.delete_txn(actor(), int(pick)), t("Transaction deleted"))


# ----------------------------------------------------------------------------------------------- loans
def _loans_tab(accounts: pd.DataFrame) -> None:
    loans = svc.loans_frame()
    if loans.empty:
        empty_card("🤝", t("No loans yet"), t("Record money you borrowed (a bank loan, a family loan) to track what you still owe."))
    else:
        live = loans[~loans["closed"]]
        kpi_row([
            kpi_card(t("Still owed"), money_html(float(live["outstanding"].sum()), 0), "🤝", "#E5484D", t("{n} open loans", n=len(live)), "neutral", t("principal")),
            kpi_card(t("Monthly instalments"), money_html(float(live["emi"].sum()), 0), "📅", "#E0A100", t("estimated"), "neutral", t("all open loans")),
            kpi_card(t("Interest paid"), money_html(float(loans["interest_paid"].sum()), 0), "💸", "#7C3AED", t("counted as an expense"), "neutral", t("all loans")),
        ])
        rows = [[f'<span class="strong">{escape(r.lender)}</span>', money_html(r.principal, 0), f"{r.rate:g}%",
                 fdate(pd.Timestamp(r.start_date), "%b %d, %Y"), money_html(r.emi, 0), money_html(r.outstanding, 0),
                 t("Closed") if r.closed else t("Open")] for r in loans.itertuples()]
        with st.container(border=True):
            card_header(t("Loans"), t("What you borrowed and what is left"))
            html_table([t("Lender"), t("Amount"), t("Interest/yr"), t("Started"), t("Monthly instalment"), t("Still owed"), t("Status")],
                       rows, numeric={1, 4, 5})
    if not can("bank.manage"):
        return
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        card_header(t("➕ Add a loan"), t("Money the business borrowed"))
        with st.form("add_loan", border=False, clear_on_submit=True):
            lender = st.text_input(t("Lender *"), max_chars=160, placeholder=t("Bank or person"))
            a, b = st.columns(2, gap="medium")
            amount = a.number_input(t("Loan amount *"), min_value=0.0, step=1000.0, format="%.2f", value=0.0)
            rate = b.number_input(t("Interest per year %"), min_value=0.0, max_value=100.0, step=0.5, value=0.0)
            c, d = st.columns(2, gap="medium")
            when = c.date_input(t("Date received"), value=date.today(), max_value=date.today(), key="loan_date")
            months = d.number_input(t("Term (months)"), min_value=1, max_value=600, step=1, value=12)
            account = 0
            if not accounts.empty:
                ids = [0] + [int(i) for i in accounts["id"]]
                names = {int(r.id): r.name for r in accounts.itertuples()}
                names[0] = t("Do not record in an account")
                account = st.selectbox(t("Deposit to"), ids, format_func=names.get, key="loan_account")
            note = st.text_input(t("Note (optional)"), key="loan_note")
            if st.form_submit_button(t("Save loan"), type="primary"):
                data = svc.LoanInput(lender, Decimal(str(amount)), Decimal(str(rate)), when, int(months), int(account) or None, note)
                _run(lambda: svc.create_loan(actor(), data), t("Loan added"))
    with right, st.container(border=True):
        card_header(t("Repay a loan"), t("A repayment, split into principal and interest"))
        open_loans = loans[~loans["closed"]] if not loans.empty else loans
        if open_loans.empty:
            st.caption(t("No open loans to repay."))
            return
        labels = {int(r.id): f"{r.lender} | {t('Still owed')} {r.outstanding:,.2f}" for r in open_loans.itertuples()}
        with st.form("repay_loan", border=False, clear_on_submit=True):
            loan_id = st.selectbox(t("Loan"), list(labels), format_func=labels.get)
            a, b = st.columns(2, gap="medium")
            principal = a.number_input(t("Principal part"), min_value=0.0, step=100.0, format="%.2f", value=0.0)
            interest = b.number_input(t("Interest part"), min_value=0.0, step=10.0, format="%.2f", value=0.0)
            c, d = st.columns(2, gap="medium")
            when = c.date_input(t("Date paid"), value=date.today(), max_value=date.today(), key="repay_date")
            account = 0
            if not accounts.empty:
                ids = [0] + [int(i) for i in accounts["id"]]
                names = {int(r.id): r.name for r in accounts.itertuples()}
                names[0] = t("Do not record in an account")
                account = d.selectbox(t("Pay from"), ids, format_func=names.get, key="repay_account")
            if st.form_submit_button(t("Record repayment"), type="primary"):
                _run(lambda: svc.repay_loan(actor(), int(loan_id), when, Decimal(str(principal)), Decimal(str(interest)),
                                            int(account) or None), t("Repayment recorded"))


# ------------------------------------------------------------------------------------------------ page
def render() -> None:
    page_header(t("Banking"), t("Your accounts, payments received, reconciliation and loans"))
    accounts = _accounts()
    tabs = st.tabs([t("🏦 Accounts"), t("💰 Receive payment"), t("🔄 Transactions"), t("🤝 Loans")])
    with tabs[0]:
        _accounts_tab(accounts)
    with tabs[1]:
        _receive_tab(accounts)
    with tabs[2]:
        _transactions_tab(accounts)
    with tabs[3]:
        _loans_tab(accounts)
