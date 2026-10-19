"""Read-side reporting: the invoice frame, filters and KPIs.

One query loads every non-deleted invoice into a pandas frame; the dashboard then slices
and aggregates in memory. The UI caches ``load_invoice_frame`` with ``st.cache_data``
keyed by ``data_stamp()``, so changing a filter never touches the database.
(For millions of rows, push the date filter into the SQL query instead.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from ..db import read_session, retry_transient
from ..models import Client, Invoice, Payment

MONEY_COLUMNS = ["subtotal", "discount_total", "tax_total", "total", "amount_paid", "expenses"]


@retry_transient()
def data_stamp() -> tuple:
    """Cheap fingerprint that changes whenever an invoice or payment is added/edited."""
    with read_session() as s:
        inv = s.execute(select(func.count(Invoice.id), func.max(Invoice.updated_at), func.max(Invoice.version),
                               func.sum(Invoice.version))).one()
        pay = s.execute(select(func.count(Payment.id), func.max(Payment.id))).one()
    return tuple(str(v) for v in (*inv, *pay))


@retry_transient()
def load_invoice_frame(today: date) -> pd.DataFrame:
    """All invoices with client name and derived reporting columns (money as float)."""
    with read_session() as s:
        rows = s.execute(
            select(Invoice.id, Invoice.number, Client.name, Invoice.issue_date, Invoice.due_date,
                   Invoice.category, Invoice.payment_method, Invoice.status, Invoice.subtotal,
                   Invoice.discount_total, Invoice.tax_total, Invoice.total, Invoice.amount_paid,
                   Invoice.expenses)
            .join(Client, Client.id == Invoice.client_id)
            .where(Invoice.deleted_at.is_(None))
            .order_by(Invoice.issue_date, Invoice.id)
        ).all()
    columns = ["id", "number", "client", "issue_date", "due_date", "category", "payment_method",
               "status", *MONEY_COLUMNS]
    df = pd.DataFrame(rows, columns=columns)
    for col in MONEY_COLUMNS:
        df[col] = df[col].astype(float)
    df["issue_date"] = pd.to_datetime(df["issue_date"])
    df["due_date"] = pd.to_datetime(df["due_date"])

    active = df["status"] != "Cancelled"
    df["balance"] = np.where(active, df["total"] - df["amount_paid"], 0.0)
    overdue = df["status"].isin(["Pending", "Partially Paid"]) & (df["due_date"] < pd.Timestamp(today))
    df["display_status"] = np.where(overdue, "Overdue", df["status"])
    # Revenue = net sales (after discounts, before tax). Profit = revenue - internal expenses.
    df["revenue"] = np.where(active, df["subtotal"] - df["discount_total"], 0.0)
    df["profit"] = np.where(active, df["revenue"] - df["expenses"], 0.0)
    df["outstanding"] = df["balance"]
    df["collected"] = np.where(active, df["amount_paid"], 0.0)
    df["billed"] = np.where(active, df["total"], 0.0)
    return df


@dataclass(frozen=True)
class Filters:
    start: pd.Timestamp
    end: pd.Timestamp
    statuses: tuple[str, ...]
    query: str
    period: str


@dataclass(frozen=True)
class Kpis:
    revenue: float
    profit: float
    orders: int
    outstanding: float
    overdue_count: int
    open_count: int
    paid_count: int
    collection_rate: float
    margin: float
    tax: float


def resolve_period(period: str, custom: object, data_min: date, today: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    if period == "Today":
        start, end = today, today
    elif period == "This Week":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "This Month":
        start, end = today.replace(day=1), today
    elif period == "Last 30 Days":
        start, end = today - timedelta(days=29), today
    elif period == "All Time":
        start, end = data_min, today
    else:
        picked = [d for d in (list(custom) if isinstance(custom, (list, tuple)) else [custom]) if d]
        start, end = (picked[0], picked[-1]) if picked else (today - timedelta(days=29), today)
    return pd.Timestamp(start), pd.Timestamp(end)


def apply_filters(df: pd.DataFrame, flt: Filters, *, with_dates: bool = True) -> pd.DataFrame:
    out = df[df["display_status"].isin(flt.statuses)]
    query = flt.query.strip()
    if query:
        hit = (out["client"].str.contains(query, case=False, regex=False, na=False)
               | out["number"].str.contains(query, case=False, regex=False, na=False))
        out = out[hit]
    if with_dates:
        out = out[(out["issue_date"] >= flt.start) & (out["issue_date"] <= flt.end)]
    return out


def previous_window(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    length = (end - start).days + 1
    prev_end = start - pd.Timedelta(days=1)
    return prev_end - pd.Timedelta(days=length - 1), prev_end


def compute_kpis(df: pd.DataFrame) -> Kpis:
    revenue, profit = float(df["revenue"].sum()), float(df["profit"].sum())
    billed = float(df["billed"].sum())
    return Kpis(
        revenue=revenue, profit=profit, orders=int(len(df)), outstanding=float(df["outstanding"].sum()),
        overdue_count=int((df["display_status"] == "Overdue").sum()),
        open_count=int(df["display_status"].isin(["Pending", "Partially Paid"]).sum()),
        paid_count=int((df["display_status"] == "Paid").sum()),
        collection_rate=float(df["collected"].sum()) / billed * 100 if billed else 0.0,
        margin=profit / revenue * 100 if revenue else 0.0,
        tax=float(df.loc[df["status"] != "Cancelled", "tax_total"].sum()),
    )


def pct_change(current: float, previous: float) -> Optional[float]:
    return None if previous == 0 else (current - previous) / abs(previous) * 100
