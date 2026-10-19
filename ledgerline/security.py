"""Passwords, signed session tokens and role-based access control (RBAC)."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import jwt

if TYPE_CHECKING:  # avoid a circular import at runtime
    from .events import Actor

# --------------------------------------------------------------------------- passwords
PBKDF2_ITERATIONS = 600_000  # OWASP guidance for PBKDF2-HMAC-SHA256
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str, iterations: Optional[int] = None) -> str:
    """Salted PBKDF2-HMAC-SHA256, stored as ``pbkdf2_sha256$iterations$salt$hash``.

    Pure standard library, so installs cleanly on every platform. The iteration count is
    stored with the hash, so it can be raised later without invalidating old passwords.
    """
    iterations = iterations or PBKDF2_ITERATIONS
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def password_problems(password: str, username: str = "") -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if username and password.lower() == username.lower():
        problems.append("The password must not equal the username.")
    if password.isdigit() or password.isalpha():
        problems.append("Mix letters with numbers or symbols.")
    return problems


# ------------------------------------------------------------------------------- JWT
def issue_token(secret: str, user_id: int, role: str, jti: str, expires_at: datetime) -> str:
    """Signed (HS256) session token. The ``jti`` claim links it to a revocable DB row."""
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "role": role, "jti": jti, "iat": now,
              "exp": expires_at.replace(tzinfo=timezone.utc)}
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> Optional[dict]:
    """Verified claims, or None for a forged, tampered or expired token."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "jti"]})
    except jwt.PyJWTError:
        return None


# ------------------------------------------------------------------------------ RBAC
ROLES = ("admin", "manager", "viewer")

_VIEWER = {"dashboard.view", "invoice.view", "invoice.pdf"}
_MANAGER = _VIEWER | {
    "invoice.create", "invoice.edit", "invoice.cancel", "payment.record",
    "client.edit", "message.send", "data.export", "product.manage", "expense.manage", "bank.manage",
}
_ADMIN = _MANAGER | {
    "invoice.edit_any",   # edit invoices older than the manager edit window
    "invoice.delete", "payment.delete",
    "user.manage", "audit.view", "settings.manage", "data.import",
}
PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset(_VIEWER), "manager": frozenset(_MANAGER), "admin": frozenset(_ADMIN),
}


class PermissionDenied(PermissionError):
    pass


def has_permission(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, frozenset())


def require(actor: "Actor", permission: str) -> None:
    """Service-layer guard. The UI also hides buttons, but THIS is what enforces the rule."""
    if actor is None or not has_permission(actor.role, permission):
        raise PermissionDenied(f"Your role does not allow this action ({permission}).")
