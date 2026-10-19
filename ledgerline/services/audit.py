"""Read access to the audit trail and CSV exports (both permission-checked)."""

from __future__ import annotations


import pandas as pd
from sqlalchemy import select

from ..db import read_session
from ..events import Actor
from ..models import AuditLog, Client, Invoice, InvoiceItem
from ..security import require


def query_audit(actor: Actor, *, limit: int = 500, username: str = "", entity_type: str = "",
                action: str = "", text: str = "") -> pd.DataFrame:
    require(actor, "audit.view")
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 5000))
    if username:
        stmt = stmt.where(AuditLog.username == username)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if text:
        stmt = stmt.where(AuditLog.summary.ilike(f"%{text}%"))
    with read_session() as s:
        rows = s.scalars(stmt).all()
    return pd.DataFrame([{"Time (UTC)": r.timestamp, "User": r.username, "Action": r.action, "Entity": r.entity_type,
                          "Entity ID": r.entity_id, "Invoice ID": r.invoice_id, "Summary": r.summary,
                          "Changes": r.changes} for r in rows])


def audit_filter_values(actor: Actor) -> dict[str, list[str]]:
    require(actor, "audit.view")
    with read_session() as s:
        return {
            "users": sorted(set(s.scalars(select(AuditLog.username).distinct()))),
            "entities": sorted(set(s.scalars(select(AuditLog.entity_type).distinct()))),
            "actions": sorted(set(s.scalars(select(AuditLog.action).distinct()))),
        }


def export_invoices_csv(actor: Actor) -> bytes:
    require(actor, "data.export")
    with read_session() as s:
        rows = s.execute(select(Invoice.number, Client.name, Invoice.issue_date, Invoice.due_date, Invoice.category,
                                Invoice.payment_method, Invoice.status, Invoice.subtotal, Invoice.discount_total,
                                Invoice.tax_total, Invoice.total, Invoice.amount_paid, Invoice.expenses)
                         .join(Client).where(Invoice.deleted_at.is_(None)).order_by(Invoice.issue_date)).all()
    cols = ["invoice_number", "client", "issue_date", "due_date", "category", "payment_method", "status",
            "subtotal", "discount_total", "tax_total", "total", "amount_paid", "expenses"]
    return pd.DataFrame(rows, columns=cols).to_csv(index=False).encode("utf-8")


def export_items_csv(actor: Actor) -> bytes:
    require(actor, "data.export")
    with read_session() as s:
        rows = s.execute(select(Invoice.number, InvoiceItem.position, InvoiceItem.description, InvoiceItem.quantity,
                                InvoiceItem.unit_price, InvoiceItem.discount_pct, InvoiceItem.discount_amount,
                                InvoiceItem.tax_rate, InvoiceItem.line_total)
                         .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
                         .where(Invoice.deleted_at.is_(None)).order_by(Invoice.number, InvoiceItem.position)).all()
    cols = ["invoice_number", "line", "description", "quantity", "unit_price", "discount_pct", "discount_amount",
            "tax_rate", "line_total"]
    return pd.DataFrame(rows, columns=cols).to_csv(index=False).encode("utf-8")
