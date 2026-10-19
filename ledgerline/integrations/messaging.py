"""WhatsApp: manual wa.me link plus automated providers.

Two ways to send, one fallback chain
1. Manual    ``build_wa_link`` opens WhatsApp Web/app with the text pre-filled. Always
             available, needs no account, a person presses Send.
2. Automated ``WhatsAppCloudProvider`` (Meta) or ``TwilioWhatsAppProvider`` deliver in the
             background through the outbox worker. Selected with WHATSAPP_PROVIDER.
If no automated provider is configured (or it keeps failing) the UI falls back to the
manual link, so a reminder can always be sent.

Both providers use plain ``requests`` (no vendor SDKs): fewer dependencies, easy to test.
WhatsApp rule: a business may only start a conversation with an APPROVED TEMPLATE message;
free text is allowed within 24 h of the customer's last message. Set
WHATSAPP_CLOUD_TEMPLATE for reminders (body variables: client, invoice, amount, due date).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol
from urllib.parse import quote

import requests

from ..config import MessagingSettings, get_config
from ..formatting import text_money
from ..services.invoices import InvoiceDetail
from ..services.settings import AppSettings

REQUEST_TIMEOUT = 15


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider: str
    message_id: str = ""
    error: str = ""
    retryable: bool = False


# Dialling code to assume for numbers typed in local form (e.g. 0346 3562785), by business currency.
CURRENCY_DIAL = {"PKR": "92", "INR": "91", "BDT": "880", "LKR": "94", "AED": "971", "SAR": "966", "EGP": "20",
                 "TRY": "90", "GBP": "44", "USD": "1", "CAD": "1", "AUD": "61", "NZD": "64", "ZAR": "27",
                 "NGN": "234", "KES": "254", "SGD": "65", "MYR": "60", "PHP": "63", "THB": "66"}


def normalize_phone_with(raw: str, country_code: str = "") -> Optional[str]:
    """Pure helper. ``+92...`` and ``0092...`` are already international. A number that starts with a single
    0 (local style, e.g. 03463562785) gets ``country_code`` in place of the 0, because wa.me needs
    92 346 3562785 and shows a blank page for 0346 3562785."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and country_code:
        digits = country_code + digits[1:]
    return digits if 8 <= len(digits) <= 15 else None


def default_country_code() -> str:
    """DEFAULT_COUNTRY_CODE from settings/.env if set, otherwise guessed from the business currency."""
    from ..config import setting

    configured = re.sub(r"\D", "", setting("DEFAULT_COUNTRY_CODE", "") or "")
    if configured:
        return configured
    try:
        from .. import countries
        from ..services.settings import get_settings

        cfg = get_settings()
        if cfg.default_country in countries.BY_ISO:
            return countries.get(cfg.default_country).dial   # the country chosen under Customize / Settings
        return CURRENCY_DIAL.get(cfg.currency, "")
    except Exception:  # noqa: BLE001  (never let a lookup problem stop a message link)
        return ""


def normalize_phone(raw: str) -> Optional[str]:
    """Digits only, international format without '+' or leading zeros (what wa.me wants).
    Returns None when it cannot be a valid number (8-15 digits)."""
    return normalize_phone_with(raw, default_country_code())


def build_wa_link(phone: str, text: str) -> tuple[str, bool]:
    """``(url, number_was_usable)``. Without a usable number WhatsApp shows its contact picker."""
    digits = normalize_phone(phone)
    base = f"https://wa.me/{digits}" if digits else "https://wa.me/"
    return f"{base}?text={quote(text)}", bool(digits) or not phone.strip()


def reminder_params(detail: InvoiceDetail, settings: AppSettings) -> list[str]:
    return [detail.client_name, detail.number, text_money(detail.balance, settings.currency),
            f"{detail.due_date:%b %d, %Y}"]


