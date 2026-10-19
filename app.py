"""Ledgerline entry point.      Run:  python -m streamlit run app.py

Order of work on EVERY rerun (kept deliberately linear so there are no hidden loops):

  1. set_page_config   must be the first Streamlit call; sidebar starts EXPANDED
  2. init_session_state  every key the app reads exists before anything uses it
  3. CSS injection       one stylesheet, same position every run (no flash, no shift)
  4. start services      cached once per server process (schema, admin bootstrap, outbox)
  5. authentication      session -> cookie -> login/setup page (see ui/auth_ui.py)
  6. restore the view    page + filters from the URL, only on a fresh browser session
  7. sidebar + page      the page body sits inside an error boundary
  8. sync the URL        so a refresh or bookmark returns to the same view

Only step 5 may call st.rerun(), and only once per sign-in/sign-out (see auth_ui.py).
"""

import logging
import uuid

import streamlit as st

# 1. Must precede every other Streamlit command.
st.set_page_config(
    page_title="Ledgerline",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",   # sidebar open on first load
)

from ledgerline import bootstrap, i18n  # noqa: E402
from ledgerline.config import ConfigError  # noqa: E402
from ledgerline.events import current_actor  # noqa: E402
from ledgerline.ui import (admin, banking, auth_ui, clients, customize, dashboard, expenses, help_page, invoice_form, payments,  # noqa: E402
                           products, reports, safe, setup, sidebar, state, styles)
from ledgerline.ui.components import (PAGE_BANKING, PAGE_ADMIN, PAGE_CLIENTS, PAGE_CUSTOMIZE, PAGE_EXPENSES, PAGE_HELP, PAGE_INVOICE,  # noqa: E402
                                      PAGE_PAYMENTS, PAGE_PRODUCTS, PAGE_REPORTS, can, popup, setup_done, stamp)

log = logging.getLogger("ledgerline.app")


@st.cache_resource(show_spinner="Starting Ledgerline...")
def start_services() -> bootstrap.BootInfo:
    """Once per server process: create tables, optional admin bootstrap, start the outbox worker.
    If this raises, nothing is cached, so the next run simply tries again."""
    return bootstrap.start()


def _error_card(title: str, detail: str, key: str) -> str:
    ref = uuid.uuid4().hex[:8]
    st.markdown(f'<div class="error-card"><b>{title}</b>{detail}<br><small>Reference {ref}</small></div>',
                unsafe_allow_html=True)
    if st.button("Try again", key=f"retry_{key}"):
        st.cache_data.clear()
        st.rerun()
    return ref


def _boot_ok() -> bool:
    try:
        start_services()
        return True
    except ConfigError as exc:
        _error_card("Configuration problem", f"{exc}", "config")
    except Exception:  # noqa: BLE001  database unreachable, bad DATABASE_URL ...
        log.exception("Startup failed")
        _error_card("Ledgerline could not start.", "The database is not reachable. Check DATABASE_URL and try again.", "boot")
    return False


def _render_page(user) -> None:
    # Never let a database hiccup remove the sidebar: fall back to a minimal one.
    try:
        current_stamp = stamp()
        invoice_count = int(current_stamp[0])
    except Exception:  # noqa: BLE001
        log.exception("Could not read the data stamp")
        sidebar.render_minimal(user)
        with safe.guarded("the application", "stamp"):
            raise RuntimeError("The database is temporarily unavailable.")
        return

    try:
        page = sidebar.render(user, current_stamp, invoice_count)
    except Exception:  # noqa: BLE001
        log.exception("Sidebar failed")
        sidebar.render_minimal(user)
        page = None

    message = st.session_state.pop("_flash", None)
    if message:
        popup(message)               # success message in the middle of the screen

    with safe.guarded("this page", f"page_{page}"):
        if can("settings.manage") and not setup_done():
            setup.render()       # first run: the admin enters business name, currency, logo ...
        elif page == PAGE_INVOICE and can("invoice.create"):
            invoice_form.render()
        elif page == PAGE_CLIENTS and can("client.edit"):
            clients.render()
        elif page == PAGE_PRODUCTS and can("client.edit"):
            products.render()
        elif page == PAGE_PAYMENTS and can("client.edit"):
            payments.render()
        elif page == PAGE_EXPENSES and can("expense.manage"):
            expenses.render()
        elif page == PAGE_BANKING and can("bank.manage"):
            banking.render()
        elif page == PAGE_REPORTS and can("client.edit"):
            reports.render()
        elif page == PAGE_HELP and can("client.edit"):
            help_page.render()
        elif page == PAGE_CUSTOMIZE and can("settings.manage"):
            customize.render()
        elif page == PAGE_ADMIN and can("user.manage"):
            admin.render()
        else:
            dashboard.render()   # a fragment: filter changes rerun only the dashboard
    if page:
        state.sync_query_params(page)


def main() -> None:
    state.init_session_state()                 # 2
    styles.inject()                            # 3
    # Keep the sidebar mounted from the very first paint, even on the login screen, so its
    # expanded/collapsed state is initialised once and never "pops" in after sign-in.
    st.sidebar.markdown('<span class="sidebar-anchor"></span>', unsafe_allow_html=True)
    if not _boot_ok():                         # 4
        return
    try:                                       # language + theme need the database (company defaults)
        from ledgerline.ui.components import settings as _settings
        default_language = _settings().default_language
    except Exception:  # noqa: BLE001
        default_language = "en"
    i18n.init(default_language)
    styles.inject_theme()
    user = auth_ui.require_login()             # 5
    if user is None:
        return
    current_actor.set(user)                    # the audit trail reads "who" from here
    state.hydrate_from_query_params()          # 6
    state.keep_widget_state()
    _render_page(user)                         # 7, 8


try:
    main()
except Exception:  # noqa: BLE001  last line of defence: no stack trace ever reaches a client
    ref = uuid.uuid4().hex[:8]
    log.exception("Unhandled error [%s]", ref)
    st.markdown(f'<div class="error-card"><b>Something went wrong.</b>The error has been logged and your data is safe.'
                f'<br><small>Reference {ref}</small></div>', unsafe_allow_html=True)
    if st.button("Reload", key="fatal_reload"):
        st.cache_data.clear()
        st.rerun()
