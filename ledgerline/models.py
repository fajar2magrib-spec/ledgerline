"""Relational schema (SQLAlchemy 2.0 declarative models).

users ──< auth_sessions
clients ──< invoices ──< invoice_items
                    └──< payments
audit_logs, outbound_messages, app_settings (supporting tables)

Design notes
* Money is stored as integer cents (``MONEY``) and handled as ``Decimal``.
* Totals on ``Invoice`` and ``InvoiceItem.line_*`` are CALCULATED by the ORM hook in
  ``events.py`` before every flush. Application code never sets them by hand.
* Invoices are soft-deleted (``deleted_at``). Financial records are never physically
  removed, and the audit trail keeps pointing at them.
* ``Invoice.version`` enables optimistic locking: if two people save the same invoice
  at once, the second save fails with ``StaleDataError`` instead of overwriting.
* CHECK constraints repeat the key business rules at database level, as a last line
  of defence against bad data from scripts or other tools.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from .calc import ZERO
from .dbtypes import MONEY, EncryptedString, ScaledDecimal


def utcnow() -> datetime:
    """Naive UTC timestamp. Every datetime in the database is UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)  # stored lower-case
    full_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[Optional[str]] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (CheckConstraint("role IN ('admin','manager','viewer')", name="ck_users_role"),)

    @validates("username")
    def _lower(self, _key: str, value: str) -> str:
        return value.strip().lower()


class AuthSession(Base):
    """One row per signed-in browser. The JWT cookie carries ``id`` (the jti claim);
    revoking the row (sign out, password reset) invalidates the cookie immediately."""

    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    name_key: Mapped[str] = mapped_column(String(160), unique=True)  # lower-case, enforces uniqueness
    email: Mapped[Optional[str]] = mapped_column(EncryptedString)
    phone: Mapped[Optional[str]] = mapped_column(EncryptedString)
    tax_id: Mapped[Optional[str]] = mapped_column(EncryptedString)
    address: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="client")

    @validates("name")
    def _set_key(self, _key: str, value: str) -> str:
        value = " ".join(value.split())
        self.name_key = value.lower()
        return value


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(60), default="General")
    payment_method: Mapped[str] = mapped_column(String(40), default="Bank Transfer")
    status: Mapped[str] = mapped_column(String(16), default="Pending", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    expenses: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)  # internal cost, never printed

    # Calculated by events.py - do not assign manually.
    subtotal: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    discount_total: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    tax_total: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    total: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    amount_paid: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)

    version: Mapped[int] = mapped_column(Integer, nullable=False)  # optimistic locking
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    deleted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    client: Mapped[Client] = relationship(back_populates="invoices")
    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.position",
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="Payment.paid_on",
    )

    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        CheckConstraint("status IN ('Pending','Partially Paid','Paid','Cancelled')", name="ck_invoices_status"),
        CheckConstraint("total >= 0 AND amount_paid >= 0 AND expenses >= 0", name="ck_invoices_amounts"),
        CheckConstraint("due_date >= issue_date", name="ck_invoices_dates"),
        Index("ix_invoices_client_status", "client_id", "status"),
    )

    @property
    def balance(self) -> Decimal:
        return ZERO if self.status == "Cancelled" else self.total - self.amount_paid


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(ScaledDecimal(3), default=Decimal(1))
    unit_price: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    discount_pct: Mapped[Decimal] = mapped_column(ScaledDecimal(2), default=ZERO)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    tax_rate: Mapped[Decimal] = mapped_column(ScaledDecimal(2), default=ZERO)  # percent: VAT/GST/sales tax

    # Calculated by events.py
    line_subtotal: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    line_discount: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    line_tax: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    line_total: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)

    invoice: Mapped[Invoice] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_items_quantity"),
        CheckConstraint("unit_price >= 0 AND discount_amount >= 0", name="ck_items_prices"),
        CheckConstraint("discount_pct >= 0 AND discount_pct <= 10000", name="ck_items_discount_pct"),  # cents of a percent
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 10000", name="ck_items_tax_rate"),
    )


