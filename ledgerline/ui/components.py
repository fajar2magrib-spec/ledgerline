"""Shared UI helpers: cached settings, money formatting, cards, session access."""

from __future__ import annotations

import inspect
import logging
import uuid
from html import escape
from typing import Optional, Sequence

import pandas as pd
import streamlit as st

from .. import formatting
from ..events import Actor
from ..i18n import t, tr_message
from ..security import has_permission
from ..services import analytics, formconfig
from ..services.expenses import load_expenses_frame
from ..services.settings import AppSettings, get_logo, get_settings, is_setup_done

# Page names (single source of truth for navigation).
PAGE_DASHBOARD = "📊 Business Performance"
PAGE_INVOICE = "➕ New Invoice"
PAGE_CLIENTS = "👥 Clients"
PAGE_PRODUCTS = "🏷️ Products"
PAGE_PAYMENTS = "💳 Payments"
PAGE_EXPENSES = "🧮 Expenses"
PAGE_REPORTS = "📑 Reports"
PAGE_HELP = "💡 Help & Guide"
PAGE_ADMIN = "🛡️ Administration"
PAGE_CUSTOMIZE = "🎨 Customize"
PAGE_BANKING = "🏦 Banking"


@st.cache_data(ttl=60, show_spinner=False)
def settings() -> AppSettings:
    """Business settings, cached. Cleared by ``invalidate()`` whenever data is saved."""
    return get_settings()


@st.cache_data(ttl=300, show_spinner=False)
def logo_bytes():
    """The company logo (PNG/JPG bytes) or None. Cleared by ``invalidate()``."""
    try:
        return get_logo()
    except Exception:  # noqa: BLE001  a missing logo must never break a page
        return None


@st.cache_data(ttl=30, show_spinner=False)
def form_fields(entity: str) -> list:
    """The Client / Invoice form layout chosen on the Customize page (cached; cleared by ``invalidate()``)."""
    try:
        return formconfig.get_fields(entity)
    except Exception:  # noqa: BLE001  a layout problem must never block the forms
        from .. import customization
        return customization.resolve_fields(entity, None)


@st.cache_data(ttl=30, show_spinner=False)
def form_lists() -> dict:
    """Dropdown lists (categories, payment methods ...) chosen on the Customize page."""
    try:
        return formconfig.get_lists()
    except Exception:  # noqa: BLE001
        from .. import customization
        return customization.get_lists(None)


@st.cache_data(ttl=30, show_spinner=False)
def theme_cfg() -> dict:
    try:
        return formconfig.get_theme()
    except Exception:  # noqa: BLE001
        from .. import customization
        return customization.sanitize_theme(None)


@st.cache_data(ttl=30, show_spinner=False)
def setup_done() -> bool:
    try:
        return is_setup_done()
    except Exception:  # noqa: BLE001
        return True  # if in doubt do not block the app behind the wizard


@st.cache_data(ttl=30, show_spinner=False)
def cached_expenses() -> pd.DataFrame:
    """All general expenses (short cache: the data stamp does not cover this table)."""
    return load_expenses_frame()


def invalidate() -> None:
    """Drop every cached query after a write so all screens show fresh numbers."""
    st.cache_data.clear()


def actor() -> Actor:
    """The signed-in user (set by auth_ui). Only call this after authentication."""
    return st.session_state["user"]


def can(permission: str) -> bool:
    user = st.session_state.get("user")
    return bool(st.session_state.get("authenticated") and user and has_permission(user.role, permission))


@st.cache_data(ttl=3, show_spinner=False)
def stamp() -> tuple:
    """Data fingerprint, cached for 3 s so a burst of reruns costs one query. Cleared by
    ``invalidate()`` after any write, so your own changes are visible immediately."""
    return analytics.data_stamp()


def money(value) -> str:
    return formatting.money(value, settings().currency, 2)


def money_html(value, decimals: int = 0) -> str:
    return formatting.money_html(value, settings().currency, decimals)


def button_width() -> dict:
    """Full-width button kwargs that work on old and new Streamlit versions."""
    if "width" in inspect.signature(st.download_button).parameters:
        return {"width": "stretch"}
    return {"use_container_width": True}


def plural(n: int, word: str) -> str:
    """"1 invoice", "2 invoices"."""
    return f"{n:,} " + (t(word) if n == 1 else t(word + "s"))


def page_header(title: str, subtitle: str = "") -> None:
    """Same big title used by every page (matches the dashboard)."""
    st.markdown(f'<div class="dash-header"><div><h1 class="dash-title">{escape(title)}</h1>'
                f'<div class="dash-sub">{escape(subtitle)}</div></div></div>', unsafe_allow_html=True)


def kpi_row(cards: Sequence[str]) -> None:
    """Lay out ready-made ``kpi_card`` HTML strings in equal columns."""
    for col, html in zip(st.columns(len(cards), gap="medium"), cards):
        col.markdown(html, unsafe_allow_html=True)


def go_to(page: str) -> None:
    """Navigate from a button: the sidebar applies ``_goto`` on the next run."""
    st.session_state["_goto"] = page
    st.rerun()


def card_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<p class="card-title">{title}</p><p class="card-sub">{subtitle}</p>', unsafe_allow_html=True)


def empty_card(icon: str, title: str, text: str, compact: bool = False) -> None:
    """Padded, centred empty/fallback state with consistent typography."""
    st.markdown(f'<div class="empty-card{" empty-card--chart" if compact else ""}">'
                f'<div class="empty-card__icon">{icon}</div><p class="empty-card__title">{title}</p>'
                f'<p class="empty-card__text">{text}</p></div>', unsafe_allow_html=True)