def reminder_text(detail: InvoiceDetail, settings: AppSettings, today: Optional[date] = None) -> str:
    verb = "which was due on" if detail.display_status(today) == "Overdue" else "due on"
    client, number, amount, due = reminder_params(detail, settings)
    return (f"Hello {client}, this is a friendly reminder regarding Invoice {number} for {amount} "
            f"{verb} {due}. Please let us know if you have any questions!")


class WhatsAppProvider(Protocol):
    name: str

    def configured(self) -> bool: ...
    def send(self, phone_digits: str, text: str, template_params: Optional[list[str]] = None) -> SendResult: ...


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


class WhatsAppCloudProvider:
    name = "whatsapp_cloud"

    def __init__(self, cfg: MessagingSettings) -> None:
        self.cfg = cfg

    def configured(self) -> bool:
        return bool(self.cfg.cloud_token and self.cfg.cloud_phone_number_id)

    def send(self, phone_digits: str, text: str, template_params: Optional[list[str]] = None) -> SendResult:
        c = self.cfg
        url = f"https://graph.facebook.com/{c.cloud_api_version}/{c.cloud_phone_number_id}/messages"
        if c.cloud_template and template_params:
            payload = {"messaging_product": "whatsapp", "to": phone_digits, "type": "template",
                       "template": {"name": c.cloud_template, "language": {"code": c.cloud_template_lang},
                                    "components": [{"type": "body", "parameters": [
                                        {"type": "text", "text": p} for p in template_params]}]}}
        else:
            payload = {"messaging_product": "whatsapp", "to": phone_digits, "type": "text",
                       "text": {"body": text, "preview_url": False}}
        try:
            r = requests.post(url, json=payload, headers={"Authorization": f"Bearer {c.cloud_token}"},
                              timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            return SendResult(False, self.name, error=f"Network error: {exc}", retryable=True)
        if r.ok:
            try:
                return SendResult(True, self.name, message_id=r.json()["messages"][0]["id"])
            except (ValueError, KeyError, IndexError):
                return SendResult(True, self.name)
        try:
            detail = r.json().get("error", {}).get("message", r.text[:200])
        except ValueError:
            detail = r.text[:200]
        return SendResult(False, self.name, error=f"WhatsApp Cloud API {r.status_code}: {detail}",
                          retryable=_retryable(r.status_code))


class TwilioWhatsAppProvider:
    name = "twilio"

    def __init__(self, cfg: MessagingSettings) -> None:
        self.cfg = cfg

    def configured(self) -> bool:
        return bool(self.cfg.twilio_sid and self.cfg.twilio_token and self.cfg.twilio_from)

    def send(self, phone_digits: str, text: str, template_params: Optional[list[str]] = None) -> SendResult:
        c = self.cfg
        sender = c.twilio_from if c.twilio_from.startswith("whatsapp:") else f"whatsapp:{c.twilio_from}"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{c.twilio_sid}/Messages.json"
        try:
            r = requests.post(url, data={"From": sender, "To": f"whatsapp:+{phone_digits}", "Body": text},
                              auth=(c.twilio_sid, c.twilio_token), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            return SendResult(False, self.name, error=f"Network error: {exc}", retryable=True)
        if r.status_code in (200, 201):
            return SendResult(True, self.name, message_id=r.json().get("sid", ""))
        try:
            detail = r.json().get("message", r.text[:200])
        except ValueError:
            detail = r.text[:200]
        return SendResult(False, self.name, error=f"Twilio {r.status_code}: {detail}", retryable=_retryable(r.status_code))


def get_provider() -> Optional[WhatsAppProvider]:
    """The configured automated provider, or None (then only the manual link is offered)."""
    cfg = get_config().messaging
    provider: Optional[WhatsAppProvider] = {
        "whatsapp_cloud": WhatsAppCloudProvider(cfg), "twilio": TwilioWhatsAppProvider(cfg),
    }.get(cfg.provider)
    return provider if provider is not None and provider.configured() else None
