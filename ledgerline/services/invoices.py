"""Invoice, client and payment use-cases.

Rules for this layer
* Every public function takes the ``actor`` first and calls ``require`` itself, so
  permissions hold no matter which screen, script or API calls it.
* Functions return plain dataclasses/ids, never live ORM objects.
* Money maths is NOT done here: the ORM hook in events.py recalculates totals on flush.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..calc import D, display_status
from ..db import read_session, write_session
from ..events import Actor, acting_as
from ..models import Client, Invoice, InvoiceItem, Payment, utcnow
from ..security import PermissionDenied, has_permission, require
from ..config import get_config
from .settings import get_settings


class InvoiceError(ValueError):
    """Business-rule violation. ``errors`` holds one readable message per problem."""

    def __init__(self, errors: list[str] | str) -> None:
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__(" ".join(self.errors))


class ConcurrencyError(InvoiceError):
    """The invoice was changed by someone else after it was loaded."""


# ------------------------------------------------------------------------ inputs
@dataclass
class ItemInput:
    description: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal = Decimal(0)
    discount_pct: Decimal = Decimal(0)
    discount_amount: Decimal = Decimal(0)
    tax_rate: Decimal = Decimal(0)


@dataclass
class ClientInput:
    name: str
    email: str = ""
    phone: str = ""
    tax_id: str = ""
    address: str = ""


@dataclass
class InvoiceInput:
    issue_date: date
    due_date: date
    category: str
    payment_method: str
    items: list[ItemInput]
    client_id: Optional[int] = None
    new_client: Optional[ClientInput] = None
    number: Optional[str] = None
    expenses: Decimal = Decimal(0)
    notes: str = ""


def validate_invoice_input(data: InvoiceInput) -> list[str]:
    errors: list[str] = []
    if data.client_id is None and not (data.new_client and data.new_client.name.strip()):
        errors.append("Choose a client or enter a new client's name.")
    if data.due_date < data.issue_date:
        errors.append("The due date cannot be before the issue date.")
    if D(data.expenses) < 0:
        errors.append("Expenses cannot be negative.")
    if data.number is not None and data.number.strip() and not re.fullmatch(r"[A-Za-z0-9._/-]{1,40}", data.number.strip()):
        errors.append("Invoice number: up to 40 letters, digits, '.', '-', '_' or '/'.")
    if not data.items:
        errors.append("Add at least one line item.")
    for n, item in enumerate(data.items, start=1):
        if not item.description.strip():
            errors.append(f"Line {n}: description is required.")
        if D(item.quantity) <= 0:
            errors.append(f"Line {n}: quantity must be greater than 0.")
        if D(item.unit_price) < 0 or D(item.discount_amount) < 0:
            errors.append(f"Line {n}: prices and discounts cannot be negative.")
        if not 0 <= D(item.discount_pct) <= 100:
            errors.append(f"Line {n}: discount % must be between 0 and 100.")
        if not 0 <= D(item.tax_rate) <= 100:
            errors.append(f"Line {n}: tax rate must be between 0 and 100.")
    return errors


# ------------------------------------------------------------------------- views
@dataclass(frozen=True)
class ItemView:
    position: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount_pct: Decimal
    discount_amount: Decimal
    tax_rate: Decimal
    line_subtotal: Decimal
    line_discount: Decimal
    line_tax: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class PaymentView:
    id: int
    amount: Decimal
    paid_on: date
    method: str
    reference: str
    note: str


@dataclass(frozen=True)
class InvoiceDetail:
    id: int
    number: str
    client_id: int
    client_name: str
    client_email: str
    client_phone: str
    client_tax_id: str
    client_address: str
    issue_date: date
    due_date: date
    category: str
    payment_method: str
    status: str
    notes: str
    expenses: Decimal
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal
    version: int
    created_at: object
    items: tuple[ItemView, ...] = field(default_factory=tuple)
    payments: tuple[PaymentView, ...] = field(default_factory=tuple)

    @property
    def balance(self) -> Decimal:
        return Decimal("0.00") if self.status == "Cancelled" else self.total - self.amount_paid

    def display_status(self, today: Optional[date] = None) -> str:
        return display_status(self.status, self.due_date, today or date.today())


@dataclass(frozen=True)
class ClientView:
    id: int
    name: str
    email: str
    phone: str
    tax_id: str
    address: str


def _detail(inv: Invoice) -> InvoiceDetail:
    c = inv.client
    return InvoiceDetail(
        id=inv.id, number=inv.number, client_id=c.id, client_name=c.name, client_email=c.email or "",
        client_phone=c.phone or "", client_tax_id=c.tax_id or "", client_address=c.address or "",
        issue_date=inv.issue_date, due_date=inv.due_date, category=inv.category,
        payment_method=inv.payment_method, status=inv.status, notes=inv.notes or "",
        expenses=inv.expenses, subtotal=inv.subtotal, discount_total=inv.discount_total,
        tax_total=inv.tax_total, total=inv.total, amount_paid=inv.amount_paid, version=inv.version,
        created_at=inv.created_at,
        items=tuple(ItemView(i.position, i.description, i.quantity, i.unit_price, i.discount_pct,
                             i.discount_amount, i.tax_rate, i.line_subtotal, i.line_discount,
                             i.line_tax, i.line_total) for i in inv.items),
        payments=tuple(PaymentView(p.id, p.amount, p.paid_on, p.method, p.reference or "", p.note or "")
                       for p in inv.payments),
    )


def get_invoice_detail(invoice_id: int) -> Optional[InvoiceDetail]:
    with read_session() as s:
        inv = s.scalar(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.deleted_at.is_(None))
            .options(selectinload(Invoice.items), selectinload(Invoice.payments), selectinload(Invoice.client))
        )
        return _detail(inv) if inv else None


def list_clients() -> list[ClientView]:
    with read_session() as s:
        return [ClientView(c.id, c.name, c.email or "", c.phone or "", c.tax_id or "", c.address or "")
                for c in s.scalars(select(Client).order_by(func.lower(Client.name)))]


# ------------------------------------------------------------------- permissions
def can_edit(actor: Actor, created_at) -> bool:
    """Admins may edit anything; managers only invoices created inside the edit window."""
    if has_permission(actor.role, "invoice.edit_any"):
        return True
    if not has_permission(actor.role, "invoice.edit"):
        return False
    return (utcnow() - created_at) <= timedelta(days=get_config().manager_edit_window_days)


def _require_edit(actor: Actor, inv: Invoice) -> None:
    if not can_edit(actor, inv.created_at):
        days = get_config().manager_edit_window_days
        raise PermissionDenied(f"Only an administrator can change invoices older than {days} days.")


# --------------------------------------------------------------------- operations
def _next_number(s: Session, prefix: str) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    highest = 1000
    for (number,) in s.execute(select(Invoice.number).where(Invoice.number.like(f"{prefix}-%"))):
        m = pattern.match(number)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{highest + 1}"


def suggest_invoice_number() -> str:
    with read_session() as s:
        return _next_number(s, get_settings().invoice_prefix)


_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;.]{2,}$")


def _check_email(value: str) -> None:
    """Reject obviously mistyped addresses such as 'abc@gmail,com'. Blank is allowed."""
    value = (value or "").strip()
    if value and not _EMAIL_RE.match(value):
        raise InvoiceError(f"'{value}' is not a valid email address. Check for typos such as a comma instead of a dot.")


def _resolve_client(s: Session, data: InvoiceInput) -> Client:
    if data.client_id is not None:
        client = s.get(Client, data.client_id)
        if client is None:
            raise InvoiceError("The selected client no longer exists.")
        return client
    new = data.new_client
    assert new is not None
    _check_email(new.email)
    key = " ".join(new.name.split()).lower()
    existing = s.scalar(select(Client).where(Client.name_key == key))
    if existing:
        return existing  # same name typed again: reuse instead of failing
    client = Client(name=new.name, email=new.email.strip() or None, phone=new.phone.strip() or None,
                    tax_id=new.tax_id.strip() or None, address=new.address.strip() or None)
    s.add(client)
    return client


def _fill_item(item: InvoiceItem, src: ItemInput) -> None:
    item.description = src.description.strip()[:255]
    item.quantity, item.unit_price = D(src.quantity), D(src.unit_price)
    item.discount_pct, item.discount_amount = D(src.discount_pct), D(src.discount_amount)
    item.tax_rate = D(src.tax_rate)


def create_invoice(actor: Actor, data: InvoiceInput) -> int:
    require(actor, "invoice.create")
    errors = validate_invoice_input(data)
    if errors:
        raise InvoiceError(errors)
    prefix = get_settings().invoice_prefix
    typed = (data.number or "").strip()
    for attempt in range(3):
        try:
            with acting_as(actor), write_session() as s:
                number = typed or _next_number(s, prefix)
                if s.scalar(select(Invoice.id).where(func.lower(Invoice.number) == number.lower())):
                    raise InvoiceError(f"Invoice number {number} already exists.")
                inv = Invoice(
                    number=number, client=_resolve_client(s, data), issue_date=data.issue_date,
                    due_date=data.due_date, category=data.category, payment_method=data.payment_method,
                    notes=data.notes.strip() or None, expenses=D(data.expenses), created_by_id=actor.user_id,
                )
                for src in data.items:
                    item = InvoiceItem()
                    _fill_item(item, src)
                    inv.items.append(item)
                s.add(inv)
                s.flush()
                return inv.id
        except IntegrityError as exc:
            # Two people took the same auto-number at the same moment: pick the next one.
            if typed or attempt == 2:
                raise InvoiceError("That invoice number is already in use.") from exc
    raise InvoiceError("Could not allocate an invoice number.")  # pragma: no cover


def update_invoice(actor: Actor, invoice_id: int, data: InvoiceInput, expected_version: Optional[int] = None) -> None:
    require(actor, "invoice.edit")
    errors = validate_invoice_input(data)
    if errors:
        raise InvoiceError(errors)
    from sqlalchemy.orm.exc import StaleDataError

    try:
        with acting_as(actor), write_session() as s:
            inv = s.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.deleted_at.is_(None))
                           .options(selectinload(Invoice.items), selectinload(Invoice.payments)))
            if inv is None:
                raise InvoiceError("This invoice no longer exists.")
            _require_edit(actor, inv)
            if expected_version is not None and inv.version != expected_version:
                raise ConcurrencyError("Someone else changed this invoice. Reload it and try again.")
            number = (data.number or inv.number).strip()
            if number.lower() != inv.number.lower() and s.scalar(
                    select(Invoice.id).where(func.lower(Invoice.number) == number.lower())):
                raise InvoiceError(f"Invoice number {number} already exists.")
            inv.number, inv.client = number, _resolve_client(s, data)
            inv.issue_date, inv.due_date = data.issue_date, data.due_date
            inv.category, inv.payment_method = data.category, data.payment_method
            inv.notes, inv.expenses = data.notes.strip() or None, D(data.expenses)
            # Update lines in place (cleaner audit trail), then add or drop the difference.
            for index, src in enumerate(data.items):
                if index < len(inv.items):
                    _fill_item(inv.items[index], src)
                else:
                    item = InvoiceItem()
                    _fill_item(item, src)
                    inv.items.append(item)
            del inv.items[len(data.items):]
    except StaleDataError as exc:
        raise ConcurrencyError("Someone else changed this invoice. Reload it and try again.") from exc


def _load_for_update(s: Session, invoice_id: int) -> Invoice:
    inv = s.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.deleted_at.is_(None))
                   .options(selectinload(Invoice.items), selectinload(Invoice.payments)))
    if inv is None:
        raise InvoiceError("This invoice no longer exists.")
    return inv


def set_cancelled(actor: Actor, invoice_id: int, cancelled: bool) -> None:
    require(actor, "invoice.cancel")
    with acting_as(actor), write_session() as s:
        inv = _load_for_update(s, invoice_id)
        _require_edit(actor, inv)
        inv.status = "Cancelled" if cancelled else "Pending"  # events.py re-derives it


def delete_invoice(actor: Actor, invoice_id: int) -> None:
    """Soft delete: the row stays for the audit trail but disappears from every screen."""
    require(actor, "invoice.delete")
    with acting_as(actor), write_session() as s:
        inv = _load_for_update(s, invoice_id)
        inv.deleted_at, inv.deleted_by_id = utcnow(), actor.user_id


def add_payment(actor: Actor, invoice_id: int, amount: Decimal, paid_on: date, method: str,
                reference: str = "", note: str = "") -> int:
    require(actor, "payment.record")
    amount = D(amount).quantize(Decimal("0.01"))
    with acting_as(actor), write_session() as s:
        inv = _load_for_update(s, invoice_id)
        if inv.status == "Cancelled":
            raise InvoiceError("A cancelled invoice cannot receive payments.")
        if amount <= 0:
            raise InvoiceError("Enter a payment amount greater than zero.")
        if amount > inv.total - inv.amount_paid:
            raise InvoiceError(f"The payment is larger than the balance due ({inv.total - inv.amount_paid}).")
        payment = Payment(amount=amount, paid_on=paid_on, method=method, reference=reference.strip() or None,
                          note=note.strip() or None, created_by_id=actor.user_id)
        inv.payments.append(payment)
        s.flush()
        return payment.id


def delete_payment(actor: Actor, payment_id: int) -> None:
    require(actor, "payment.delete")
    with acting_as(actor), write_session() as s:
        payment = s.get(Payment, payment_id)
        if payment is None:
            raise InvoiceError("Payment not found.")
        payment.invoice.payments.remove(payment)  # keep the collection in sync for the totals hook
        s.delete(payment)


def create_client(actor: Actor, data: ClientInput) -> int:
    """Add a client without creating an invoice (used by the Clients page)."""
    require(actor, "client.edit")
    name = " ".join(data.name.split())
    if not name:
        raise InvoiceError("The client name is required.")
    _check_email(data.email)
    with acting_as(actor), write_session() as s:
        if s.scalar(select(Client.id).where(Client.name_key == name.lower())):
            raise InvoiceError(f"A client named {name} already exists.")
        client = Client(name=name, email=data.email.strip() or None, phone=data.phone.strip() or None,
                        tax_id=data.tax_id.strip() or None, address=data.address.strip() or None)
        s.add(client)
        s.flush()
        return client.id


def update_client(actor: Actor, client_id: int, data: ClientInput) -> None:
    require(actor, "client.edit")
    if not data.name.strip():
        raise InvoiceError("The client name is required.")
    _check_email(data.email)
    with acting_as(actor), write_session() as s:
        client = s.get(Client, client_id)
        if client is None:
            raise InvoiceError("Client not found.")
        taken = s.scalar(select(Client.id).where(Client.name_key == " ".join(data.name.split()).lower(),
                                                 Client.id != client_id))
        if taken:
            raise InvoiceError("Another client already uses that name.")
        client.name = data.name
        client.email, client.phone = data.email.strip() or None, data.phone.strip() or None
        client.tax_id, client.address = data.tax_id.strip() or None, data.address.strip() or None
