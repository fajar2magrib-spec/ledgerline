"""A forgotten administrator password can be recovered from the server's secrets, once, and safely."""

import pytest

from ledgerline import config
from ledgerline.services import users


def _login(name, pw):
    return users.authenticate(name, pw).user is not None


@pytest.fixture()
def admin(env):
    users.create_user(None, "boss", "Original-pass-1", "admin")
    users.create_user(users.list_users()[0].actor(), "vera", "Viewer-pass-77", "viewer")
    return "boss"


def _set(monkeypatch, password=None, username=None):
    for key, value in (("ADMIN_RESET_PASSWORD", password), ("ADMIN_RESET_USERNAME", username)):
        monkeypatch.delenv(key, raising=False) if value is None else monkeypatch.setenv(key, value)
    config.get_config.cache_clear()


def test_nothing_happens_without_the_setting(admin, monkeypatch):
    _set(monkeypatch)
    assert users.reset_admin_from_config() is None and _login("boss", "Original-pass-1")


def test_reset_replaces_the_password_and_signs_everyone_out(admin, monkeypatch):
    _, _jti = users.start_session(users.list_users()[0].id)[:2]
    _set(monkeypatch, "Brand-new-pass-9")
    assert users.reset_admin_from_config() == "boss"
    assert _login("boss", "Brand-new-pass-9") and not _login("boss", "Original-pass-1")


def test_it_is_applied_once_so_a_later_password_change_survives_restarts(admin, monkeypatch):
    _set(monkeypatch, "Brand-new-pass-9")
    users.reset_admin_from_config()
    boss = next(u for u in users.list_users() if u.username == "boss")
    users.reset_password(boss.actor(), boss.id, "Chosen-later-pass-5")
    assert users.reset_admin_from_config() is None            # secret still set: must NOT overwrite
    assert _login("boss", "Chosen-later-pass-5")
    _set(monkeypatch, "Another-reset-pass-3")                 # a NEW value is a new request
    assert users.reset_admin_from_config() == "boss" and _login("boss", "Another-reset-pass-3")


def test_unknown_or_non_admin_username_and_weak_password_change_nothing(admin, monkeypatch):
    _set(monkeypatch, "Brand-new-pass-9", "vera")             # vera is a viewer, not an administrator
    assert users.reset_admin_from_config() is None
    _set(monkeypatch, "Brand-new-pass-9", "nobody")
    assert users.reset_admin_from_config() is None
    _set(monkeypatch, "short")
    assert users.reset_admin_from_config() is None
    assert _login("boss", "Original-pass-1") and _login("vera", "Viewer-pass-77")



def test_a_user_can_change_their_own_password(admin):
    vera = next(u for u in users.list_users() if u.username == "vera")
    _token, jti, _ = users.start_session(vera.id)
    _token2, jti_other, _ = users.start_session(vera.id)           # a second device
    with pytest.raises(users.UserError):                              # wrong current password
        users.change_own_password(vera.actor(), "wrong-pass-1", "Fresh-pass-2024", keep_jti=jti)
    with pytest.raises(users.UserError):                              # weak new password
        users.change_own_password(vera.actor(), "Viewer-pass-77", "short", keep_jti=jti)
    with pytest.raises(users.UserError):                              # unchanged
        users.change_own_password(vera.actor(), "Viewer-pass-77", "Viewer-pass-77", keep_jti=jti)
    users.change_own_password(vera.actor(), "Viewer-pass-77", "Fresh-pass-2024", keep_jti=jti)
    assert _login("vera", "Fresh-pass-2024") and not _login("vera", "Viewer-pass-77")
    assert users.resolve_token(_token) is not None                    # this device stays signed in
    assert users.resolve_token(_token2) is None                       # the other device is signed out
