"""Import data from Ledgerline v2.0 (flat ``database.db`` / ``invoices.csv``).

Each old row becomes one invoice with ONE line item (gross price, flat discount), so the
old totals are reproduced exactly. Paid rows get one full payment, so they stay Paid.
Duplicates (same invoice number) are skipped, which makes the import safe to run twice.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import select

from ..calc import D
from ..db import write_session
from ..events import Actor, acting_as, log_event
from ..models import Client, Invoice, InvoiceItem, Payment
from ..security import require
from .settings import get_settings

REQUIRED = ["invoice_id", "client", "date", "category", "payment_method", "status", "gross", "discount", "expenses"]


@dataclass
class ImportReport:
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _read_sources(db_path: Optional[Path], csv_path: Optional[Path]) -> tuple[pd.DataFrame, dict[str, tuple[str, str]]]:
    frames, contacts = [], {}
    if db_path and db_path.exists():
        with sqlite3.connect(db_path) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "invoices" in tables and set(REQUIRED) <= {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}:
                frames.append(pd.read_sql_query(f"SELECT {', '.join(REQUIRED)} FROM invoices", conn))
            if "client_contacts" in tables:
                contacts = {c: (p or "", e or "") for c, p, e in conn.execute("SELECT client, phone, email FROM client_contacts")}
    if csv_path and csv_path.exists():
        raw = pd.read_csv(csv_path)
        if set(REQUIRED) <= set(raw.columns):
            frames.append(raw[REQUIRED])
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=REQUIRED)
    return df.drop_duplicates(subset="invoice_id"), contacts


def import_legacy(actor: Actor, db_path: Optional[Path], csv_path: Optional[Path]) -> ImportReport:
    require(actor, "data.import")
    df, contacts = _read_sources(db_path, csv_path)
    report, terms = ImportReport(), get_settings().payment_terms_days
    with acting_as(actor), write_session() as s:
        s.info["skip_audit"] = True
        known = {n.lower() for n in s.scalars(select(Invoice.number))}
        clients = {c.name_key: c for c in s.scalars(select(Client))}
        for row in df.itertuples():
            number = str(row.invoice_id).strip()
            if not number or number.lower() in known:
                report.skipped += 1
                continue
            created_client = None
            try:
                with s.begin_nested():  # savepoint: a bad row is undone, the rest still import
                    issued = pd.to_datetime(row.date).date()
                    name = " ".join(str(row.client).split())
                    client = clients.get(name.lower())
                    if client is None:
                        phone, email = contacts.get(str(row.client), ("", ""))
                        client = created_client = Client(name=name, phone=phone or None, email=email or None)
                        s.add(client)
                    status = str(row.status).strip().title()
                    inv = Invoice(number=number, client=client, issue_date=issued, due_date=issued + timedelta(days=terms),
                                  category=str(row.category), payment_method=str(row.payment_method),
                                  expenses=D(row.expenses), created_by_id=actor.user_id,
                                  notes="Imported from Ledgerline v2.0")
                    inv.items.append(InvoiceItem(description=f"{row.category} (imported)", quantity=Decimal(1),
                                                 unit_price=D(row.gross), discount_amount=D(row.discount)))
                    s.add(inv)
                    s.flush()
                    if status == "Cancelled":
                        inv.status = "Cancelled"
                    elif status == "Paid" and inv.total > 0:
                        inv.payments.append(Payment(amount=inv.total, paid_on=issued, method=inv.payment_method,
                                                    note="Imported as fully paid"))
                    s.flush()
                if created_client is not None:
                    clients[name.lower()] = created_client
                known.add(number.lower())
                report.created += 1
            except Exception as exc:  # one bad row must not abort the whole import
                report.errors.append(f"{number}: {exc}")
        log_event(s, "LEGACY_IMPORT", f"Imported {report.created} invoices ({report.skipped} skipped, "
                  f"{len(report.errors)} errors)", entity_type="System", actor=actor)
    return report
