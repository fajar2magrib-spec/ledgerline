"""Users, login (with lockout) and revocable sessions."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select, update

from .. import security
from ..config import get_config
from ..db import read_session, write_session
from ..events import Actor, acting_as, log_event
from ..models import AuthSession, User, utcnow


class UserError(ValueError):
    """A problem the user can fix (shown in the UI as-is)."""


@dataclass(frozen=True)
class UserView:
    id: int
    username: str
    full_name: str
    email: str
    role: str
    is_active: bool
    last_login_at: Optional[object]

    def actor(self) -> Actor:
        return Actor(self.id, self.username, self.role)


def _view(u: User) -> UserView:
    return UserView(u.id, u.username, u.full_name or "", u.email or "", u.role, u.is_active, u.last_login_at)


def user_count() -> int:
    with read_session() as s:
        return int(s.scalar(select(func.count(User.id))) or 0)


def list_users() -> list[UserView]:
    with read_session() as s:
        return [_view(u) for u in s.scalars(select(User).order_by(User.username))]


def create_user(actor: Optional[Actor], username: str, password: str, role: str,
                full_name: str = "", email: str = "") -> int:
    """Create a user. ``actor`` None is allowed only for the first admin (setup wizard)."""
    username = username.strip().lower()
    if actor is not None:
        security.require(actor, "user.manage")
    elif user_count() > 0:
        raise security.PermissionDenied("Only an administrator can create users.")
    problems = []
    if not (3 <= len(username) <= 64) or not username.replace("_", "").replace(".", "").replace("-", "").isalnum():
        problems.append("Username: 3-64 letters, digits, '.', '-' or '_'.")
    if role not in security.ROLES:
        problems.append("Unknown role.")
    problems += security.password_problems(password, username)
    if problems:
        raise UserError(" ".join(problems))
    with acting_as(actor or Actor(None, "setup", "admin")), write_session() as s:
        if s.scalar(select(User.id).where(User.username == username)):
            raise UserError("That username is already taken.")
        user = User(username=username, full_name=full_name.strip(), email=email.strip() or None,
                    password_hash=security.hash_password(password), role=role)
        s.add(user)
        s.flush()
        return user.id


def bootstrap_admin_from_config() -> bool:
    """Optional unattended setup: ADMIN_USERNAME / ADMIN_PASSWORD env vars create the first
    admin when the database has no users. Remove the password from the environment after."""
    cfg = get_config()
    if user_count() or not (cfg.bootstrap_admin_username and cfg.bootstrap_admin_password):
        return False
    create_user(None, cfg.bootstrap_admin_username, cfg.bootstrap_admin_password, "admin", "Administrator")
    return True


# ------------------------------------------------------------------------ login
@dataclass(frozen=True)
class LoginResult:
    user: Optional[UserView]
    error: str = ""


# Verified against when the username does not exist, so response time is the same.
_DUMMY_HASH = security.hash_password("not-a-real-password", iterations=1000)


def authenticate(username: str, password: str) -> LoginResult:
    cfg = get_config()
    username = username.strip().lower()
    now = utcnow()
    with write_session() as s:
        user = s.scalar(select(User).where(User.username == username))
        if user is not None and user.locked_until and user.locked_until > now:
            minutes = int((user.locked_until - now).total_seconds() // 60) + 1
            return LoginResult(None, f"Too many failed attempts. Try again in {minutes} minute(s).")
        ok = security.verify_password(password, user.password_hash if user else _DUMMY_HASH)
        if user is None or not ok or not user.is_active:
            if user is not None and user.is_active:
                user.failed_logins += 1
                if user.failed_logins >= cfg.login_max_attempts:
                    user.locked_until = now + timedelta(minutes=cfg.login_lockout_minutes)
                    user.failed_logins = 0
                    log_event(s, "LOGIN_LOCKED", f"Account {username} locked after repeated failures",
                              entity_type="User", entity_id=user.id, actor=Actor(user.id, username, user.role))
            return LoginResult(None, "Incorrect username or password.")
        user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
        log_event(s, "LOGIN", f"{username} signed in", entity_type="User", entity_id=user.id,
                  actor=Actor(user.id, username, user.role))
        return LoginResult(_view(user))


# --------------------------------------------------------------------- sessions
def start_session(user_id: int) -> tuple[str, str, object]:
    """Create a revocable session row and a signed token for the cookie."""
    cfg = get_config()
    jti = secrets.token_hex(24)
    expires = utcnow() + timedelta(days=cfg.session_days)
    with write_session() as s:
        user = s.get(User, user_id)
        if user.last_login_at is None:
            user.last_login_at = utcnow()  # first sign-in (e.g. right after setup) would otherwise show "Never"
        s.add(AuthSession(id=jti, user_id=user_id, expires_at=expires))
        token = security.issue_token(cfg.auth_secret, user_id, user.role, jti, expires)
    return token, jti, expires


def resolve_token(token: str) -> Optional[tuple[UserView, str]]:
    """Validate a cookie token: signature, expiry, not revoked, user still active."""
    claims = security.decode_token(get_config().auth_secret, token)
    if not claims:
        return None
    with read_session() as s:
        row = s.get(AuthSession, claims["jti"])
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            return None
        user = s.get(User, row.user_id)
        if user is None or not user.is_active:
            return None
        return _view(user), row.id  # role comes from the DB, never from the token


def revoke_session(jti: str) -> None:
    with write_session() as s:
        s.execute(update(AuthSession).where(AuthSession.id == jti, AuthSession.revoked_at.is_(None))
                  .values(revoked_at=utcnow()))


def revoke_user_sessions(user_id: int) -> None:
    with write_session() as s:
        s.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                  .values(revoked_at=utcnow()))


# ------------------------------------------------------------------ administration
def _guard_last_admin(s, user: User, *, new_role: Optional[str] = None, deactivate: bool = False) -> None:
    if user.role != "admin" or not user.is_active:
        return
    if (new_role and new_role != "admin") or deactivate:
        others = s.scalar(select(func.count(User.id)).where(User.role == "admin", User.is_active, User.id != user.id))
        if not others:
            raise UserError("There must always be at least one active administrator.")


def update_user(actor: Actor, user_id: int, *, role: Optional[str] = None, is_active: Optional[bool] = None) -> None:
    security.require(actor, "user.manage")
    if role is not None and role not in security.ROLES:
        raise UserError("Unknown role.")
    with acting_as(actor), write_session() as s:
        user = s.get(User, user_id)
        if user is None:
            raise UserError("User not found.")
        _guard_last_admin(s, user, new_role=role, deactivate=is_active is False)
        if role is not None:
            user.role = role
        if is_active is not None:
            user.is_active = is_active
    if role is not None or is_active is False:
        revoke_user_sessions(user_id)  # the change applies on their very next click


def reset_password(actor: Actor, user_id: int, new_password: str) -> None:
    security.require(actor, "user.manage")
    with write_session() as s:
        user = s.get(User, user_id)
        if user is None:
            raise UserError("User not found.")
        problems = security.password_problems(new_password, user.username)
        if problems:
            raise UserError(" ".join(problems))
        with acting_as(actor):
            user.password_hash = security.hash_password(new_password)
            user.failed_logins, user.locked_until = 0, None
    revoke_user_sessions(user_id)
