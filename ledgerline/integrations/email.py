"""SMTP email engine: connection pool, retries, HTML + plain-text bodies, PDF attachment.

Architecture
* ``SmtpPool``      keeps a few authenticated connections open and reuses them, so a burst
                    of invoices does not pay the TLS + login handshake every time. Idle or
                    broken connections are detected (NOOP) and replaced.
* ``EmailService``  sends with short in-process retries (covers a dropped pooled connection
                    or a hiccup) and classifies failures as retryable or permanent. The
                    outbox worker adds LONG retries with backoff on top of this.
* Templates         standard-library ``string.Template`` with every value HTML-escaped,
                    so there is no extra dependency and no injection from client names.
"""

from __future__ import annotations

import logging
import random
import smtplib
import ssl
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from functools import lru_cache
from html import escape
from string import Template
from typing import Iterator, Optional

from ..config import SmtpSettings, get_config
from ..formatting import text_money
from ..services.invoices import InvoiceDetail
from ..services.settings import AppSettings

log = logging.getLogger(__name__)


class EmailError(Exception):
    """Delivery failure. ``retryable`` tells the outbox whether trying again can help."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


# ------------------------------------------------------------------------ pool
class SmtpPool:
    def __init__(self, cfg: SmtpSettings, idle_seconds: int = 60) -> None:
        self.cfg, self.idle_seconds = cfg, idle_seconds
        self._idle: deque[tuple[smtplib.SMTP, float]] = deque()
        self._lock = threading.Lock()

    def _connect(self) -> smtplib.SMTP:
        c = self.cfg
        ctx = ssl.create_default_context()
        if c.security == "SSL":
            conn: smtplib.SMTP = smtplib.SMTP_SSL(c.host, c.port, timeout=c.timeout, context=ctx)
        else:
            conn = smtplib.SMTP(c.host, c.port, timeout=c.timeout)
        conn.ehlo()
        if c.security == "STARTTLS":
            conn.starttls(context=ctx)
            conn.ehlo()
        if c.username:
            conn.login(c.username, c.password)
        return conn

    @staticmethod
    def _close(conn: smtplib.SMTP) -> None:
        try:
            conn.quit()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def connection(self) -> Iterator[smtplib.SMTP]:
        conn = self._checkout()
        try:
            yield conn
        except BaseException:
            self._close(conn)  # never put a possibly broken connection back
            raise
        else:
            self._checkin(conn)

    def _checkout(self) -> smtplib.SMTP:
        while True:
            with self._lock:
                entry = self._idle.pop() if self._idle else None
            if entry is None:
                return self._connect()
            conn, last_used = entry
            if time.monotonic() - last_used > self.idle_seconds:
                self._close(conn)
                continue
            try:
                if conn.noop()[0] == 250:
                    return conn
            except Exception:
                pass
            self._close(conn)

    def _checkin(self, conn: smtplib.SMTP) -> None:
        with self._lock:
            if len(self._idle) < max(self.cfg.pool_size, 1):
                self._idle.append((conn, time.monotonic()))
                return
        self._close(conn)

    def close_all(self) -> None:
        with self._lock:
            idle, self._idle = list(self._idle), deque()
        for conn, _ in idle:
            self._close(conn)


# --------------------------------------------------------------------- service
class EmailService:
    def __init__(self, cfg: SmtpSettings) -> None:
        self.cfg = cfg
        self.pool = SmtpPool(cfg)

    @property
    def configured(self) -> bool:
        return self.cfg.configured

    def build_message(self, to_addr: str, subject: str, text: str, html: str,
                      attachments: Optional[list[tuple[str, bytes]]] = None) -> MIMEMultipart:
        msg = MIMEMultipart("mixed")
        # Header values must never contain line breaks (header injection).
        clean = lambda v: " ".join(str(v).split())  # noqa: E731
        msg["From"] = formataddr((clean(self.cfg.from_name), self.cfg.from_addr))
        msg["To"] = clean(to_addr)
        msg["Subject"] = clean(subject)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.cfg.from_addr.split("@")[-1])
        body = MIMEMultipart("alternative")  # clients show the last part they understand: HTML
        body.attach(MIMEText(text, "plain", "utf-8"))
        body.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(body)
        for filename, data in attachments or []:
            part = MIMEApplication(data, _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
        return msg

    def send(self, msg: MIMEMultipart) -> None:
        if not self.configured:
            raise EmailError("Email is not configured (set SMTP_HOST and SMTP_FROM_ADDRESS).")
        last: Optional[Exception] = None
        for attempt in range(max(self.cfg.retries, 1)):
            try:
                with self.pool.connection() as conn:
                    conn.send_message(msg)
                return
            except smtplib.SMTPAuthenticationError as exc:
                raise EmailError("The mail server rejected the username or password.") from exc
            except smtplib.SMTPRecipientsRefused as exc:
                raise EmailError("The mail server refused the recipient address.") from exc
            except smtplib.SMTPResponseException as exc:
                if 500 <= exc.smtp_code < 600:  # permanent rejection: retrying cannot help
                    raise EmailError(f"The mail server rejected the message ({exc.smtp_code}).") from exc
                last = exc
            except (smtplib.SMTPException, OSError) as exc:  # disconnects, timeouts, DNS ...
                last = exc
            if attempt + 1 < self.cfg.retries:
                time.sleep(min(0.5 * 2 ** attempt, 4) + random.random() * 0.25)  # exponential backoff + jitter
        log.warning("SMTP send failed after retries: %s", last)
        raise EmailError(f"Could not reach the mail server: {last}", retryable=True) from last


@lru_cache(maxsize=1)
def get_email_service() -> EmailService:
    return EmailService(get_config().smtp)


# ------------------------------------------------------------------- templates
_HTML = Template("""\
<!doctype html><html><body style="margin:0;padding:24px;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#111827">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
<tr><td style="background:#2563eb;color:#ffffff;padding:20px 24px"><div style="font-size:20px;font-weight:bold">$company</div><div style="font-size:13px;opacity:.85">Invoice $number</div></td></tr>
<tr><td style="padding:24px;font-size:14px;line-height:1.55">$message_html
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0;border:1px solid #e5e7eb;border-radius:8px">
<tr><td style="padding:10px 14px;color:#6b7280">Invoice</td><td style="padding:10px 14px;text-align:right"><b>$number</b></td></tr>
<tr><td style="padding:10px 14px;color:#6b7280;border-top:1px solid #e5e7eb">Due date</td><td style="padding:10px 14px;text-align:right;border-top:1px solid #e5e7eb">$due</td></tr>
<tr><td style="padding:10px 14px;color:#6b7280;border-top:1px solid #e5e7eb">Total</td><td style="padding:10px 14px;text-align:right;border-top:1px solid #e5e7eb">$total</td></tr>
<tr><td style="padding:10px 14px;color:#6b7280;border-top:1px solid #e5e7eb;background:#eff6ff">Balance due</td><td style="padding:10px 14px;text-align:right;border-top:1px solid #e5e7eb;background:#eff6ff"><b>$balance</b></td></tr>
</table><p style="color:#6b7280;font-size:12px;margin:0">The invoice is attached as a PDF.</p></td></tr>
</table></td></tr></table></body></html>""")


def render_invoice_email_html(detail: InvoiceDetail, settings: AppSettings, message: str) -> str:
    paragraphs = "".join(f"<p style='margin:0 0 12px'>{escape(p).replace(chr(10), '<br>')}</p>"
                         for p in message.strip().split("\n\n") if p.strip())
    money = lambda v: escape(text_money(v, settings.currency))  # noqa: E731
    return _HTML.substitute(
        company=escape(settings.company_name), number=escape(detail.number), message_html=paragraphs,
        due=f"{detail.due_date:%b %d, %Y}", total=money(detail.total), balance=money(detail.balance),
    )


def default_email(detail: InvoiceDetail, settings: AppSettings, today: Optional[date] = None) -> tuple[str, str]:
    """Suggested (subject, plain-text body), worded by the invoice's current status."""
    status = detail.display_status(today)
    total, balance = text_money(detail.total, settings.currency), text_money(detail.balance, settings.currency)
    due = f"{detail.due_date:%b %d, %Y}"
    subject = f"Invoice {detail.number} from {settings.company_name}"
    if status == "Paid":
        subject = f"Receipt for invoice {detail.number} from {settings.company_name}"
        intro = f"Thank you for your payment. Your receipt for invoice {detail.number} ({total}) is attached."
    elif status == "Cancelled":
        intro = f"Invoice {detail.number} has been cancelled. A copy is attached for your records."
    else:
        late = " It is now overdue, so we would appreciate payment as soon as possible." if status == "Overdue" else ""
        part = f" (balance due {balance})" if status in ("Partially Paid", "Overdue") and detail.amount_paid else ""
        intro = f"Please find attached invoice {detail.number} for {total}{part}, due by {due}.{late}"
    body = (f"Hello {detail.client_name},\n\n{intro}\n\nIf you have any questions, just reply to this email."
            f"\n\nKind regards,\n{settings.company_name}")
    return subject, body
