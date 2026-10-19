"""Sign-in, first-run setup and cookie-backed sessions.

How a session survives a browser refresh
1. Login creates a revocable row in ``auth_sessions`` and a signed JWT whose ``jti`` is
   that row's id (services/users.py).
2. The token is written to a browser cookie with ``extra-streamlit-components``.
3. A refresh opens a NEW Streamlit session, but the browser sends the cookie with the
   websocket handshake. ``st.context.cookies`` exposes it on the very first run, with no
   component round trip, and we validate it (signature, expiry, not revoked, user active).
4. Sign out revokes the row and deletes the cookie. Admin actions (role change, disable,
   password reset) revoke sessions too, so they apply on the user's next click.

Limitations to know about (also in README): Streamlit cannot set HttpOnly cookies, so the
cookie is readable by page JavaScript. Mitigations: SameSite=Strict, Secure on HTTPS,
server-side revocation and expiry. For stricter needs put the app behind an identity-aware
proxy or Streamlit's native OIDC login (st.login).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from ..i18n import language_picker, t
from ..config import get_config
from ..events import Actor
from ..security import MIN_PASSWORD_LENGTH
from ..services import users
from . import styles
from .components import fail, tr_error

try:
    import extra_streamlit_components as stx
except ImportError:  # sessions then last only until the page is refreshed
    stx = None


AUTH_RECHECK_SECONDS = 20  # how often a live session is re-verified against the database


def cookies_supported() -> bool:
    return stx is not None


def _browser_cookie() -> Optional[str]:
    """The session cookie sent when this browser tab connected.

    IMPORTANT: ``st.context.cookies`` is a snapshot from the websocket handshake. It does NOT
    change when we later delete the cookie in the browser. Without the ``_ignore_cookie``
    guard, a rejected or signed-out token would be re-read on every rerun, queue another
    delete, rerun again, and loop forever. Once a token is rejected we never read it again.
    """
    try:
        token = st.context.cookies.get(get_config().cookie_name)
    except Exception:  # very old Streamlit without st.context
        return None
    if token and token == st.session_state.get("_ignore_cookie"):
        return None
    return token


def queue_cookie(op: str, token: str = "") -> None:
    st.session_state["_cookie_ops"].append((op, token))


def flush_cookie_ops() -> None:
    """Render the (invisible) cookie component, only when a cookie must change."""
    ops = st.session_state["_cookie_ops"]
    if not ops:
        return
    st.session_state["_cookie_ops"] = []
    if stx is None:
        return
    cfg = get_config()
    seq = st.session_state["_cookie_seq"] = st.session_state.get("_cookie_seq", 0) + 1
    manager = stx.CookieManager(key=f"ledgerline_cookies_{seq}")
    for n, (op, token) in enumerate(ops):
        if op == "set":
            manager.set(cfg.cookie_name, token, key=f"ck_set_{seq}_{n}", max_age=cfg.session_days * 86400,
                        expires_at=datetime.now() + timedelta(days=cfg.session_days),
                        secure=cfg.cookie_secure, same_site="strict")
        else:
            try:
                manager.delete(cfg.cookie_name, key=f"ck_del_{seq}_{n}")
            except KeyError:
                # The library drops the name from its local dict AFTER dispatching the delete,
                # and that dict is still empty on the first render. The delete already went out.
                pass


def _mark_signed_out() -> None:
    st.session_state.update(authenticated=False, user=None, auth_token=None, auth_jti=None)


def resolve_user() -> Optional[Actor]:
    """Return the verified user for this run, or None.

    Order: (1) this session's token, (2) the browser cookie (restores a session after a
    refresh). The token is re-verified against the database at most every
    AUTH_RECHECK_SECONDS, which keeps revocation near-instant without a query per rerun.
    """
    ss = st.session_state
    token, from_cookie = ss["auth_token"], False
    if token and ss["authenticated"] and ss["user"] is not None \
            and time.monotonic() - ss["auth_checked_at"] < AUTH_RECHECK_SECONDS:
        return ss["user"]
    if not token:
        token, from_cookie = _browser_cookie(), True
    if not token:
        _mark_signed_out()
        return None
    resolved = users.resolve_token(token)
    if resolved is None:
        if from_cookie:
            ss["_ignore_cookie"] = token      # never read this one again (prevents a reload loop)
            queue_cookie("delete")            # and clear it from the browser, once
        _mark_signed_out()
        return None
    user, jti = resolved
    ss.update(authenticated=True, user=user.actor(), auth_token=token, auth_jti=jti,
              auth_checked_at=time.monotonic())
    return ss["user"]


def _sign_in(user_id: int) -> None:
    token, jti, _ = users.start_session(user_id)
    st.session_state.update(auth_token=token, auth_jti=jti, auth_checked_at=0.0)  # verified fresh next run
    queue_cookie("set", token)
    st.rerun()  # one deliberate rerun; afterwards authenticated=True so it cannot repeat


def logout() -> None:
    ss = st.session_state
    token, jti = ss.get("auth_token"), ss.get("auth_jti")
    if jti:
        users.revoke_session(jti)
    ss.clear()  # nothing from this user may leak into the next login
    ss["_ignore_cookie"] = token  # the handshake cookie may still hold this (now revoked) token
    ss["_cookie_ops"] = []
    queue_cookie("delete")
    st.rerun()


# ------------------------------------------------------------------------ pages
def _shell(title: str, subtitle: str):
    styles.hide_sidebar()
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
        st.markdown(f'<div style="height:6vh"></div><p class="login-brand">{title}</p>'
                    f'<p class="login-sub">{subtitle}</p>', unsafe_allow_html=True)
        language_picker("login")
    return middle


def render_setup() -> None:
    """First run: no users exist, so create the administrator (no default password ever exists)."""
    name = get_config().app_name
    with _shell(name, t("Welcome. Create the administrator account to get started.")):
        with st.container(border=True):
            with st.form("setup_form"):
                username = st.text_input(t("Username"), key="setup_user")
                full_name = st.text_input(t("Full name (optional)"), key="setup_name")
                password = st.text_input(t("Password (min {MIN_PASSWORD_LENGTH} characters)", MIN_PASSWORD_LENGTH=MIN_PASSWORD_LENGTH), type="password", key="setup_pass")
                confirm = st.text_input(t("Repeat password"), type="password", key="setup_pass2")
                submitted = st.form_submit_button(t("Create administrator"), type="primary")
        if submitted:
            if password != confirm:
                st.error(t("The passwords do not match."))
                return
            try:
                user_id = users.create_user(None, username, password, "admin", full_name)
            except (users.UserError, PermissionError) as exc:
                fail(exc)
                return
            _sign_in(user_id)


def render_login() -> None:
    cfg = get_config()
    with _shell(cfg.app_name, t("Sign in to continue")):
        with st.container(border=True):
            with st.form("login_form"):
                username = st.text_input(t("Username"), key="login_user")
                password = st.text_input(t("Password"), type="password", key="login_pass")
                submitted = st.form_submit_button(t("Sign in"), type="primary")
        if not cookies_supported():
            st.caption(t("Tip: install extra-streamlit-components to stay signed in after a refresh."))
        if submitted:
            result = users.authenticate(username, password)
            if result.user is None:
                st.error(tr_error(result.error))
            else:
                _sign_in(result.user.id)


def require_login() -> Optional[Actor]:
    """Return the signed-in user, or draw the setup/login page and return None."""
    flush_cookie_ops()
    user = resolve_user()
    if user is not None:
        return user
    if users.user_count() == 0:
        render_setup()
    else:
        render_login()
    flush_cookie_ops()  # a stale-cookie delete queued by resolve_user()
    return None
