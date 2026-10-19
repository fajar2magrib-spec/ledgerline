"""Business settings stored in the database and editable by admins.

Deploy-time secrets live in config.py / .env; these are business preferences
(company name, currency, tax label, payment terms ...) that non-developers change.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, asdict, replace
from decimal import Decimal

from ..config import get_config
from ..countries import BY_ISO
from ..db import read_session, write_session
from ..events import Actor, log_event
from ..formatting import CURRENCIES, DEFAULT_CURRENCY
from ..models import AppSetting
from ..security import require


@dataclass(frozen=True)
class AppSettings:
    company_name: str
    company_address: str
    company_email: str
    company_tax_id: str
    currency: str
    tax_label: str
    default_tax_rate: Decimal
    payment_terms_days: int
    invoice_prefix: str
    brand_color: str = "#2563EB"
    default_language: str = "en"      # language shown to new visitors (each person can switch)
    default_country: str = "PK"       # country pre-selected in phone-number pickers (ISO-2)


def _defaults() -> AppSettings:
    return AppSettings(
        company_name=get_config().app_name, company_address="", company_email="",
        company_tax_id="", currency=DEFAULT_CURRENCY, tax_label="Tax",
        default_tax_rate=Decimal("0"), payment_terms_days=14, invoice_prefix="INV",
    )


def get_settings() -> AppSettings:
    base = _defaults()
    with read_session() as s:
        stored = {r.key: r.value for r in s.query(AppSetting).all()}
    values = {}
    for name, default in asdict(base).items():
        if name not in stored:
            continue
        raw = stored[name]
        values[name] = Decimal(raw) if isinstance(default, Decimal) else int(raw) if isinstance(default, int) else raw
    return replace(base, **values)


def validate_settings(new: AppSettings) -> list[str]:
    problems = []
    if not new.company_name.strip():
        problems.append("Company name is required.")
    if new.currency not in CURRENCIES:
        problems.append("Choose a supported currency.")
    if not 0 <= new.default_tax_rate <= 100:
        problems.append("Default tax rate must be between 0 and 100.")
    if not 0 <= new.payment_terms_days <= 365:
        problems.append("Payment terms must be between 0 and 365 days.")
    if not new.invoice_prefix.strip().isalnum():
        problems.append("Invoice prefix may contain letters and digits only.")
    if not HEX_COLOR.match(new.brand_color or ""):
        problems.append("Brand colour must look like #2563EB.")
    if not re.match(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$|^x_[a-z0-9]{1,8}$", new.default_language or ""):
        problems.append("Choose a valid default language.")
    if new.default_country not in BY_ISO:
        problems.append("Choose a valid default country.")
    return problems


def save_settings(actor: Actor, new: AppSettings) -> list[str]:
    """Persist settings; returns validation problems (empty list means saved)."""
    require(actor, "settings.manage")
    problems = validate_settings(new)
    if problems:
        return problems
    old = get_settings()
    changed = {k: [str(v), str(getattr(new, k))] for k, v in asdict(old).items() if getattr(new, k) != v}
    with write_session() as s:
        for key, value in asdict(new).items():
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=str(value)))
            else:
                row.value = str(value)
        if changed:
            log_event(s, "SETTINGS_UPDATED", f"Settings changed: {', '.join(changed)}",
                      entity_type="Settings", actor=actor, changes=changed)
    return []


# ----------------------------------------------------------------------- logo and first-run
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
LOGO_KEY, SETUP_KEY = "logo_b64", "setup_done"
MAX_LOGO_BYTES = 500_000


def image_kind(data: bytes) -> str:
    """'png' or 'jpeg' judged by the file's first bytes (never by its name), else ''."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return ""


def get_logo() -> bytes | None:
    with read_session() as s:
        row = s.get(AppSetting, LOGO_KEY)
        raw = row.value if row else ""
    try:
        return base64.b64decode(raw) if raw else None
    except ValueError:
        return None


def save_logo(actor: Actor, data: bytes | None) -> list[str]:
    """Store (or with ``None`` remove) the company logo. Returns problems; empty means saved."""
    require(actor, "settings.manage")
    if data is not None:
        if len(data) > MAX_LOGO_BYTES:
            return [f"The logo is too large. Please use an image under {MAX_LOGO_BYTES // 1000} KB."]
        if not image_kind(data):
            return ["The logo must be a PNG or JPG image."]
    with write_session() as s:
        row = s.get(AppSetting, LOGO_KEY)
        value = base64.b64encode(data).decode("ascii") if data else ""
        if row is None:
            s.add(AppSetting(key=LOGO_KEY, value=value))
        else:
            row.value = value
        log_event(s, "SETTINGS_UPDATED", "Company logo " + ("updated" if data else "removed"), entity_type="Settings", actor=actor)
    return []


def is_setup_done() -> bool:
    """True once the first-run wizard was finished or skipped, or an existing install already saved settings."""
    with read_session() as s:
        return s.get(AppSetting, SETUP_KEY) is not None or s.get(AppSetting, "company_name") is not None


def mark_setup_done(actor: Actor) -> None:
    require(actor, "settings.manage")
    with write_session() as s:
        if s.get(AppSetting, SETUP_KEY) is None:
            s.add(AppSetting(key=SETUP_KEY, value="1"))
