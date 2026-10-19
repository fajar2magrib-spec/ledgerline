"""Read-only data loaders for the Clients, Payments and Reports pages.

The maths lives in ``ledgerline.reportcalc`` (pure pandas). Money leaves this layer as float,
exactly like ``analytics.load_invoice_frame``.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from ..db import read_session
from ..models import Client, Invoice, Payment

PAYMENT_COLUMNS = ["id", "paid_on", "invoice_id", "invoice", "client", "amount", "method", "reference"]


def load_payments_frame() -> pd.DataFrame:
    """Every payment on a live (not deleted) invoice, newest first."""
    with read_session() as s:
        rows = s.execute(
            select(Payment.id, Payment.paid_on, Invoice.id, Invoice.number, Client.name, Payment.amount,
                   Payment.method, Payment.reference)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .join(Client, Client.id == Invoice.client_id)
            .where(Invoice.deleted_at.is_(None))
            .order_by(Payment.paid_on.desc(), Payment.id.desc())
        ).all()
    df = pd.DataFrame(rows, columns=PAYMENT_COLUMNS)
    df["amount"] = df["amount"].astype(float)
    df["paid_on"] = pd.to_datetime(df["paid_on"])
    df["reference"] = df["reference"].fillna("")
    return df
