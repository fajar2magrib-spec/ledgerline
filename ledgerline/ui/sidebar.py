"""Sidebar: navigation (by role), admin currency control, backup, current user, sign out."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from html import escape

import streamlit as st

from ..i18n import language_picker, t
from ..db import database_info
from ..events import Actor
from ..formatting import CURRENCIES
from ..security import MIN_PASSWORD_LENGTH
from ..services import audit, users
from ..services.settings import save_settings
from . import auth_ui
from .components import (PAGE_BANKING, PAGE_ADMIN, PAGE_CLIENTS, PAGE_CUSTOMIZE, PAGE_DASHBOARD, PAGE_EXPENSES, PAGE_HELP, PAGE_INVOICE, PAGE_PAYMENTS,
                         PAGE_PRODUCTS, PAGE_REPORTS, button_width, can, fail, flash, invalidate, logo_bytes, settings)


@st.cache_data(show_spinner=False)
def _backup(stamp: tuple, _actor: Actor) -> bytes:
    return audit.export_invoices_csv(_actor)


def _save_currency(actor: Actor) -> None:
    problems = save_settings(actor, replace(settings(), currency=st.session_state["sidebar_currency"]))
    if problems:
        st.session_state["_sidebar_error"] = " ".join(problems)
    invalidate()


def render(actor: Actor, stamp: tuple, invoice_count: int) -> str:
    staff = can("client.edit")  # managers and admins; a viewer only gets the dashboard (see README "Roles")
    pages = [PAGE_DASHBOARD]
    if can("invoice.create"):
        pages.append(PAGE_INVOICE)
    if staff:
        pages += [PAGE_CLIENTS, PAGE_PRODUCTS, PAGE_PAYMENTS]
    if can("expense.manage"):
        pages.append(PAGE_EXPENSES)
    if can("bank.manage"):
        pages.append(PAGE_BANKING)
    if staff:
        pages += [PAGE_REPORTS, PAGE_HELP]
    if can("settings.manage"):
        pages.append(PAGE_CUSTOMIZE)
    if can("user.manage"):
        pages.append(PAGE_ADMIN)

    # Navigation requests from buttons are applied BEFORE the radio is created (Streamlit
    # forbids changing a widget's value after it has been drawn in the same run).
    goto = st.session_state.get("_goto")
    st.session_state["_goto"] = None
    if goto in pages:
        st.session_state["nav_page"] = goto
    if st.session_state.get("nav_page") not in pages:
        st.session_state["nav_page"] = PAGE_DASHBOARD

    cfg = settings()
    with st.sidebar:
        logo = logo_bytes()
        if logo:
            st.image(logo, width=150)
        st.markdown(f'<p class="side-brand">{escape(cfg.company_name)}</p><p class="side-sub">{t("Invoices and cash flow")}</p>',
                    unsafe_allow_html=True)
        page = st.radio(t("Navigation"), pages, key="nav_page", label_visibility="collapsed", format_func=t)
        language_picker("sidebar")

        if can("settings.manage"):
            # Keep the widget in step with the saved setting (it may be changed under
            # Administration). Saving happens in a callback, BEFORE the rerun, so there is no
            # compare-then-rerun cycle that could bounce between two values.
            if st.session_state.get("_currency_seen") != cfg.currency:
                st.session_state["sidebar_currency"] = cfg.currency
                st.session_state["_currency_seen"] = cfg.currency
            st.selectbox(t("Currency"), list(CURRENCIES), key="sidebar_currency", on_change=_save_currency, args=(actor,),
                         format_func=lambda c: f"{c}  ({CURRENCIES[c]})",
                         help=t("Company-wide. Changes the symbol only; amounts are not converted."))
            if st.session_state.get("_sidebar_error"):
                st.error(st.session_state.pop("_sidebar_error"))
        else:
            st.caption(t("Currency: {currency} ({v2})", currency=cfg.currency, v2=CURRENCIES[cfg.currency]))

        info = database_info()
        count_text = t("{n} invoices", n=f"{invoice_count:,}")
        st.markdown(f'<div class="side-card"><b>{t("Data source")}</b>{escape(info["backend"])}, {count_text}</div>',
                    unsafe_allow_html=True)
        if can("data.export"):
            st.download_button(t("Backup invoices (CSV)"), data=_backup(stamp, actor),
                               file_name=f"ledgerline_invoices_{date.today():%Y-%m-%d}.csv", mime="text/csv",
                               key="backup_csv", **button_width())
        st.markdown(f'<div class="side-user">{t("Signed in as")} <b>{escape(actor.username)}</b>'
                    f'<span class="role-chip">{escape(t(actor.role))}</span></div>', unsafe_allow_html=True)
        _change_password_box(actor)
        if st.button(t("Sign out"), key="logout", **button_width()):
            auth_ui.logout()
        if not auth_ui.cookies_supported():
            st.caption(t("Sessions end on refresh: install extra-streamlit-components."))
    return page


def _change_password_box(actor: Actor) -> None:
    """Every user (viewer, manager, admin) can change their own password."""
    with st.expander(t("Change my password"), expanded=False):
        with st.form("change_own_password", clear_on_submit=True, border=False):
            current = st.text_input(t("Current password"), type="password", key="pw_current", autocomplete="current-password")
            new = st.text_input(t("Password (min {MIN_PASSWORD_LENGTH} characters)", MIN_PASSWORD_LENGTH=MIN_PASSWORD_LENGTH),
                                type="password", key="pw_new", autocomplete="new-password")
            again = st.text_input(t("Repeat password"), type="password", key="pw_new2", autocomplete="new-password")
            submitted = st.form_submit_button(t("Change password"), **button_width())
        if submitted:
            if new != again:
                st.error(t("The passwords do not match."))
                return
            try:
                users.change_own_password(actor, current, new, keep_jti=st.session_state.get("auth_jti"))
            except (users.UserError, PermissionError) as exc:
                fail(exc)
            else:
                flash(t("Password changed. Your other devices were signed out."))
                st.rerun()


def render_minimal(actor: Actor) -> None:
    """Fallback sidebar used when the database is unreachable: the user can still sign out."""
    with st.sidebar:
        st.markdown(f'<p class="side-brand">Ledgerline</p><p class="side-sub">{t("Reconnecting to the database...")}</p>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="side-user">{t("Signed in as")} <b>{escape(actor.username)}</b></div>', unsafe_allow_html=True)
        if st.button(t("Sign out"), key="logout_min", **button_width()):
            auth_ui.logout()
