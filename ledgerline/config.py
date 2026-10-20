"""
Configuration and secret management
===================================
Every deploy-time value (database URL, signing/encryption keys, SMTP and WhatsApp
credentials) is resolved here and nowhere else. Nothing secret is hardcoded.

Lookup order for each setting (first hit wins):
  1. Real environment variables   Docker, systemd, CI, hosting dashboards.
  2. A local ``.env`` file        python-dotenv loads it into the environment for
                                  development. Copy ``.env.example`` to ``.env``.
  3. ``.streamlit/secrets.toml``  read through ``st.secrets`` using the SAME flat,
                                  UPPERCASE names. Copy ``secrets.toml.example``.
  4. The safe defaults below.

Architectural decision: one frozen ``AppConfig`` object, built once and cached, so the
rest of the code base never touches ``os.environ`` or ``st.secrets`` directly. That keeps
secrets out of the UI layer and makes tests trivial (set env vars, clear the cache).
"""

from __future__ import annotations

import logging
import os
import secrets as pysecrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# override=False: variables already present in the real environment always win.
load_dotenv(BASE_DIR / ".env", override=False)

# Used only when AUTH_SECRET is missing in development. It lives in memory, so every
# restart signs everyone out. Production refuses to start without a real secret.
_EPHEMERAL_AUTH_SECRET = pysecrets.token_urlsafe(48)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _from_streamlit_secrets(name: str) -> Optional[str]:
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:  # no secrets file, bare mode, or malformed TOML
        return None
    return None if value is None else str(value)


