"""Session-state discipline: strict defaults, URL-backed persistence, widget-state retention.

Why this module exists
* Strict initialisation: every key the app reads is created up front, once per run, so no
  code path can hit a KeyError or race against a half-initialised session.
* Refresh survival: Streamlit forgets session_state on a browser refresh. Sign-in survives
  via the session cookie (auth_ui.py). THIS module makes the *view* survive: the page,
  filters, search and selected invoice are mirrored into the URL query string and restored
  once when a fresh session starts. A refreshed or bookmarked URL returns to the same view.
* Widget-state retention: Streamlit deletes the state of widgets that are not drawn in a
  run, so filters would reset when you visit another page. ``keep_widget_state`` re-assigns
  them each run, which marks them as retained.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any

import streamlit as st

from ..calc import DISPLAY_STATUSES
from .components import (PAGE_BANKING, PAGE_ADMIN, PAGE_CLIENTS, PAGE_CUSTOMIZE, PAGE_DASHBOARD, PAGE_EXPENSES, PAGE_HELP, PAGE_INVOICE,
                         PAGE_PAYMENTS, PAGE_PRODUCTS, PAGE_REPORTS)

# Authentication keys requested by the spec: authenticated (bool) and user (Actor | None).
DEFAULTS: dict[str, Any] = {
    "authenticated": False,
    "user": None,
    "auth_token": None,
    "auth_jti": None,
    "auth_checked_at": 0.0,
    "_ignore_cookie": None,   # a cookie token we already rejected: never read it again this session
    "_cookie_ops": [],
    "_hydrated": False,
    "_goto": None,
    "form_nonce": 0,
}

SLUGS = {"dashboard": PAGE_DASHBOARD, "invoice": PAGE_INVOICE, "clients": PAGE_CLIENTS, "products": PAGE_PRODUCTS, "payments": PAGE_PAYMENTS,
         "expenses": PAGE_EXPENSES, "reports": PAGE_REPORTS, "help": PAGE_HELP, "admin": PAGE_ADMIN, "customize": PAGE_CUSTOMIZE, "banking": PAGE_BANKING}
SLUG_OF = {v: k for k, v in SLUGS.items()}
PERIODS = ["Today", "This Week", "This Month", "Last 30 Days", "All Time", "Custom Date Range"]
DEFAULT_PERIOD = "Last 30 Days"
MIX_OPTIONS = ["Category", "Payment Method"]
# Widgets whose state must survive visiting other pages.
RETAINED_KEYS = ("period", "custom_range", "statuses", "query", "donut_dim")


def init_session_state() -> None:
    """Create every required key (idempotent; never overwrites an existing value)."""
    for key, default in DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = copy.deepcopy(default)


def keep_widget_state() -> None:
    for key in RETAINED_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def _iso(value: str):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def hydrate_from_query_params() -> None:
    """Restore the view from the URL, once per browser session. Every value is validated,
    so a hand-edited or outdated URL can never crash the app."""
    if st.session_state["_hydrated"]:
        return
    st.session_state["_hydrated"] = True
    qp, ss = st.query_params, st.session_state
    if qp.get("page") in SLUGS:
        ss["_goto"] = SLUGS[qp["page"]]
    if qp.get("period") in PERIODS:
        ss["period"] = qp["period"]
    start, end = _iso(qp.get("start", "")), _iso(qp.get("end", ""))
    today = date.today()
    if start and end and start <= end <= today and start >= today - timedelta(days=5 * 365):
        ss["custom_range"] = (start, end)
    chosen = [s for s in qp.get("status", "").split(",") if s in DISPLAY_STATUSES]
    if chosen:
        ss["statuses"] = chosen
    if qp.get("q"):
        ss["query"] = qp["q"][:80]
    if qp.get("mix") in MIX_OPTIONS:
        ss["donut_dim"] = qp["mix"]
    if qp.get("invoice", "").isdigit():
        ss["_want_invoice"] = int(qp["invoice"])  # applied by actions.py once the options exist


def sync_query_params(page: str) -> None:
    """Mirror the current view into the URL (no rerun is triggered by this)."""
    ss = st.session_state
    wanted: dict[str, str] = {"page": SLUG_OF.get(page, "dashboard")}
    period = ss.get("period", DEFAULT_PERIOD)
    if page != PAGE_DASHBOARD:
        period = DEFAULT_PERIOD  # dashboard filters stay out of other pages' URLs
    if period != DEFAULT_PERIOD:
        wanted["period"] = period
    custom = ss.get("custom_range")
    if period == "Custom Date Range" and isinstance(custom, (tuple, list)) and len(custom) == 2:
        wanted["start"], wanted["end"] = custom[0].isoformat(), custom[1].isoformat()
    lang = ss.get("_lang")
    if lang and lang != ss.get("_lang_default", "en"):
        wanted["lang"] = lang          # keeps the chosen language across refresh and bookmarks
    statuses = [s for s in DISPLAY_STATUSES if s in ss.get("statuses", DISPLAY_STATUSES)]
    if page == PAGE_DASHBOARD and statuses and len(statuses) != len(DISPLAY_STATUSES):
        wanted["status"] = ",".join(statuses)
    if page == PAGE_DASHBOARD and ss.get("query"):
        wanted["q"] = ss["query"]
    if page == PAGE_DASHBOARD and ss.get("donut_dim") in MIX_OPTIONS[1:]:
        wanted["mix"] = ss["donut_dim"]
    if page == PAGE_DASHBOARD and ss.get("action_invoice"):
        wanted["invoice"] = str(ss["action_invoice"])
    try:
        if dict(st.query_params) != wanted:  # write only on change: no needless frontend traffic
            st.query_params.from_dict(wanted)
    except Exception:  # noqa: BLE001  (never let URL syncing break a page)
        pass
