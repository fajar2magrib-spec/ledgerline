"""Durable outbound queue (email + WhatsApp) with a background worker.

Why a database queue instead of sending inside the click handler
* The UI never freezes on a slow mail server.
* Nothing is lost if the app restarts: queued messages are rows.
* Failed sends are retried with exponential backoff, then marked ``failed`` with the
  reason, visible in the UI.
* Several app processes can share one database: a message is CLAIMED with an atomic
  UPDATE, so it is never sent twice.
"""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, update

from ..config import get_config
from ..db import read_session, write_session
from ..events import SYSTEM_ACTOR, Actor, acting_as, log_event
from ..integrations import messaging
from ..integrations.email import EmailError, get_email_service, render_invoice_email_html
from ..models import OutboundMessage, utcnow
from ..pdf import REPORTLAB_AVAILABLE, build_invoice_pdf
from ..security import require
from .invoices import InvoiceError, get_invoice_detail
from .settings import get_settings

log = logging.getLogger(__name__)
_wake = threading.Event()


@dataclass(frozen=True)
class MessageView:
    id: int
    channel: str
    recipient: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str
    created_at: object
    sent_at: Optional[object]


def _backoff(attempt: int) -> timedelta:
    """1 min, 2, 4, 8 ... capped at 1 hour, with jitter so retries do not stampede."""
    return timedelta(seconds=min(60 * 2 ** (attempt - 1), 3600) * (0.8 + random.random() * 0.4))


def enqueue_email(actor: Actor, invoice_id: int, to_addr: str, subject: str, body: str) -> int:
    require(actor, "message.send")
    if not get_email_service().configured:
        raise InvoiceError("Email is not configured. Ask an administrator to set the SMTP_* secrets.")
    if not REPORTLAB_AVAILABLE:
        raise InvoiceError("Attaching the PDF needs reportlab (pip install reportlab).")
    return _enqueue(actor, "email", invoice_id, to_addr.strip(), subject.strip(), body)


def enqueue_whatsapp(actor: Actor, invoice_id: int, phone: str, body: str) -> int:
    require(actor, "message.send")
    if messaging.get_provider() is None:
        raise InvoiceError("Automatic WhatsApp is not configured. Use the manual link instead.")
    digits = messaging.normalize_phone(phone)
    if not digits:
        raise InvoiceError("Enter the client's WhatsApp number with country code.")
    return _enqueue(actor, "whatsapp", invoice_id, digits, "", body)


def _enqueue(actor: Actor, channel: str, invoice_id: int, recipient: str, subject: str, body: str) -> int:
    with acting_as(actor), write_session() as s:
        msg = OutboundMessage(channel=channel, recipient=recipient, subject=subject, body=body,
                              invoice_id=invoice_id, created_by_id=actor.user_id,
                              max_attempts=get_config().outbox_max_attempts)
        s.add(msg)
        s.flush()
        log_event(s, "MESSAGE_QUEUED", f"Queued {channel} for invoice #{invoice_id}", entity_type="OutboundMessage",
                  entity_id=msg.id, invoice_id=invoice_id, actor=actor)
        mid = msg.id
    _wake.set()  # deliver now instead of waiting for the next poll
    return mid


def recent_messages(invoice_id: int, limit: int = 8) -> list[MessageView]:
    with read_session() as s:
        rows = s.scalars(select(OutboundMessage).where(OutboundMessage.invoice_id == invoice_id)
                         .order_by(OutboundMessage.id.desc()).limit(limit))
        return [MessageView(m.id, m.channel, m.recipient, m.status, m.attempts, m.max_attempts,
                            m.last_error or "", m.created_at, m.sent_at) for m in rows]


def _claim_due(limit: int) -> list[int]:
    now = utcnow()
    claimed: list[int] = []
    with write_session() as s:  # BEGIN IMMEDIATE: claims are serialised across processes
        ids = s.scalars(select(OutboundMessage.id).where(
            OutboundMessage.status == "queued", OutboundMessage.next_attempt_at <= now)
            .order_by(OutboundMessage.id).limit(limit)).all()
        for mid in ids:
            res = s.execute(update(OutboundMessage).where(OutboundMessage.id == mid, OutboundMessage.status == "queued")
                            .values(status="sending", attempts=OutboundMessage.attempts + 1))
            if res.rowcount:
                claimed.append(mid)
    return claimed


