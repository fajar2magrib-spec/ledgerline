"""End-to-end UI tests using Streamlit's AppTest (no browser needed).

They focus on the stability problems that matter most to end clients: reload loops,
state surviving navigation and refresh, graceful database failure, role-correct screens.
"""

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from streamlit.testing.v1 import AppTest

from ledgerline.services import users

APP = str(Path(__file__).resolve().parent.parent / "app.py")
OWNER, PASSWORD = "owner", "Strong-pass-42"


@pytest.fixture()
def ui(env, monkeypatch):
    from ledgerline.ui import auth_ui
    cookie = {"value": None}  # what the "browser" sent when the tab connected
    monkeypatch.setattr(auth_ui, "_browser_cookie",
                        lambda: None if cookie["value"] in (None, auth_ui.st.session_state.get("_ignore_cookie")) else cookie["value"])
    queued = []
    real_queue = auth_ui.queue_cookie
    monkeypatch.setattr(auth_ui, "queue_cookie", lambda op, token="": (queued.append(op), real_queue(op, token))[1])
    return type("UI", (), {"cookie": cookie, "queued": queued})


def run():
    return AppTest.from_file(APP, default_timeout=120).run()


def btn(at, label):
    return next(b for b in at.button if b.label == label)


def exc(at):
    return [e.value[:160] for e in at.exception]


def signed_in(user=OWNER, password=PASSWORD):
    at = run()
    if at.text_input and at.text_input[0].key == "setup_user":
        at.text_input(key="setup_user").set_value(user)
        at.text_input(key="setup_pass").set_value(password)
        at.text_input(key="setup_pass2").set_value(password)
        btn(at, "Create administrator").click().run()
    else:
        at.text_input(key="login_user").set_value(user)
        at.text_input(key="login_pass").set_value(password)
        btn(at, "Sign in").click().run()
    skip = [b for b in at.button if b.label == "Skip for now"]   # first-run business wizard (admins only)
    if skip:
        skip[0].click().run()
    return at


def test_strict_session_state_defaults(env, ui):
    at = run()
    ss = at.session_state
    assert ss["authenticated"] is False and ss["user"] is None and ss["auth_token"] is None


def test_first_run_setup_then_signed_in(env, ui):
    at = signed_in()
    assert exc(at) == []
    assert at.session_state["authenticated"] is True and at.session_state["user"].role == "admin"
    assert at.radio(key="nav_page").value == "📊 Business Performance"
    assert any("No invoices yet" in m.value for m in at.markdown)  # padded empty-state card


def test_stale_cookie_does_not_loop(env, ui):
    """A rejected/expired cookie used to be re-read every rerun, queueing a delete each time."""
    signed_in()
    ui.cookie["value"] = "not.a.valid.token"
    ui.queued.clear()
    at = run()
    for _ in range(4):
        at.run()
    assert ui.queued.count("delete") == 1, ui.queued  # queued exactly once, not on every rerun
    assert at.session_state["authenticated"] is False and len(at.text_input) == 2  # login page, calm


def test_logout_with_handshake_cookie_does_not_loop(env, ui):
    at = signed_in()
    ui.cookie["value"] = at.session_state["auth_token"]  # the tab was loaded with this cookie
    at = run()
    assert at.session_state["authenticated"] is True  # refresh restores the session from the cookie
    ui.queued.clear()
    at.sidebar.button[[b.label for b in at.sidebar.button].index("Sign out")].click().run()
    for _ in range(3):
        at.run()
    assert at.session_state["authenticated"] is False
    assert ui.queued.count("delete") == 1, ui.queued
    assert run().session_state["authenticated"] is False  # revoked token can never come back


def test_filters_survive_page_navigation_and_url_restores_view(env, ui):
    from ledgerline.services import demo
    at = signed_in()
    admin = at.session_state["user"]
    demo.seed_demo_data(admin, 40)
    import streamlit as st
    st.cache_data.clear()  # data written outside the UI: drop the 3-second stamp cache the UI would normally clear
    at = run()  # fresh session (cookie-less AppTest keeps no login, so log in again)
    at = signed_in()
    at.selectbox(key="period").select("All Time").run()
    at.text_input(key="query").set_value("Quill").run()
    at.radio(key="nav_page").set_value("➕ New Invoice").run()
    assert exc(at) == []
    at.radio(key="nav_page").set_value("📊 Business Performance").run()
    assert at.selectbox(key="period").value == "All Time" and at.text_input(key="query").value == "Quill"
    qp = lambda key: [at.query_params.get(key)] if isinstance(at.query_params.get(key), str) else at.query_params.get(key)  # AppTest: list or str by version
    assert qp("period") == ["All Time"] and qp("q") == ["Quill"]

    # "browser refresh": brand-new session with those query params in the URL
    fresh = AppTest.from_file(APP, default_timeout=120)
    fresh.query_params["period"], fresh.query_params["q"], fresh.query_params["page"] = "All Time", "Quill", "dashboard"
    fresh.run()
    fresh.text_input(key="login_user").set_value(OWNER); fresh.text_input(key="login_pass").set_value(PASSWORD)
    btn(fresh, "Sign in").click().run()
    assert fresh.selectbox(key="period").value == "All Time" and fresh.text_input(key="query").value == "Quill"


def test_tampered_url_cannot_crash(env, ui):
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params.update({"period": "Bogus", "start": "nope", "end": "2999-01-01", "status": "x,y", "invoice": "abc", "page": "../etc"})
    at.run()
    at = signed_in() if at.text_input and at.text_input[0].key.startswith("setup") else at
    assert exc(at) == []


def test_database_failure_shows_friendly_card_and_keeps_sidebar(env, ui, monkeypatch):
    from ledgerline.services import analytics, demo
    at = signed_in()
    demo.seed_demo_data(at.session_state["user"], 10)
    # data written outside the UI: drop the 3-second stamp cache the UI would normally clear
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setattr(analytics, "load_invoice_frame",
                        lambda today: (_ for _ in ()).throw(OperationalError("SELECT", {}, Exception("disk I/O error"))))
    at = signed_in()
    html = " ".join(m.value for m in at.markdown)
    assert "error-card" in html and "temporarily unavailable" in html
    assert "Traceback" not in html and exc(at) == []
    assert len(at.radio) == 1  # navigation is still there


def test_currency_change_is_stable(env, ui):
    at = signed_in()
    at.sidebar.selectbox(key="sidebar_currency").select("PKR").run()
    assert exc(at) == [] and at.sidebar.selectbox(key="sidebar_currency").value == "PKR"
    at.run(); at.run()
    assert at.sidebar.selectbox(key="sidebar_currency").value == "PKR"  # no bounce back to USD


def test_viewer_sees_limited_navigation(env, ui):
    signed_in()
    admin = users.list_users()[0].actor()
    users.create_user(admin, "vera", "Viewer-pass-77", "viewer")
    at = signed_in("vera", "Viewer-pass-77")
    assert exc(at) == [] and at.radio(key="nav_page").options == ["📊 Business Performance"]
    assert not [s for s in at.sidebar.selectbox if s.key == "sidebar_currency"]  # no currency control for viewers (the language picker is for everyone)


def test_stylesheet_keeps_sidebar_toggle_visible():
    from ledgerline.ui import styles
    css = styles._stylesheet()
    assert "stExpandSidebarButton" in css and "stSidebarCollapseButton" in css
    assert "stToolbar" not in css and "display: none !important; }" not in css.split("Page chrome")[1].split("2. Sidebar")[0].replace("footer, #MainMenu { display: none !important; }", "")