def empty_state(message: str, height: int = 340) -> None:
    """Compact variant used inside chart cards."""
    empty_card("📭", t("Nothing to show"), message, compact=True)


def badge(text: str, tone: str) -> str:
    return f'<span class="badge badge--{tone}">{text}</span>'


STATUS_TONE = {"Paid": "up", "Partially Paid": "neutral", "Pending": "warn", "Overdue": "down", "Cancelled": "neutral"}


PILL_TONE = {"Paid": "up", "Partially Paid": "info", "Pending": "warn", "Overdue": "down", "Cancelled": "neutral"}
_AVATARS = ("#2563EB", "#0F9D74", "#D97706", "#7C3AED", "#E11D48", "#0891B2")


def status_pill(status: str) -> str:
    """Coloured pill for an invoice status (safe: the status is escaped)."""
    return f'<span class="pill pill--{PILL_TONE.get(status, "neutral")}">{escape(t(str(status)))}</span>'


def who_cell(name: str) -> str:
    """Initials avatar + name; the colour is stable per name."""
    clean = str(name or "").strip() or "?"
    initials = "".join(w[0] for w in clean.split()[:2]).upper() or "?"
    colour = _AVATARS[sum(map(ord, clean)) % len(_AVATARS)]
    return (f'<span class="who"><span class="who__av" style="background:{colour}">{escape(initials)}</span>'
            f'<span class="strong">{escape(clean)}</span></span>')


def html_table(headers: Sequence[str], rows: Sequence[Sequence[str]], numeric: Sequence[int] = (),
               max_height: Optional[int] = None, empty: str = "") -> None:
    """Render a styled table. Cells must already be HTML-safe (use ``escape``, ``who_cell``,
    ``status_pill``). ``numeric`` lists the column indexes to right-align."""
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{escape(h)}</th>' for i, h in enumerate(headers))
    if rows:
        body = "".join("<tr>" + "".join(f'<td class="{"num" if i in numeric else ""}">{c}</td>'
                                         for i, c in enumerate(r)) + "</tr>" for r in rows)
        table = f'<table class="lg-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    else:
        table = f'<div class="lg-table-empty">{escape(empty or t("Nothing to show"))}</div>'
    style = f' style="max-height:{int(max_height)}px"' if max_height else ""
    st.markdown(f'<div class="lg-table-wrap"{style}>{table}</div>', unsafe_allow_html=True)


def kpi_card(label: str, value: str, icon: str, accent: str, badge_text: str, badge_tone: str, hint: str) -> str:
    return (
        f'<div class="kpi" style="--accent:{accent}">'
        f'<div class="kpi__top"><span class="kpi__label">{label}</span><span class="kpi__icon">{icon}</span></div>'
        f'<div class="kpi__value">{value}</div>'
        f'<div class="kpi__foot"><span class="badge badge--{badge_tone}">{badge_text}</span>'
        f'<span class="kpi__hint">{hint}</span></div></div>'
    )


def flash(message: str) -> None:
    """Show a message in the middle of the screen after the next full rerun."""
    st.session_state["_flash"] = message


_ICONS = {"success": "✅", "error": "⚠️", "warning": "⚠️"}


def popup(message: str, kind: str = "success", seconds: float = 4.0) -> None:
    """A message in the CENTRE of the screen that fades away by itself (pure CSS, no clicking needed)."""
    n = st.session_state["_popup_n"] = st.session_state.get("_popup_n", 0) + 1
    kind = kind if kind in _ICONS else "success"
    st.markdown(
        f"<style>@keyframes lgpop{n}{{0%{{opacity:0;transform:translate(-50%,-44%) scale(.94)}}"
        f"7%{{opacity:1;transform:translate(-50%,-50%) scale(1)}}86%{{opacity:1;transform:translate(-50%,-50%) scale(1)}}"
        f"100%{{opacity:0;transform:translate(-50%,-50%) scale(.98);visibility:hidden}}}}"
        f".lg-popup-{n}{{animation:lgpop{n} {seconds}s ease forwards}}</style>"
        f'<div class="lg-popup lg-popup--{kind} lg-popup-{n}" role="alert"><span class="lg-popup__icon">{_ICONS[kind]}</span>'
        f'<span class="lg-popup__text">{escape(message)}</span></div>', unsafe_allow_html=True)


def show_errors(messages: Sequence[str]) -> None:
    """Tell the person clearly what to fix: a red box under the button plus a short centred notice."""
    texts = [m for m in messages if m]
    if not texts:
        return
    if len(texts) == 1:
        st.error(texts[0])
    else:
        st.error(t("Please fix the following:") + "\n\n" + "\n".join(f"- {m}" for m in texts))
    popup(texts[0] if len(texts) == 1 else t("Please check the form: {n} things need your attention.", n=len(texts)), "error", 5.5)


def tr_error(text: str) -> str:
    return tr_message(str(text))


def fail(exc: BaseException) -> None:
    """Show ANY exception from a save/update in plain words. Business-rule errors are shown as they are
    (translated); anything unexpected is logged with a reference and never shown as a crash."""
    errors = getattr(exc, "errors", None)
    name = type(exc).__name__
    if name in ("StaleDataError", "ConcurrencyError"):
        show_errors([t("Someone else changed this record. Reload the page and try again.")])
    elif isinstance(errors, list) and errors:
        show_errors([tr_error(e) for e in errors])
    elif type(exc).__module__.startswith("ledgerline") or isinstance(exc, ValueError):
        show_errors([tr_error(str(exc))])
    else:
        ref = uuid.uuid4().hex[:8]
        logging.getLogger(__name__).exception("Unexpected error while saving [%s]", ref)
        show_errors([t("Could not save. Nothing was changed, please try again."),
                     f"{t('Reference')} {ref} ({name})"])
