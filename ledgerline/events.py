"""ORM-level business rules: automatic totals, status and audit trail.

Both hooks run on EVERY flush of ANY ``Session``, regardless of which screen, script or
service made the change. That is the point: totals and audit rows cannot be forgotten.

before_flush           (1) recalculate item and invoice totals, amount paid and status
                       (2) record what is about to change (the "who" comes from a
                           ContextVar set by ``acting_as``)
after_flush_postexec   write the AuditLog rows once new rows have primary keys
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, Optional

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .calc import ZERO, derive_status, invoice_totals, line_amounts
from .models import AuditLog, Client, Invoice, InvoiceItem, Payment, User, utcnow


@dataclass(frozen=True)
class Actor:
    """Who is performing an action. Built from the verified session, never from user input."""
    user_id: Optional[int]
    username: str
    role: str


SYSTEM_ACTOR = Actor(None, "system", "admin")
current_actor: ContextVar[Optional[Actor]] = ContextVar("ledgerline_actor", default=None)


@contextmanager
def acting_as(actor: Actor) -> Iterator[Actor]:
    token = current_actor.set(actor)
    try:
        yield actor
    finally:
        current_actor.reset(token)


# --------------------------------------------------------------------------- totals
def _recompute_invoice(session: Session, inv: Invoice) -> None:
    gone = session.deleted
    items = [i for i in inv.items if i not in gone]
    payments = [p for p in inv.payments if p not in gone]

    lines = []
    for position, item in enumerate(items, start=1):
        amounts = line_amounts(item.quantity, item.unit_price, item.discount_pct,
                               item.discount_amount, item.tax_rate)
        item.position = position
        item.line_subtotal, item.line_discount = amounts.subtotal, amounts.discount
        item.line_tax, item.line_total = amounts.tax, amounts.total
        lines.append(amounts)

    totals = invoice_totals(lines)
    paid = sum((p.amount for p in payments), ZERO)

    if inv.deleted_at is None:
        if not items:
            raise ValueError("An invoice needs at least one line item.")
        if paid > totals.total:
            raise ValueError(
                f"Payments ({paid}) exceed the invoice total ({totals.total}). "
                "Remove a payment before reducing the invoice."
            )
    inv.subtotal, inv.discount_total = totals.subtotal, totals.discount_total
    inv.tax_total, inv.total, inv.amount_paid = totals.tax_total, totals.total, paid
    inv.status = derive_status(totals.total, paid, cancelled=inv.status == "Cancelled")


def _recalculate(session: Session) -> None:
    touched: dict[int, Invoice] = {}
    for obj in [*session.new, *session.dirty, *session.deleted]:
        inv = None
        if isinstance(obj, (InvoiceItem, Payment)):
            inv = obj.invoice
        elif isinstance(obj, Invoice) and obj not in session.deleted:
            inv = obj
        if inv is not None and inv not in session.deleted:
            touched[id(inv)] = inv
    for inv in touched.values():
        _recompute_invoice(session, inv)


# ----------------------------------------------------------------------------- audit
AUDIT_FIELDS: dict[type, tuple[str, ...]] = {
    Invoice: ("number", "client_id", "issue_date", "due_date", "category", "payment_method", "status",
              "notes", "expenses", "subtotal", "discount_total", "tax_total", "total",
              "amount_paid", "deleted_at"),
    InvoiceItem: ("description", "quantity", "unit_price", "discount_pct", "discount_amount", "tax_rate"),
    Payment: ("amount", "paid_on", "method", "reference", "note"),
    Client: ("name", "email", "phone", "tax_id", "address"),
    User: ("username", "full_name", "email", "role", "is_active", "password_hash"),
}
# Values of these fields are never copied into the log (personal data / secrets);
# the log only records THAT they changed.
SENSITIVE = {"password_hash", "email", "phone", "tax_id"}


@dataclass
class _Pending:
    action: str
    obj: Any
    summary: str
    changes: Optional[dict]
    actor: Optional[Actor]
    snapshot: bool = False  # take the snapshot AFTER the flush, when defaults/ids exist


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _snapshot(obj: Any) -> dict:
    return {f: ("(set)" if f in SENSITIVE else _json(getattr(obj, f, None)))
            for f in AUDIT_FIELDS[type(obj)]}


def _diff(obj: Any) -> dict:
    state, out = inspect(obj), {}
    for field in AUDIT_FIELDS[type(obj)]:
        hist = state.attrs[field].history
        if hist.has_changes():
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else getattr(obj, field)
            out[field] = "(changed)" if field in SENSITIVE else [_json(old), _json(new)]
    return out


def _describe(obj: Any) -> str:
    """Short human label: 'invoice INV-7', 'payment on invoice INV-7', 'client Acme'."""
    if isinstance(obj, Invoice):
        return f"invoice {obj.number}"
    if isinstance(obj, (InvoiceItem, Payment)):
        noun = "payment" if isinstance(obj, Payment) else "line item"
        inv = obj.invoice
        return f"{noun} on invoice {inv.number}" if inv is not None else noun
    if isinstance(obj, Client):
        return f"client {obj.name}"
    return f"user {getattr(obj, 'username', '')}"


def _collect_audit(session: Session) -> None:
    if session.info.get("skip_audit"):  # bulk imports log one summary entry instead
        return
    actor = current_actor.get()
    pending: list[_Pending] = session.info.setdefault("audit_pending", [])
    new_invoices = {id(o) for o in session.new if isinstance(o, Invoice)}

    def child_of_new(obj: Any) -> bool:
        return isinstance(obj, (InvoiceItem, Payment)) and id(obj.invoice) in new_invoices

    for obj in session.new:
        if type(obj) not in AUDIT_FIELDS or child_of_new(obj):
            continue
        kind = type(obj).__name__
        action = {"Payment": "PAYMENT_ADDED", "InvoiceItem": "ITEM_ADDED"}.get(kind, "CREATE")
        verb = "Added" if kind in ("Payment", "InvoiceItem") else "Created"
        pending.append(_Pending(action, obj, f"{verb} {_describe(obj)}", None, actor, snapshot=True))

    for obj in session.dirty:
        if type(obj) not in AUDIT_FIELDS or obj in session.new or not session.is_modified(obj):
            continue
        changes = _diff(obj)
        if not changes:
            continue
        kind = type(obj).__name__
        action = {"Payment": "PAYMENT_UPDATED", "InvoiceItem": "ITEM_UPDATED"}.get(kind, "UPDATE")
        if isinstance(obj, Invoice) and "deleted_at" in changes and changes["deleted_at"][0] is None:
            action = "DELETE"
        text = (f"Deleted {_describe(obj)}" if action == "DELETE"
                else f"Updated {_describe(obj)}: {', '.join(changes)}")
        pending.append(_Pending(action, obj, text, changes, actor))

    for obj in session.deleted:
        if type(obj) not in AUDIT_FIELDS:
            continue
        kind = type(obj).__name__
        action = {"Payment": "PAYMENT_DELETED", "InvoiceItem": "ITEM_REMOVED"}.get(kind, "DELETE")
        pending.append(_Pending(action, obj, f"Removed {_describe(obj)}", _snapshot(obj), actor))


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _ctx: Any, _instances: Any) -> None:
    _recalculate(session)
    _collect_audit(session)


@event.listens_for(Session, "after_flush_postexec")
def _after_flush_postexec(session: Session, _ctx: Any) -> None:
    pending: list[_Pending] = session.info.pop("audit_pending", [])
    for p in pending:
        obj = p.obj
        invoice_id = obj.id if isinstance(obj, Invoice) else getattr(getattr(obj, "invoice", None), "id", None)
        session.add(AuditLog(
            user_id=p.actor.user_id if p.actor else None,
            username=p.actor.username if p.actor else "system",
            action=p.action, entity_type=type(obj).__name__, entity_id=getattr(obj, "id", None),
            invoice_id=invoice_id, changes=_snapshot(obj) if p.snapshot else p.changes,
            summary=p.summary + (f" ({obj.total})" if p.snapshot and isinstance(obj, Invoice)
                                 else f" ({obj.amount})" if p.snapshot and isinstance(obj, Payment) else ""),
        ))


def log_event(session: Session, action: str, summary: str, *, entity_type: str = "System",
              entity_id: Optional[int] = None, invoice_id: Optional[int] = None,
              actor: Optional[Actor] = None, changes: Optional[dict] = None) -> None:
    """Explicit audit entry for events that are not row changes (login, send, import)."""
    actor = actor or current_actor.get()
    session.add(AuditLog(
        timestamp=utcnow(), user_id=actor.user_id if actor else None,
        username=actor.username if actor else "system", action=action, entity_type=entity_type,
        entity_id=entity_id, invoice_id=invoice_id, summary=summary, changes=changes,
    ))
