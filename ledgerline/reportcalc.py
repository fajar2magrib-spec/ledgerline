"""Pure report maths on the invoice frame (pandas only: no database, no Streamlit).

Kept separate from ``services`` so it can be unit-tested without any infrastructure.
Input frames are the ones built by ``services.analytics.load_invoice_frame``.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

AGING_BUCKETS = ("Not yet due", "1-30 days", "31-60 days", "61-90 days", "90+ days")
OPEN_STATUSES = ("Pending", "Partially Paid")
_EPS = 0.005


def aging_bucket(days_overdue: int) -> str:
    """Bucket name for an invoice that is ``days_overdue`` days past its due date."""
    if days_overdue <= 0:
        return AGING_BUCKETS[0]
    if days_overdue <= 30:
        return AGING_BUCKETS[1]
    if days_overdue <= 60:
        return AGING_BUCKETS[2]
    if days_overdue <= 90:
        return AGING_BUCKETS[3]
    return AGING_BUCKETS[4]


def aging_detail(df: pd.DataFrame, today: date) -> pd.DataFrame:
    """Every open invoice with ``days_overdue`` and its ``bucket`` (oldest first)."""
    cols = ["number", "client", "due_date", "balance", "days_overdue", "bucket"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    open_ = df[df["status"].isin(OPEN_STATUSES) & (df["balance"] > _EPS)].copy()
    if open_.empty:
        return pd.DataFrame(columns=cols)
    days = (pd.Timestamp(today) - open_["due_date"]).dt.days.astype(int)
    open_["days_overdue"] = days.clip(lower=0)
    open_["bucket"] = [aging_bucket(int(d)) for d in days]
    return open_.sort_values(["days_overdue", "balance"], ascending=[False, False])[cols].reset_index(drop=True)


def aging_summary(detail: pd.DataFrame) -> pd.DataFrame:
    """One row per bucket (always all five, in order) with invoice count and balance."""
    out = pd.DataFrame({"bucket": list(AGING_BUCKETS)})
    if detail.empty:
        out["invoices"], out["balance"] = 0, 0.0
        return out
    grouped = detail.groupby("bucket").agg(invoices=("number", "count"), balance=("balance", "sum"))
    out = out.merge(grouped, how="left", left_on="bucket", right_index=True)
    out["invoices"] = out["invoices"].fillna(0).astype(int)
    out["balance"] = out["balance"].fillna(0.0)
    return out


def client_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per client: invoices (excluding cancelled), billed, collected, outstanding, overdue, last invoice."""
    cols = ["client", "invoices", "billed", "collected", "outstanding", "overdue", "last_invoice"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    live = df[df["status"] != "Cancelled"].copy()
    live["overdue_amt"] = np.where(live["display_status"] == "Overdue", live["balance"], 0.0)
    if live.empty:
        return pd.DataFrame(columns=cols)
    out = live.groupby("client", as_index=False).agg(
        invoices=("number", "count"), billed=("billed", "sum"), collected=("collected", "sum"),
        outstanding=("outstanding", "sum"), overdue=("overdue_amt", "sum"), last_invoice=("issue_date", "max"))
    return out.sort_values("billed", ascending=False).reset_index(drop=True)


def monthly_tax(df: pd.DataFrame) -> pd.DataFrame:
    """Tax charged per issue month (cancelled invoices excluded)."""
    cols = ["month", "invoices", "taxable", "tax", "total"]
    live = df[df["status"] != "Cancelled"] if not df.empty else df
    if live.empty:
        return pd.DataFrame(columns=cols)
    live = live.assign(month=live["issue_date"].dt.to_period("M").dt.to_timestamp(),
                       taxable=live["subtotal"] - live["discount_total"])
    out = live.groupby("month", as_index=False).agg(
        invoices=("number", "count"), taxable=("taxable", "sum"), tax=("tax_total", "sum"), total=("total", "sum"))
    return out.sort_values("month", ascending=False).reset_index(drop=True)


def statement(df: pd.DataFrame, client: str) -> pd.DataFrame:
    """Account statement for one client: invoices oldest first with a running balance."""
    cols = ["number", "issue_date", "due_date", "status", "total", "paid", "balance", "running_balance"]
    rows = df[(df["client"] == client) & (df["status"] != "Cancelled")].sort_values(["issue_date", "id"])
    if rows.empty:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({"number": rows["number"], "issue_date": rows["issue_date"], "due_date": rows["due_date"],
                        "status": rows["display_status"], "total": rows["total"], "paid": rows["amount_paid"],
                        "balance": rows["balance"]})
    out["running_balance"] = out["balance"].cumsum()
    return out.reset_index(drop=True)


def category_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue, internal expenses, profit and margin per category (cancelled excluded)."""
    cols = ["category", "invoices", "revenue", "expenses", "profit", "margin"]
    live = df[df["status"] != "Cancelled"] if not df.empty else df
    if live.empty:
        return pd.DataFrame(columns=cols)
    out = live.groupby("category", as_index=False).agg(
        invoices=("number", "count"), revenue=("revenue", "sum"), expenses=("expenses", "sum"), profit=("profit", "sum"))
    out["margin"] = np.where(out["revenue"] > 0, out["profit"] / out["revenue"] * 100, 0.0)
    return out.sort_values("revenue", ascending=False).reset_index(drop=True)


def profit_and_loss(df: pd.DataFrame, exp: pd.DataFrame) -> pd.DataFrame:
    """Monthly P&L: revenue - costs entered on invoices - general expenses = net profit (newest month first).

    ``df`` is the invoice frame; ``exp`` the expenses frame (``spent_on``, ``amount``). Either may be empty.
    """
    cols = ["month", "revenue", "invoice_costs", "other_costs", "net_profit"]
    parts = []
    live = df[df["status"] != "Cancelled"] if not df.empty else df
    if not live.empty:
        parts.append(live.assign(month=live["issue_date"].dt.to_period("M").dt.to_timestamp()).groupby("month").agg(
            revenue=("revenue", "sum"), invoice_costs=("expenses", "sum")))
    if exp is not None and not exp.empty:
        parts.append(exp.assign(month=exp["spent_on"].dt.to_period("M").dt.to_timestamp()).groupby("month").agg(
            other_costs=("amount", "sum")))
    if not parts:
        return pd.DataFrame(columns=cols)
    out = pd.concat(parts, axis=1, sort=True).fillna(0.0)
    for col in ("revenue", "invoice_costs", "other_costs"):
        if col not in out:
            out[col] = 0.0
    out["net_profit"] = out["revenue"] - out["invoice_costs"] - out["other_costs"]
    out.index.name = "month"
    return out.reset_index()[cols].sort_values("month", ascending=False).reset_index(drop=True)


def expense_by_category(exp: pd.DataFrame) -> pd.DataFrame:
    """Total spent per expense category, biggest first."""
    if exp is None or exp.empty:
        return pd.DataFrame(columns=["category", "count", "amount"])
    out = exp.groupby("category", as_index=False).agg(count=("id", "count"), amount=("amount", "sum"))
    return out.sort_values("amount", ascending=False).reset_index(drop=True)