def setting(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve one setting: environment/.env first, then st.secrets, then default."""
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    value = _from_streamlit_secrets(name)
    if value not in (None, ""):
        return value
    return default


def _flag(name: str, default: bool) -> bool:
    raw = setting(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _number(name: str, default: int) -> int:
    try:
        return int(setting(name, str(default)))  # type: ignore[arg-type]
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number.") from exc


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str  # "STARTTLS", "SSL" or "NONE"
    username: str
    password: str
    from_addr: str
    from_name: str
    pool_size: int
    timeout: int
    retries: int

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_addr)


@dataclass(frozen=True)
class MessagingSettings:
    provider: str  # "link" (manual wa.me only), "whatsapp_cloud" or "twilio"
    cloud_token: str
    cloud_phone_number_id: str
    cloud_api_version: str
    cloud_template: str
    cloud_template_lang: str
    twilio_sid: str
    twilio_token: str
    twilio_from: str


@dataclass(frozen=True)
class AppConfig:
    env: str
    app_name: str
    database_url: str
    auth_secret: str
    auth_secret_is_ephemeral: bool
    session_days: int
    cookie_name: str
    cookie_secure: bool
    encryption_key: Optional[str]
    bootstrap_admin_username: Optional[str]
    bootstrap_admin_password: Optional[str]
    reset_admin_username: Optional[str]
    reset_admin_password: Optional[str]
    login_max_attempts: int
    login_lockout_minutes: int
    manager_edit_window_days: int
    outbox_poll_seconds: int
    outbox_max_attempts: int
    smtp: SmtpSettings
    messaging: MessagingSettings

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def _database_url() -> str:
    url = setting("DATABASE_URL")
    if not url:
        # Development default: a local SQLite file (WAL mode is enabled in db.py).
        return f"sqlite:///{(BASE_DIR / 'data' / 'ledgerline.db').as_posix()}"
    return normalize_database_url(url)


def normalize_database_url(url: str) -> str:
    """Accept the link exactly as a host (Neon, Supabase, Heroku ...) shows it.

    Those hosts print ``postgres://`` or ``postgresql://``. SQLAlchemy would then look for the old ``psycopg2`` driver,
    but this app ships ``psycopg`` (v3), so the scheme is rewritten to ``postgresql+psycopg://``. A URL that already names
    a driver (``postgresql+psycopg2://``, ``mysql+pymysql://``, ``sqlite:///``) is left alone.
    """
    from importlib.util import find_spec
    for legacy in ("postgres://", "postgresql://"):
        if url.startswith(legacy):
            if legacy == "postgresql://" and find_spec("psycopg") is None and find_spec("psycopg2") is not None:
                return url                                   # only the old driver is installed: keep SQLAlchemy's default
            return "postgresql+psycopg://" + url[len(legacy):]
    return url


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    env = (setting("LEDGERLINE_ENV", "development") or "development").lower()
    auth_secret = setting("AUTH_SECRET")
    ephemeral = False
    if not auth_secret or len(auth_secret) < 32:
        if env == "production":
            raise ConfigError(
                "AUTH_SECRET is required in production and must be at least 32 characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        auth_secret, ephemeral = _EPHEMERAL_AUTH_SECRET, True

    security = (setting("SMTP_SECURITY", "STARTTLS") or "STARTTLS").upper().replace("/", "")
    smtp = SmtpSettings(
        host=setting("SMTP_HOST", "") or "",
        port=_number("SMTP_PORT", 587),
        security="SSL" if security.startswith("SSL") else ("NONE" if security == "NONE" else "STARTTLS"),
        username=setting("SMTP_USERNAME", "") or "",
        password=setting("SMTP_PASSWORD", "") or "",
        from_addr=setting("SMTP_FROM_ADDRESS", "") or "",
        from_name=setting("SMTP_FROM_NAME", "") or "",
        pool_size=_number("SMTP_POOL_SIZE", 2),
        timeout=_number("SMTP_TIMEOUT_SECONDS", 30),
        retries=_number("SMTP_RETRIES", 3),
    )
    messaging = MessagingSettings(
        provider=(setting("WHATSAPP_PROVIDER", "link") or "link").lower(),
        cloud_token=setting("WHATSAPP_CLOUD_TOKEN", "") or "",
        cloud_phone_number_id=setting("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "") or "",
        cloud_api_version=setting("WHATSAPP_CLOUD_API_VERSION", "v21.0") or "v21.0",
        cloud_template=setting("WHATSAPP_CLOUD_TEMPLATE", "") or "",
        cloud_template_lang=setting("WHATSAPP_CLOUD_TEMPLATE_LANG", "en") or "en",
        twilio_sid=setting("TWILIO_ACCOUNT_SID", "") or "",
        twilio_token=setting("TWILIO_AUTH_TOKEN", "") or "",
        twilio_from=setting("TWILIO_WHATSAPP_FROM", "") or "",
    )
    return AppConfig(
        env=env,
        app_name=setting("APP_NAME", "Ledgerline") or "Ledgerline",
        database_url=_database_url(),
        auth_secret=auth_secret,
        auth_secret_is_ephemeral=ephemeral,
        session_days=_number("SESSION_DAYS", 7),
        cookie_name=setting("SESSION_COOKIE_NAME", "ledgerline_session") or "ledgerline_session",
        # "Secure" cookies are only sent over HTTPS, so default to on in production.
        cookie_secure=_flag("COOKIE_SECURE", env == "production"),
        encryption_key=setting("ENCRYPTION_KEY"),
        bootstrap_admin_username=setting("ADMIN_USERNAME"),
        bootstrap_admin_password=setting("ADMIN_PASSWORD"),
        reset_admin_username=setting("ADMIN_RESET_USERNAME"),
        reset_admin_password=setting("ADMIN_RESET_PASSWORD"),
        login_max_attempts=_number("LOGIN_MAX_ATTEMPTS", 5),
        login_lockout_minutes=_number("LOGIN_LOCKOUT_MINUTES", 15),
        manager_edit_window_days=_number("MANAGER_EDIT_WINDOW_DAYS", 7),
        outbox_poll_seconds=_number("OUTBOX_POLL_SECONDS", 3),
        outbox_max_attempts=_number("OUTBOX_MAX_ATTEMPTS", 5),
        smtp=smtp,
        messaging=messaging,
    )


def configure_logging() -> None:
    """Console logging with timestamps; level via LOG_LEVEL (default INFO)."""
    level = getattr(logging, (setting("LOG_LEVEL", "INFO") or "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
