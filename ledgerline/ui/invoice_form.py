"""Create / edit an invoice with multiple line items, per-line discount and tax.

The line-item grid is an ``st.data_editor`` (add and delete rows freely). Totals are shown
live by calling the SAME ``calc`` functions the database hook uses, so the preview always
equals what gets saved.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from html import escape
from typing import Optional

import pandas as pd
import streamlit as st

from ..i18n import t
from ..calc import D, invoice_totals, line_amounts
from .. import customization as cz
from ..services import catalog, formconfig
from ..services import invoices as inv
from . import widgets
from .components import fail, show_errors, tr_error
from .components import actor, can, flash, form_fields, form_lists, invalidate, money, settings

COLS = ["Description", "Qty", "Unit price", "Disc %", "Disc amount", "Tax %"]
NEW_CLIENT = -1
PAY_UNPAID, PAY_PARTLY, PAY_PAID = "⏳ Unpaid", "💸 Partly paid", "✅ Paid in full"


@st.cache_data(ttl=30, show_spinner=False)
def _products() -> list:
    try:
        return catalog.list_products(active_only=True)
    except Exception:  # noqa: BLE001  the picker is a convenience; the form works without it
        return []


def _add_product_line(form_id: str, seed_key: str) -> None:
    """Button callback: append the chosen product to the grid and reset the editor so it shows the new row."""
    pick = st.session_state.get(f"catpick_{form_id}")
    product = next((p for p in _products() if p.id == pick), None)
    if product is None:
        return
    current = st.session_state.get(f"_cur_items_{form_id}")
    rows = current.copy() if isinstance(current, pd.DataFrame) else pd.DataFrame(columns=COLS)
    rows = rows[rows["Description"].fillna("").astype(str).str.strip() != ""]  # drop the empty starter row
    new = pd.DataFrame([{"Description": product.name, "Qty": 1.0, "Unit price": float(product.unit_price), "Disc %": 0.0,
                         "Disc amount": 0.0, "Tax %": float(product.tax_rate)}], columns=COLS)
    st.session_state[seed_key] = pd.concat([rows, new], ignore_index=True)
    st.session_state.pop(f"items_{form_id}", None)
    st.session_state[f"catpick_{form_id}"] = None


def _seed_rows(detail: Optional[inv.InvoiceDetail], default_tax: Decimal) -> pd.DataFrame:
    if detail:
        return pd.DataFrame([{"Description": i.description, "Qty": float(i.quantity), "Unit price": float(i.unit_price),
                              "Disc %": float(i.discount_pct), "Disc amount": float(i.discount_amount),
                              "Tax %": float(i.tax_rate)} for i in detail.items], columns=COLS)
    return pd.DataFrame([{"Description": "", "Qty": 1.0, "Unit price": 0.0, "Disc %": 0.0, "Disc amount": 0.0,
                          "Tax %": float(default_tax)}], columns=COLS)


def _parse_items(frame: pd.DataFrame) -> list[inv.ItemInput]:
    items = []
    for row in frame.to_dict("records"):
        desc = str(row.get("Description") or "").strip()
        numbers = [row.get(c) for c in COLS[1:]]
        if not desc and all(pd.isna(v) or v in (0, 0.0, 1, 1.0) for v in numbers):
            continue  # an untouched blank row
        items.append(inv.ItemInput(desc, D(row.get("Qty")), D(row.get("Unit price")), D(row.get("Disc %")),
                                   D(row.get("Disc amount")), D(row.get("Tax %"))))
    return items


def _preview(items: list[inv.ItemInput]):
    totals = invoice_totals(line_amounts(i.quantity, i.unit_price, i.discount_pct, i.discount_amount, i.tax_rate) for i in items)
    cfg = settings()
    rows = [(t("Subtotal"), totals.subtotal), (t("Discounts"), -totals.discount_total), (cfg.tax_label, totals.tax_total)]
    html = "".join(f"<div><span>{k}</span><span>{escape(money(v))}</span></div>" for k, v in rows)
    html += f'<div class="grand"><span>{t("Total")}</span><span>{escape(money(totals.total))}</span></div>'
    st.markdown(f'<div class="totals-box">{html}</div>', unsafe_allow_html=True)
    return totals


def _save_extras(user, entity: str, entity_id: int, texts: dict, permission: Optional[str] = None) -> list[str]:
    """Save the admin-defined extra fields. A problem here must never undo or block the invoice itself."""
    try:
        formconfig.save_values(user, entity, entity_id, texts, permission)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("Could not save extra fields for %s %s", entity, entity_id)
        return [t("The extra fields could not be saved; open Edit invoice to enter them again.")]
    return []


def _finish(message: str, *, keep_editing: bool = False) -> None:
    """After a save, STAY on this screen (never jump to another page).

    A new invoice leaves a fresh, empty form ready for the next one. After an edit the same invoice stays open,
    re-read from the database so the next save uses its new version.
    """
    for key in ("edit_version", "edit_version_id") if keep_editing else ("edit_invoice_id", "edit_version", "edit_version_id"):
        st.session_state.pop(key, None)
    st.session_state["form_nonce"] = st.session_state.get("form_nonce", 0) + 1  # fresh widgets (empty, or reloaded from the DB)
    invalidate()
    flash(message)
    st.rerun()


def render() -> None:
    cfg = settings()
    user = actor()
    edit_id: Optional[int] = st.session_state.get("edit_invoice_id")
    detail = inv.get_invoice_detail(edit_id) if edit_id else None
    if edit_id and detail is None:
        st.session_state.pop("edit_invoice_id", None)
        st.warning(t("That invoice no longer exists."))
        edit_id = None
    if detail is not None:
        if st.session_state.get("edit_version_id") != detail.id:  # remember the version we loaded
            st.session_state["edit_version"], st.session_state["edit_version_id"] = detail.version, detail.id
        if not (can("invoice.edit") and inv.can_edit(user, detail.created_at)):
            st.error(t("You are not allowed to edit this invoice. Ask an administrator."))
            return

    nonce = st.session_state.setdefault("form_nonce", 0)
    form_id = f"{edit_id or 'new'}_{nonce}"
    title = t("Edit invoice {number}", number=detail.number) if detail else t("Add new invoice")
    st.markdown(f'<div class="dash-header"><div><h1 class="dash-title">{escape(title)}</h1><div class="dash-sub">'
                + escape(t("Several line items, per-line discount and tax. Totals update as you type.")) + "</div></div></div>",
                unsafe_allow_html=True)
    fields = {f["key"]: f for f in form_fields("invoice")}
    lists = form_lists()
    extra_saved = formconfig.get_values("invoice", detail.id) if detail else {}

    clients = inv.list_clients()
    names = {c.id: c.name for c in clients}
    options = [*names, NEW_CLIENT]
    default_client = detail.client_id if detail else (options[0] if clients else NEW_CLIENT)

    with st.container(border=True):
        number_visible = fields["number"].get("visible", True)
        c1, c2, c3 = st.columns(3, gap="medium")
        client_id = c1.selectbox(t("Client"), options, index=options.index(default_client),
                                 format_func=lambda i: t("➕ New client...") if i == NEW_CLIENT else names[i], key=f"client_{form_id}")
        suggested = detail.number if detail else inv.suggest_invoice_number()
        if number_visible:
            number = c2.text_input(cz.field_label(fields["number"], t) + (" *" if fields["number"].get("required") else ""),
                                   value=suggested, key=f"number_{form_id}",
                                   help=t("Generated for you; edit it to use your own numbering."))
            issue = c3.date_input(t("Issue date"), value=detail.issue_date if detail else date.today(), key=f"issue_{form_id}")
        else:
            number = suggested
            issue = c2.date_input(t("Issue date"), value=detail.issue_date if detail else date.today(), key=f"issue_{form_id}")

        new_client, new_client_extras, client_problems = None, {}, []
        if client_id == NEW_CLIENT:
            new_client, new_client_extras, client_problems = widgets.client_form(f"nc{form_id}", widgets.client_values(None))

        due_default = detail.due_date if detail else issue + timedelta(days=cfg.payment_terms_days)
        order = {f["key"]: i for i, f in enumerate(form_fields("invoice"))}
        detail_fields = [f for f in form_fields("invoice") if f["key"] in ("due_date", "category", "payment_method", "expenses") or not f["builtin"]]
        detail_fields.sort(key=lambda f: order[f["key"]])          # same order as on the Customize page
        current = {"due_date": max(due_default, issue), "category": detail.category if detail else lists["categories"][0],
                   "payment_method": detail.payment_method if detail else lists["payment_methods"][0],
                   "expenses": float(detail.expenses) if detail else 0.0}
        for f in detail_fields:
            if not f["builtin"]:
                current[f["key"]] = cz.decode_value(f, extra_saved.get(f["key"]))
        values = widgets.render_fields(
            detail_fields, current, f"inv_{form_id}", columns=4,
            options={"category": lists["categories"], "payment_method": lists["payment_methods"]},
            overrides={"due_date": {"min_value": issue, "key": f"inv_{form_id}_due_{issue}"},
                       "expenses": {"min_value": 0.0, "step": 10.0, "format": "%.2f",
                                    "help": t("Your own cost for this job. Used for profit, never printed on the invoice.")}})
        due = values.get("due_date") or due_default
        category = values.get("category") or current["category"]
        method = values.get("payment_method") or current["payment_method"]
        expenses = values.get("expenses", current["expenses"]) or 0.0
        extra_texts = {f["key"]: cz.encode_value(f, values[f["key"]]) for f in detail_fields
                       if not f["builtin"] and f["key"] in values}
        missing = [t("{field} is required.", field=name) for name in cz.missing_required(detail_fields, values, t)]

        st.markdown("**" + t("Line items") + "**")
        seed_key = f"seed_{form_id}"
        if seed_key not in st.session_state:
            st.session_state[seed_key] = _seed_rows(detail, cfg.default_tax_rate)
        edited = st.data_editor(
            st.session_state[seed_key], num_rows="dynamic", hide_index=True, key=f"items_{form_id}", width="stretch",
            column_config={
                "Description": st.column_config.TextColumn(t("Description"), required=True, max_chars=255, width="large"),
                "Qty": st.column_config.NumberColumn(t("Qty"), min_value=0.001, step=0.5, format="%.3f", required=True),
                "Unit price": st.column_config.NumberColumn(t("Unit price"), min_value=0.0, step=1.0, format="%.2f", required=True),
                "Disc %": st.column_config.NumberColumn(t("Disc %"), min_value=0.0, max_value=100.0, step=1.0, format="%.2f"),
                "Disc amount": st.column_config.NumberColumn(t("Disc amount"), min_value=0.0, step=1.0, format="%.2f"),
                "Tax %": st.column_config.NumberColumn(f"{cfg.tax_label} %", min_value=0.0, max_value=100.0, step=1.0, format="%.2f"),
            })
        items = _parse_items(edited)
        products = _products()
        if products:
            st.session_state[f"_cur_items_{form_id}"] = edited
            p1, p2 = st.columns([4, 1], gap="small", vertical_alignment="bottom")
            ids = [p.id for p in products]
            by_id = {p.id: p for p in products}
            chosen = p1.selectbox(t("Add from your product list"), ids, index=None, key=f"catpick_{form_id}", placeholder=t("Search your products ..."),
                                  format_func=lambda i: f"{by_id[i].name}  ·  {money(by_id[i].unit_price)}")
            p2.button(t("➕ Add line"), key=f"catadd_{form_id}", on_click=_add_product_line, args=(form_id, seed_key),
                      disabled=chosen is None, width="stretch")
        left, right = st.columns([3, 2], gap="large")
        if fields["notes"].get("visible", True):
            notes = left.text_area(cz.field_label(fields["notes"], t), value=detail.notes if detail else "", height=110, key=f"notes_{form_id}")
        else:
            notes = (detail.notes or "") if detail else ""
        with right:
            totals = _preview(items)

        # --- Payment: choose "Paid in full" here instead of hunting for it after saving.
        pay_amount, pay_date, pay_error = Decimal(0), date.today(), ""
        pay_choice = PAY_UNPAID
        if not detail and can("payment.record"):
            st.markdown("**" + t("Payment") + "**")
            pay_choice = st.radio(t("Payment status"), [PAY_UNPAID, PAY_PARTLY, PAY_PAID], horizontal=True, format_func=t,
                                  key=f"paystatus_{form_id}", label_visibility="collapsed")
            total = D(totals.total)
            if pay_choice != PAY_UNPAID:
                p1, p2 = st.columns(2, gap="medium")
                if pay_choice == PAY_PAID:
                    pay_amount = total
                    p1.metric(t("Amount received"), money(total))
                else:
                    pay_amount = D(p1.number_input(t("Amount received so far"), min_value=0.0, step=10.0, format="%.2f",
                                                   value=0.0, key=f"payamt_{form_id}"))
                pay_date = p2.date_input(t("Received on"), value=date.today(), max_value=date.today(), key=f"paydate_{form_id}")
                if total <= 0:
                    pay_error = t("Add a line item with a price before recording a payment.")
                elif pay_choice == PAY_PARTLY and not (0 < pay_amount < total):
                    pay_error = t("A partial payment must be more than 0 and less than the total ({total}).", total=money(total))

        save, cancel = st.columns([1, 1])
        if save.button(t("💾 Save changes") if detail else t("💾 Submit & Save Invoice"), type="primary", key=f"save_{form_id}"):
            data = inv.InvoiceInput(issue_date=issue, due_date=due, category=category, payment_method=method, items=items,
                                    client_id=None if client_id == NEW_CLIENT else client_id, new_client=new_client,
                                    number=number, expenses=D(expenses), notes=notes)
            problems = [text for text in [pay_error, *missing, *client_problems] if text]
            if problems:
                show_errors(problems)
            else:
                message, warnings, saved = "", [], False
                try:
                    if detail:
                        inv.update_invoice(user, detail.id, data, expected_version=st.session_state.get("edit_version"))
                        warnings += _save_extras(user, "invoice", detail.id, extra_texts, "invoice.edit")
                        message = t("Saved changes to {number}", number=number)
                    else:
                        new_id = inv.create_invoice(user, data)
                        message = t("Created {number}", number=number.strip() or t("invoice"))
                        warnings += _save_extras(user, "invoice", new_id, extra_texts)
                        if new_client_extras:
                            created = inv.get_invoice_detail(new_id)
                            if created is not None:
                                warnings += _save_extras(user, "client", created.client_id, new_client_extras)
                        if pay_choice != PAY_UNPAID and pay_amount > 0:
                            try:
                                inv.add_payment(user, new_id, pay_amount, pay_date, method)
                                message += t(" - marked as paid") if pay_choice == PAY_PAID else t(" - {amount} received", amount=money(pay_amount))
                            except Exception as exc:  # noqa: BLE001  the invoice itself is saved; only the payment failed
                                reason = " ".join(getattr(exc, "errors", None) or [str(exc)])
                                message += t(", but the payment was NOT recorded ({error}). Record it from Invoice actions.", error=tr_error(reason))
                    saved = True
                except Exception as exc:  # noqa: BLE001  shown in plain words; nothing is left half-saved
                    fail(exc)
                if saved:
                    if warnings:
                        message += " " + " ".join(warnings)
                    _finish(message, keep_editing=bool(detail))   # outside the try: it ends with st.rerun()
        if detail and cancel.button(t("Cancel editing"), key=f"cancel_{form_id}"):
            for key in ("edit_invoice_id", "edit_version", "edit_version_id"):
                st.session_state.pop(key, None)
            st.session_state["form_nonce"] = st.session_state.get("form_nonce", 0) + 1
            st.rerun()               # stays on this screen, as a fresh new-invoice form
