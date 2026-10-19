"""Clients page: directory with money owed, a profile with its statement, add and edit."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

from ..formatting import fdate
from ..i18n import t
from .. import customization as cz
from .. import reportcalc
from ..services import formconfig
from ..services import invoices as inv
from .components import fail
from .components import (PAGE_INVOICE, actor, can, card_header, empty_card, flash, html_table, invalidate, kpi_card,
                         kpi_row, money_html, page_header, stamp, status_pill, who_cell)
from . import widgets
from .components import form_fields
from .dashboard import cached_frame

MONEY_COLS = ("billed", "collected", "outstanding", "overdue")


def _directory(df: pd.DataFrame) -> pd.DataFrame:
    """Every client (even ones with no invoices yet) joined with their money totals."""
    base = pd.DataFrame([asdict(c) for c in inv.list_clients()],
                        columns=["id", "name", "email", "phone", "tax_id", "address"])
    summary = reportcalc.client_summary(df).rename(columns={"client": "name"})
    out = base.merge(summary, on="name", how="left")
    for col in MONEY_COLS:
        out[col] = out[col].fillna(0.0).astype(float)
    out["invoices"] = out["invoices"].fillna(0).astype(int)
    return out.sort_values(["billed", "name"], ascending=[False, True]).reset_index(drop=True)


def _run(action, success: str) -> None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        fail(exc)
        return
    invalidate()
    flash(success)
    st.rerun()


def _add_form() -> None:
    """Keys carry a counter that grows after each successful save: the next form starts empty,
    but after an error (duplicate name...) what you typed is kept."""
    n = st.session_state.setdefault("client_form_nonce", 0)
    with st.expander(t("➕ Add a new client"), expanded=False):
        with st.form(f"add_client_form_{n}", border=False):
            data, extras, problems = widgets.client_form(f"cl{n}", widgets.client_values(None))
            if st.form_submit_button(t("Save client"), type="primary"):
                if problems:
                    for text in problems:
                        st.error(text)
                else:
                    def _save() -> None:
                        new_id = inv.create_client(actor(), data)
                        formconfig.save_values(actor(), "client", new_id, extras)
                        st.session_state["client_form_nonce"] = n + 1

                    _run(_save, t("Client {name} added", name=data.name.strip()))


def _table(frame: pd.DataFrame) -> None:
    rows = []
    for r in frame.itertuples():
        contact = escape(r.email or r.phone or "-")
        flag = status_pill("Overdue") if r.overdue > 0.005 else (status_pill("Pending") if r.outstanding > 0.005
                                                                   else (status_pill("Paid") if r.invoices else '<span class="muted">-</span>'))
        last = f'<span class="muted">{fdate(r.last_invoice, "%b %d, %Y")}</span>' if pd.notna(r.last_invoice) else '<span class="muted">-</span>'
        rows.append([who_cell(r.name), f'<span class="muted">{contact}</span>', str(r.invoices), money_html(r.billed, 0),
                     money_html(r.collected, 0), money_html(r.outstanding, 0), flag, last])
    html_table([t("Client"), t("Contact"), t("Invoices"), t("Billed"), t("Collected"), t("Outstanding"), t("Standing"), t("Last invoice")], rows,
               numeric=(2, 3, 4, 5), max_height=420, empty=t("No clients match your search."))


def _profile(row, df: pd.DataFrame) -> None:
    with st.container(border=True):
        card_header(escape(row.name), t("Client profile and account statement"))
        left, right = st.columns([1, 2], gap="large")
        with left:
            extra = formconfig.get_values("client", int(row.id))
            info = ""
            for field in form_fields("client"):
                if not field.get("visible", True) or field["key"] == "name":
                    continue
                text = (getattr(row, field["key"], "") or "") if field["builtin"] else cz.display_value(field, extra.get(field["key"]))
                info += f"<div><span>{escape(cz.field_label(field, t))}</span><span>{escape(str(text) or '-')}</span></div>"
            st.markdown(f'<div class="totals-box">{info}'
                        f'<div class="grand"><span>{t("Outstanding")}</span><span>{money_html(row.outstanding, 2)}</span></div></div>',
                        unsafe_allow_html=True)
            if can("invoice.create") and st.button(t("➕ New invoice for this client"), key=f"cl_new_{row.id}", width="stretch"):
                st.session_state["_goto"] = PAGE_INVOICE
                st.rerun()
        with right:
            stmt = reportcalc.statement(df, row.name)
            html_table([t("Invoice"), t("Issued"), t("Due"), t("Status"), t("Total"), t("Paid"), t("Balance")],
                       [[f'<span class="strong">{escape(str(r.number))}</span>', f"{fdate(r.issue_date, '%b %d, %Y')}", f"{fdate(r.due_date, '%b %d, %Y')}",
                         status_pill(r.status), money_html(r.total, 2), money_html(r.paid, 2), money_html(r.balance, 2)]
                        for r in stmt.itertuples()],
                       numeric=(4, 5, 6), max_height=260, empty=t("No invoices for this client yet."))
            if can("data.export") and not stmt.empty:
                st.download_button(t("📥 Download statement (CSV)"), data=stmt.to_csv(index=False).encode("utf-8"),
                                   file_name=f"statement_{row.name.replace(' ', '_')}_{date.today():%Y-%m-%d}.csv",
                                   mime="text/csv", key=f"cl_stmt_{row.id}")
        if can("client.edit"):
            with st.expander(t("✏️ Edit client details")):
                with st.form(f"edit_client_{row.id}", border=False):
                    data, extras, problems = widgets.client_form(f"ec{row.id}", widgets.client_values(row, extra))
                    if st.form_submit_button(t("Save changes"), type="primary"):
                        if problems:
                            for text in problems:
                                st.error(text)
                        else:
                            def _save_changes() -> None:
                                inv.update_client(actor(), int(row.id), data)
                                formconfig.save_values(actor(), "client", int(row.id), extras)

                            _run(_save_changes, t("Client saved"))


def render() -> None:
    page_header(t("Clients"), t("Everyone you invoice, what they owe, and their full history"))
    df = cached_frame(date.today().isoformat(), stamp())
    clients = _directory(df)

    if clients.empty:
        empty_card("👥", t("No clients yet"), t("Add your first client below, or create an invoice and the client is saved for you."))
        if can("client.edit"):
            _add_form()
        return

    owing = clients[clients["outstanding"] > 0.005]
    top = clients.iloc[0]
    kpi_row([
        kpi_card(t("Total clients"), f"{len(clients):,}", "👥", "#2563EB", t("{v1} active", v1=int((clients['invoices'] > 0).sum())), "up",
                 t("with invoices")),
        kpi_card(t("Clients who owe you"), f"{len(owing):,}", "⏳", "#E0A100", t("{v1} overdue", v1=int((clients['overdue'] > 0.005).sum())),
                 "down" if (clients["overdue"] > 0.005).any() else "up", t("unpaid balance")),
        kpi_card(t("Total outstanding"), money_html(clients["outstanding"].sum(), 0), "💼", "#E5484D", t("Across all clients"), "neutral",
                 t("to collect")),
        kpi_card(t("Best client"), escape(top["name"]) if top["billed"] > 0 else "-", "🏆", "#0F9D74",
                 money_html(top["billed"], 0) if top["billed"] > 0 else t("No sales yet"), "up", t("total billed")),
    ])

    if can("client.edit"):
        _add_form()
    query = st.text_input(t("Search clients"), placeholder=t("Search by name, email or phone"), key="client_query",
                          label_visibility="collapsed")
    shown = clients
    if query.strip():
        q = query.strip()
        mask = (clients["name"].str.contains(q, case=False, regex=False, na=False)
                | clients["email"].fillna("").str.contains(q, case=False, regex=False)
                | clients["phone"].fillna("").str.contains(q, case=False, regex=False))
        shown = clients[mask]
    with st.container(border=True):
        card_header(t("Client directory"), t("{v1:,} of {v2:,} clients, biggest first", v1=len(shown), v2=len(clients)))
        _table(shown)

    if not shown.empty:
        ids = list(shown["id"])
        by_id = {int(r.id): r for r in shown.itertuples()}
        if st.session_state.get("client_pick") not in ids:
            st.session_state.pop("client_pick", None)
        pick = st.selectbox(t("Open a client's profile"), ids, format_func=lambda i: by_id[int(i)].name, key="client_pick")
        _profile(by_id[int(pick)], df)
