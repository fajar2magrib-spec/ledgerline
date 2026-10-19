"""Products page: your price list. Pick items from it when you create an invoice."""

from __future__ import annotations

from decimal import Decimal
from html import escape

import streamlit as st

from ..i18n import t
from ..services import catalog
from .components import fail
from .components import (form_lists, actor, can, card_header, empty_card, flash, html_table, invalidate, kpi_card, kpi_row, money_html,
                         page_header, plural, settings)


def _run(action, success: str) -> None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        fail(exc)
        return
    invalidate()
    flash(success)
    st.rerun()


def _fields(prefix: str, p: catalog.ProductView | None, default_tax: Decimal):
    """The product form fields (shared by Add and Edit). Returns the values in ProductInput order."""
    a, b, c = st.columns([2, 1, 1], gap="medium")
    name = a.text_input(t("Product or service name *"), value=p.name if p else "", max_chars=160, key=f"{prefix}_name")
    sku = b.text_input(t("SKU / code (optional)"), value=p.sku if p else "", max_chars=40, key=f"{prefix}_sku")
    cats = list(form_lists()["product_categories"])
    if p and p.category not in cats:
        cats.append(p.category)
    category = c.selectbox(t("Category"), cats, format_func=t, index=cats.index(p.category) if p else 0, key=f"{prefix}_cat")
    d, e, f = st.columns([1, 1, 2], gap="medium")
    price = d.number_input(t("Price *"), min_value=0.0, step=1.0, format="%.2f", value=float(p.unit_price) if p else 0.0, key=f"{prefix}_price")
    tax = e.number_input(t("Tax %"), min_value=0.0, max_value=100.0, step=1.0, value=float(p.tax_rate if p else default_tax), key=f"{prefix}_tax")
    desc = f.text_input(t("Short description (optional)"), value=p.description if p else "", key=f"{prefix}_desc")
    return catalog.ProductInput(name, Decimal(str(price)), Decimal(str(tax)), sku, category, desc)


def _add(default_tax: Decimal) -> None:
    n = st.session_state.setdefault("product_form_nonce", 0)
    with st.expander(t("➕ Add a product or service"), expanded=False):
        with st.form(f"add_product_{n}", border=False):
            data = _fields(f"np{n}", None, default_tax)
            if st.form_submit_button(t("Save product"), type="primary"):
                def _save() -> None:
                    catalog.create_product(actor(), data)
                    st.session_state["product_form_nonce"] = n + 1

                _run(_save, t("{v1} added to your products", v1=data.name.strip()))


def _edit(p: catalog.ProductView, default_tax: Decimal) -> None:
    with st.container(border=True):
        card_header(escape(p.name), t("Edit this product"))
        with st.form(f"edit_product_{p.id}", border=False):
            data = _fields(f"ep{p.id}", p, default_tax)
            save = st.form_submit_button(t("Save changes"), type="primary")
        if save:
            _run(lambda: catalog.update_product(actor(), p.id, data), t("Product saved"))
        label = t("♻️ Restore (show it on invoices again)") if not p.is_active else t("🗄️ Archive (hide it from the invoice list)")
        if st.button(label, key=f"arch_{p.id}"):
            _run(lambda: catalog.set_active(actor(), p.id, not p.is_active), t("Product restored") if not p.is_active else t("Product archived"))


def render() -> None:
    page_header(t("Products"), t("Your price list. Add an item to an invoice in one click."))
    cfg = settings()
    products = catalog.list_products(active_only=False)
    active = [p for p in products if p.is_active]

    if not products:
        empty_card("🏷️", t("No products yet"), t("Add the things you sell (a template, a course, a monthly plan) with their price. "
                   "When you create an invoice you can pick them instead of typing again."))
        if can("product.manage"):
            _add(cfg.default_tax_rate)
        return

    avg = sum(float(p.unit_price) for p in active) / len(active) if active else 0.0
    kpi_row([
        kpi_card(t("Active products"), f"{len(active):,}", "🏷️", "#2563EB", plural(len(products) - len(active), "archived item"), "neutral", t("on your list")),
        kpi_card(t("Average price"), money_html(avg, 2), "💵", "#0F9D74", t("Active items"), "neutral", t("per item")),
        kpi_card(t("Categories"), f"{len({p.category for p in active}):,}", "🗂️", "#7C3AED", t("In use"), "neutral", t("groups")),
    ])

    if can("product.manage"):
        _add(cfg.default_tax_rate)
    query = st.text_input(t("Search products"), placeholder=t("Search by name, code or category"), key="product_query", label_visibility="collapsed")
    shown = products
    if query.strip():
        q = query.strip().lower()
        shown = [p for p in products if q in p.name.lower() or q in p.sku.lower() or q in p.category.lower()]

    with st.container(border=True):
        card_header(t("Price list"), t("{v1:,} of {v2:,} items", v1=len(shown), v2=len(products)))
        html_table([t("Product"), t("Code"), t("Category"), t("Price"), f"{cfg.tax_label} %", t("Status")],
                   [[f'<span class="strong">{escape(p.name)}</span>' + (f'<br><span class="muted">{escape(p.description)}</span>' if p.description else ""),
                     f'<span class="muted">{escape(p.sku or "-")}</span>', f'<span class="pill pill--info">{escape(t(p.category))}</span>',
                     money_html(p.unit_price, 2), f"{float(p.tax_rate):g}%",
                     f'<span class="pill pill--{"up" if p.is_active else "neutral"}">{t("Active") if p.is_active else t("Archived")}</span>']
                    for p in shown], numeric=(3, 4), max_height=420, empty=t("No products match your search."))

    if shown and can("product.manage"):
        by_id = {p.id: p for p in shown}
        if st.session_state.get("product_pick") not in by_id:
            st.session_state.pop("product_pick", None)
        pick = st.selectbox(t("Edit a product"), list(by_id), format_func=lambda i: by_id[i].name, key="product_pick")
        _edit(by_id[pick], cfg.default_tax_rate)
