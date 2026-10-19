"""First-run wizard (admins only): business name, currency, tax, logo and brand colour."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import streamlit as st

from ..i18n import t
from ..formatting import CURRENCIES
from ..security import PermissionDenied
from ..services import settings as svc
from .components import tr_error
from .components import actor, flash, invalidate, page_header, settings


def finish(skip: bool = False) -> None:
    svc.mark_setup_done(actor())
    invalidate()
    flash(t("You are all set. Welcome!") if not skip else t("Setup skipped. You can change settings in Administration."))
    st.rerun()


def render() -> None:
    page_header(t("Welcome! Let's set up your business"),
                t("One minute, once. It appears on your invoices. You can change everything later in Administration → Settings."))
    cfg = settings()
    with st.container(border=True):
        with st.form("setup_form", border=False):
            a, b = st.columns(2, gap="large")
            name = a.text_input(t("Business name *"), value=cfg.company_name, max_chars=120)
            email = b.text_input(t("Business email"), value=cfg.company_email, placeholder=t("billing@yourbusiness.com"))
            address = a.text_area(t("Address (printed on invoices)"), value=cfg.company_address, height=96)
            with b:
                logo = st.file_uploader(t("Logo (PNG or JPG, under 500 KB)"), type=["png", "jpg", "jpeg"])
                brand = st.color_picker(t("Brand colour (invoice header)"), value=cfg.brand_color,
                                        help=t("Pick a medium or dark colour: the invoice title is printed in white on it."))
            c1, c2, c3, c4 = st.columns(4, gap="medium")
            currency = c1.selectbox(t("Currency"), list(CURRENCIES), index=list(CURRENCIES).index(cfg.currency),
                                    format_func=lambda c: f"{c}  ({CURRENCIES[c]})")
            tax_label = c2.text_input(t("Tax name"), value=cfg.tax_label, help=t("VAT, GST, Sales tax ..."))
            tax_rate = c3.number_input(t("Usual tax %"), min_value=0.0, max_value=100.0, step=1.0, value=float(cfg.default_tax_rate))
            terms = c4.number_input(t("Days to pay"), min_value=0, max_value=365, value=int(cfg.payment_terms_days),
                                    help=t("Invoices are due this many days after the issue date."))
            done, skip = st.columns([2, 1], gap="medium")
            finish_clicked = done.form_submit_button(t("✅ Finish setup"), type="primary")
            skip_clicked = skip.form_submit_button(t("Skip for now"))
    if skip_clicked:
        finish(skip=True)
    if finish_clicked:
        new = replace(cfg, company_name=name.strip(), company_email=email.strip(), company_address=address.strip(), brand_color=brand,
                      currency=currency, tax_label=tax_label.strip() or "Tax", default_tax_rate=Decimal(str(tax_rate)),
                      payment_terms_days=int(terms))
        try:
            problems = svc.save_settings(actor(), new)
            if not problems and logo is not None:
                problems = svc.save_logo(actor(), logo.getvalue())
        except PermissionDenied as exc:
            problems = [str(exc)]
        if problems:
            for text in problems:
                st.error(tr_error(text))
        else:
            finish()
