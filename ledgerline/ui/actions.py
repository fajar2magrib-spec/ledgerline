"""Per-invoice actions: overview and payments, PDF/WhatsApp, email, manage.

Every button is shown according to the user's role, and every service call re-checks the
permission, so hiding a button is a convenience and never the security boundary.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from ..i18n import current_lang, t
from .. import customization as cz
from ..integrations import messaging
from ..integrations.email import default_email, get_email_service
from ..formatting import fdate
from ..pdf import REPORTLAB_AVAILABLE, build_invoice_pdf
from ..services import invoices as inv
from ..services import formconfig, outbox
from . import components as ui
from . import widgets
from .components import fail
from .components import (PAGE_INVOICE, STATUS_TONE, actor, badge, button_width, can, card_header, empty_state,
                         flash, html_table, invalidate, money, money_html, settings)



def _methods() -> list[str]:
    """Payment methods chosen on the Customize page."""
    return list(ui.form_lists()["payment_methods"])


def _run(action, success: str, *, rerun: bool = True) -> bool:
    """Call a service function, turning business errors into friendly messages."""
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        fail(exc)
        return False
    invalidate()
    flash(success)
    if rerun:
        st.rerun()  # full-app rerun so KPIs, charts and the sidebar refresh too
    return True


# ------------------------------------------------------------------------- tabs
def _overview(d: inv.InvoiceDetail) -> None:
    cfg = settings()
    PAYMENT_METHODS = _methods()  # editable on the Customize page
    shown = widgets.extras_html("invoice", formconfig.get_values("invoice", d.id))
    if shown:
        st.markdown(" &nbsp;|&nbsp; ".join(f"<b>{escape(k)}</b>: {escape(v)}" for k, v in shown), unsafe_allow_html=True)
    left, right = st.columns([3, 2], gap="large")
    with left:
        html_table([t("Description"), t("Qty"), t("Unit price"), t("Discount"), f"{cfg.tax_label} %", t("Total")],
                   [[f'<span class="strong">{escape(i.description)}</span>', f"{float(i.quantity):g}", money_html(i.unit_price, 2),
                     money_html(i.line_discount, 2), f"{float(i.tax_rate):g}%", money_html(i.line_total, 2)] for i in d.items],
                   numeric=(1, 2, 3, 4, 5), empty=t("No line items."))
    with right:
        rows = [(t("Subtotal"), d.subtotal), (t("Discounts"), -d.discount_total), (cfg.tax_label, d.tax_total)]
        html = "".join(f"<div><span>{k}</span><span>{escape(money(v))}</span></div>" for k, v in rows)
        html += f'<div class="grand"><span>{t("Total")}</span><span>{escape(money(d.total))}</span></div>'
        html += f'<div><span>{t("Paid")}</span><span>{escape(money(d.amount_paid))}</span></div>'
        html += f'<div class="grand"><span>{t("Balance due")}</span><span>{escape(money(d.balance))}</span></div>'
        st.markdown(f'<div class="totals-box">{html}</div>', unsafe_allow_html=True)

    st.markdown("**" + t("Payments") + "**")
    if d.payments:
        html_table([t("Date"), t("Amount"), t("Method"), t("Reference")],
                   [[f"{fdate(p.paid_on, '%b %d, %Y')}", money_html(p.amount, 2), escape(t(p.method)), escape(p.reference or "-")]
                    for p in d.payments], numeric=(1,))
        if can("payment.delete"):
            options = {f"{fdate(p.paid_on, '%b %d')} - {money(p.amount)} ({p.method})": p.id for p in d.payments}
            c1, c2 = st.columns([3, 1])
            pick = c1.selectbox(t("Remove a payment"), list(options), key=f"delpay_{d.id}", label_visibility="collapsed")
            if c2.button(t("Remove"), key=f"delpay_btn_{d.id}"):
                _run(lambda: inv.delete_payment(actor(), options[pick]), t("Payment removed"))
    else:
        st.caption(t("No payments recorded yet."))

    if can("payment.record") and d.status != "Cancelled" and d.balance > 0:
        if st.button(t("✅ Mark as fully paid ({v1})", v1=money(d.balance)), type="primary", key=f"markpaid_{d.id}",
                     help=t("Records the remaining balance as received today.")):
            method = d.payment_method if d.payment_method in PAYMENT_METHODS else PAYMENT_METHODS[0]
            _run(lambda: inv.add_payment(actor(), d.id, d.balance, date.today(), method, "Marked as paid"),
                 t("{number} marked as paid", number=d.number))
        with st.form(f"payment_form_{d.id}", border=True):
            st.markdown("**" + t("Record a payment") + "**")
            c1, c2, c3 = st.columns(3)
            amount = c1.number_input(t("Amount"), min_value=0.0, value=float(d.balance), step=10.0, format="%.2f")
            paid_on = c2.date_input(t("Paid on"), value=date.today(), max_value=date.today())
            method = c3.selectbox(t("Method"), PAYMENT_METHODS, format_func=t, index=PAYMENT_METHODS.index(d.payment_method)
                                  if d.payment_method in PAYMENT_METHODS else 0)
            reference = st.text_input(t("Reference (optional)"))
            if st.form_submit_button(t("Save payment"), type="primary"):
                _run(lambda: inv.add_payment(actor(), d.id, Decimal(str(amount)), paid_on, method, reference),
                     t("Recorded {v1} on {number}", v1=money(Decimal(str(amount))), number=d.number))


def _share(d: inv.InvoiceDetail, pdf: bytes) -> None:
    cfg = settings()
    today = date.today()
    can_remind = d.status in ("Pending", "Partially Paid") and d.balance > 0
    phone = widgets.phone_input(t("Client WhatsApp number"), f"wa_phone_{d.id}", d.client_phone,
                                help=t("Choose the country, then type the number. Save it permanently under Manage."))
    default_text = messaging.reminder_text(d, cfg, today)
    with st.expander(t("Reminder message"), expanded=False):
        text = st.text_area(t("Message"), value=default_text, height=110, key=f"wa_msg_{d.id}",
                            label_visibility="collapsed", disabled=not can_remind)
    url, usable = messaging.build_wa_link(phone, text)
    provider = messaging.get_provider()

    c1, c2, c3 = st.columns(3, gap="medium")
    c1.download_button(t("📥 Download PDF Invoice"), data=pdf, file_name=f"{d.number}.pdf", mime="application/pdf",
                       type="primary", key=f"pdf_{d.id}", disabled=not REPORTLAB_AVAILABLE, **button_width())
    c2.link_button(t("💬 WhatsApp (manual link)"), url, disabled=not can_remind, **button_width(),
                   help=None if can_remind else t("Reminders are for unpaid invoices only."))
    if provider is not None and can("message.send"):
        if c3.button(t("⚡ Send automatically"), key=f"wa_auto_{d.id}", disabled=not can_remind, **button_width(),
                     help=t("Queued and delivered in the background via {name}", name=provider.name)):
            _run(lambda: outbox.enqueue_whatsapp(actor(), d.id, phone, text), t("WhatsApp reminder queued"), rerun=False)
    else:
        c3.button(t("⚡ Send automatically"), disabled=True, key=f"wa_auto_off_{d.id}", **button_width(),
                  help=t("Configure WHATSAPP_PROVIDER (whatsapp_cloud or twilio) to enable background sending."))

    if not REPORTLAB_AVAILABLE:
        st.warning(t("PDF export needs reportlab: pip install reportlab"))
    if not usable:
        st.warning(t("That number looks incomplete, so the link opens WhatsApp's contact picker instead."))
    _message_log(d.id, "whatsapp")


def _message_log(invoice_id: int, channel: str) -> None:
    rows = [m for m in outbox.recent_messages(invoice_id, 20) if m.channel == channel][:5]
    if not rows:
        return
    st.caption(t("Recent deliveries"))
    tone = {"sent": "up", "failed": "down", "queued": "warn", "sending": "info"}
    html_table([t("When (UTC)"), t("To"), t("Status"), t("Attempts"), t("Detail")],
               [[f'<span class="muted">{fdate(m.created_at, "%b %d, %H:%M")}</span>', escape(str(m.recipient)),
                 f'<span class="pill pill--{tone.get(m.status, "neutral")}">{escape(t(str(m.status).title()))}</span>',
                 f"{m.attempts}/{m.max_attempts}", escape(str(m.last_error or "-"))[:80]] for m in rows], numeric=(3,))
    if st.button(t("Refresh status"), key=f"refresh_{channel}_{invoice_id}"):
        st.rerun(scope="fragment")


def _email(d: inv.InvoiceDetail) -> None:
    service = get_email_service()
    if not service.configured:
        st.info(t("Email is not configured. An administrator must set SMTP_HOST and SMTP_FROM_ADDRESS "
                "(see .env.example or .streamlit/secrets.toml.example)."))
    elif not REPORTLAB_AVAILABLE:
        st.warning(t("Attaching the PDF needs reportlab: pip install reportlab"))
    subject0, body0 = default_email(d, settings(), date.today())
    with st.form(f"email_form_{d.id}"):
        to_addr = st.text_input(t("Client email address"), value=d.client_email, placeholder=t("client@example.com"))
        subject = st.text_input(t("Subject"), value=subject0)
        body = st.text_area(t("Message"), value=body0, height=190)
        st.caption(t("{number}.pdf is attached automatically, with an HTML version of the message.", number=d.number))
        send = st.form_submit_button(t("✉️ Queue email"), type="primary",
                                     disabled=not (service.configured and REPORTLAB_AVAILABLE and can("message.send")))
    if send:
        _run(lambda: outbox.enqueue_email(actor(), d.id, to_addr, subject, body),
             t("Email to {to_addr} queued", to_addr=to_addr), rerun=False)
    _message_log(d.id, "email")


def _manage(d: inv.InvoiceDetail) -> None:
    editable = can("invoice.edit") and inv.can_edit(actor(), d.created_at)
    c1, c2, c3 = st.columns(3, gap="medium")
    if c1.button(t("✏️ Edit invoice"), disabled=not editable, key=f"edit_{d.id}", **button_width(),
                 help=None if editable else t("Managers can edit invoices for a limited time; ask an administrator.")):
        st.session_state["edit_invoice_id"], st.session_state["_goto"] = d.id, PAGE_INVOICE
        st.rerun()
    if d.status == "Cancelled":
        if c2.button(t("↩️ Reinstate"), disabled=not editable, key=f"reinstate_{d.id}", **button_width()):
            _run(lambda: inv.set_cancelled(actor(), d.id, False), t("{number} reinstated", number=d.number))
    elif c2.button(t("🚫 Cancel invoice"), disabled=not (can("invoice.cancel") and editable), key=f"cancel_{d.id}", **button_width()):
        _run(lambda: inv.set_cancelled(actor(), d.id, True), t("{number} cancelled", number=d.number))
    if can("invoice.delete"):
        with c3.popover(t("🗑️ Delete"), **button_width()):
            st.warning(t("The invoice disappears from every screen. The audit trail keeps a record."))
            if st.checkbox(t("I understand"), key=f"del_ok_{d.id}") and st.button(t("Delete permanently"), key=f"del_{d.id}", type="primary"):
                _run(lambda: inv.delete_invoice(actor(), d.id), t("{number} deleted", number=d.number))

    if can("client.edit"):
        with st.expander(t("Client contact: {client_name}", client_name=d.client_name)):
            with st.form(f"client_form_{d.id}"):
                current = SimpleNamespace(name=d.client_name, email=d.client_email, phone=d.client_phone,
                                          tax_id=d.client_tax_id, address=d.client_address)
                data, extras, problems = widgets.client_form(
                    f"mc{d.id}", widgets.client_values(current, formconfig.get_values("client", d.client_id)))
                if st.form_submit_button(t("Save client")):
                    if problems:
                        for text in problems:
                            st.error(text)
                    else:
                        def _save_client() -> None:
                            inv.update_client(actor(), d.client_id, data)
                            formconfig.save_values(actor(), "client", d.client_id, extras)

                        _run(_save_client, t("Client saved"))


# ------------------------------------------------------------------------ entry
def render(current: pd.DataFrame, stamp: tuple) -> None:
    with st.container(border=True):
        card_header(t("Invoice actions"), t("Pick an invoice to review payments, download, message, email or manage it"))
        if current.empty:
            empty_state(t("No invoices match the current filters."), 90)
            return
        ordered = current.sort_values("issue_date", ascending=False)
        labels = {int(r.id): f"{r.number}  |  {r.client}  |  {money(r.total)}  |  {t(r.display_status)}"
                  for r in ordered.itertuples()}
        # Keep the selection valid: restore it from the URL once, and drop it if a filter
        # removed that invoice (a stale value would make the selectbox raise).
        wanted = st.session_state.pop("_want_invoice", None)
        if wanted in labels:
            st.session_state["action_invoice"] = wanted
        elif st.session_state.get("action_invoice") not in labels:
            st.session_state.pop("action_invoice", None)
        invoice_id = st.selectbox(t("Invoice"), list(labels), format_func=lambda i: labels.get(i, str(i)), key="action_invoice")
        detail = inv.get_invoice_detail(invoice_id)
        if detail is None:
            st.warning(t("That invoice no longer exists."))
            return
        status = detail.display_status(date.today())
        st.markdown('<div class="invoice-strip">'
                    f'<span><span class="k">{t("Client")}</span><b>{escape(detail.client_name)}</b></span>'
                    f'<span><span class="k">{t("Issued")}</span><b>{fdate(detail.issue_date, "%b %d, %Y")}</b></span>'
                    f'<span><span class="k">{t("Due")}</span><b>{fdate(detail.due_date, "%b %d, %Y")}</b></span>'
                    f'<span><span class="k">{t("Total")}</span><b>{money_html(detail.total, 2)}</b></span>'
                    f'<span><span class="k">{t("Balance")}</span><b>{money_html(detail.balance, 2)}</b></span>'
                    f'{badge(t(status), STATUS_TONE.get(status, "neutral"))}</div>', unsafe_allow_html=True)
        saved = formconfig.get_values("invoice", detail.id)
        pdf_extras = [(cz.field_label(f, t), cz.display_value(f, saved.get(f["key"])))
                      for f in ui.form_fields("invoice") if f.get("pdf") and cz.display_value(f, saved.get(f["key"]))]
        pdf = build_invoice_pdf(detail, settings(), extras=pdf_extras, lang=current_lang()) if REPORTLAB_AVAILABLE else b""
        t1, t2, t3, t4 = st.tabs([t("🧾 Overview & payments"), t("📄 PDF & WhatsApp"), t("✉️ Email"), t("⚙️ Manage")])
        with t1:
            _overview(detail)
        with t2:
            _share(detail, pdf)
        with t3:
            _email(detail)
        with t4:
            _manage(detail)