def _deliver(m: OutboundMessage) -> messaging.SendResult:
    """Send one claimed message. Never raises: failures become SendResult."""
    try:
        detail = get_invoice_detail(m.invoice_id) if m.invoice_id else None
        settings = get_settings()
        if detail is None:
            return messaging.SendResult(False, m.channel, error="The invoice no longer exists.")
        if m.channel == "email":
            service = get_email_service()
            pdf = build_invoice_pdf(detail, settings, lang=getattr(settings, "default_language", "en"))  # rendered fresh: always the current state
            html = render_invoice_email_html(detail, settings, m.body)
            msg = service.build_message(m.recipient, m.subject, m.body, html, [(f"{detail.number}.pdf", pdf)])
            service.send(msg)
            return messaging.SendResult(True, "smtp")
        provider = messaging.get_provider()
        if provider is None:
            return messaging.SendResult(False, "whatsapp", error="No WhatsApp provider is configured.")
        return provider.send(m.recipient, m.body, messaging.reminder_params(detail, settings))
    except EmailError as exc:
        return messaging.SendResult(False, "smtp", error=str(exc), retryable=exc.retryable)
    except Exception as exc:  # a worker must survive anything
        log.exception("Unexpected delivery error")
        return messaging.SendResult(False, m.channel, error=f"Unexpected error: {exc}", retryable=True)


def process_due_messages(limit: int = 10) -> int:
    """Deliver everything that is due. Returns how many messages were handled."""
    handled = 0
    for mid in _claim_due(limit):
        with read_session() as s:
            m = s.get(OutboundMessage, mid)
            s.expunge(m)
        result = _deliver(m)  # network I/O happens outside any database transaction
        with acting_as(SYSTEM_ACTOR), write_session() as s:
            row = s.get(OutboundMessage, mid)
            row.provider, row.provider_message_id = result.provider, result.message_id or None
            if result.ok:
                row.status, row.sent_at, row.last_error = "sent", utcnow(), None
                log_event(s, "MESSAGE_SENT", f"{row.channel} sent for invoice #{row.invoice_id}",
                          entity_type="OutboundMessage", entity_id=mid, invoice_id=row.invoice_id)
            elif result.retryable and row.attempts < row.max_attempts:
                row.status, row.last_error = "queued", result.error
                row.next_attempt_at = utcnow() + _backoff(row.attempts)
            else:
                row.status, row.last_error = "failed", result.error
                log_event(s, "MESSAGE_FAILED", f"{row.channel} failed: {result.error}", entity_type="OutboundMessage",
                          entity_id=mid, invoice_id=row.invoice_id)
        handled += 1
    return handled


def requeue_stuck(minutes: int = 10) -> int:
    """Messages left in 'sending' by a crashed process go back to the queue."""
    cutoff = utcnow() - timedelta(minutes=minutes)
    with write_session() as s:
        res = s.execute(update(OutboundMessage).where(OutboundMessage.status == "sending",
                        OutboundMessage.created_at < cutoff).values(status="queued"))
        return res.rowcount or 0


class OutboxWorker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="ledgerline-outbox", daemon=True)  # daemon: never blocks shutdown
        self._halt = threading.Event()

    def run(self) -> None:
        poll = max(get_config().outbox_poll_seconds, 1)
        try:
            requeue_stuck()
        except Exception:
            log.exception("requeue_stuck failed")
        while not self._halt.is_set():
            try:
                while process_due_messages():
                    pass
            except Exception:
                log.exception("Outbox cycle failed")
            _wake.wait(timeout=poll)
            _wake.clear()

    def stop(self) -> None:
        self._halt.set()
        _wake.set()


_worker: Optional[OutboxWorker] = None
_worker_lock = threading.Lock()


def start_worker() -> OutboxWorker:
    """Start the single background worker for this process (idempotent)."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = OutboxWorker()
            _worker.start()
        return _worker