class Payment(Base):
    """A single installment. An invoice may have many (partial payments)."""

    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    paid_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(40), default="Bank Transfer")
    reference: Mapped[Optional[str]] = mapped_column(String(120))
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")

    __table_args__ = (CheckConstraint("amount > 0", name="ck_payments_amount"),)


class AuditLog(Base):
    """Append-only trail written automatically by the ORM hook in events.py."""

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str] = mapped_column(String(64), default="system")  # snapshot survives user deletion
    action: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    invoice_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[Optional[Any]] = mapped_column(JSON)


class OutboundMessage(Base):
    """Durable queue for email / WhatsApp. A background worker delivers and retries."""

    __tablename__ = "outbound_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))  # email | whatsapp
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[Optional[Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)  # queued|sending|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[Optional[str]] = mapped_column(String(32))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(128))
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), index=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Product(Base):
    """A reusable product or service (price list) that can be dropped onto an invoice."""

    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    name_key: Mapped[str] = mapped_column(String(160), unique=True)  # lower-case, enforces uniqueness
    sku: Mapped[Optional[str]] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(40), default="General")
    description: Mapped[Optional[str]] = mapped_column(Text)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    tax_rate: Mapped[Decimal] = mapped_column(ScaledDecimal(2), default=ZERO)  # percent
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="ck_products_price"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 10000", name="ck_products_tax_rate"),
    )

    @validates("name")
    def _set_key(self, _key: str, value: str) -> str:
        value = " ".join(value.split())
        self.name_key = value.lower()
        return value


class Expense(Base):
    """Money the business spends that is not tied to one invoice (ads, tools, fees ...)."""

    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    spent_on: Mapped[date] = mapped_column(Date, index=True)
    vendor: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(40), default="Other")
    amount: Mapped[Decimal] = mapped_column(MONEY)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (CheckConstraint("amount > 0", name="ck_expenses_amount"),)


class BankAccount(Base):
    """A bank account, cash box, card or mobile wallet the business money moves through (Banking page)."""

    __tablename__ = "bank_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="bank")        # bank | cash | card | wallet
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BankTxn(Base):
    """One money movement on an account. ``direction`` is "in" (deposit) or "out" (withdrawal)."""

    __tablename__ = "bank_txns"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"), index=True)
    txn_date: Mapped[date] = mapped_column(Date, index=True)
    direction: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    description: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(40), default="Other")
    reference: Mapped[Optional[str]] = mapped_column(String(80))
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)         # ticked off against the bank statement
    source: Mapped[str] = mapped_column(String(16), default="manual")     # manual | payment | loan | transfer
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (CheckConstraint("amount > 0", name="ck_bank_txns_amount"),
                      CheckConstraint("direction in ('in','out')", name="ck_bank_txns_direction"))


class Loan(Base):
    """Money the business borrowed. Repayments are rows in ``loan_payments``."""

    __tablename__ = "loans"
    id: Mapped[int] = mapped_column(primary_key=True)
    lender: Mapped[str] = mapped_column(String(160))
    principal: Mapped[Decimal] = mapped_column(MONEY)
    interest_rate: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)   # yearly %, informational
    start_date: Mapped[date] = mapped_column(Date)
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    note: Mapped[Optional[str]] = mapped_column(Text)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (CheckConstraint("principal > 0", name="ck_loans_principal"),)


class LoanPayment(Base):
    __tablename__ = "loan_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id", ondelete="CASCADE"), index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    principal_part: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    interest_part: Mapped[Decimal] = mapped_column(MONEY, default=ZERO)
    note: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CustomValue(Base):
    """Value of an admin-defined extra field (Customize page) for one client or invoice.

    A separate table (instead of new columns) so existing databases need no migration:
    ``create_all`` simply adds this table on the next start."""

    __tablename__ = "custom_values"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(16))        # "client" | "invoice"
    entity_id: Mapped[int] = mapped_column(Integer)
    field_key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ux_custom_values", "entity", "entity_id", "field_key", unique=True),)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
