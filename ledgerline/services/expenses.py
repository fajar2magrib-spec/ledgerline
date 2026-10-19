"""General business expenses (ads, tools, fees) that are not tied to a single invoice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd
from sqlalchemy import select

from ..db import read_session, write_session
from ..events import Actor, acting_as, log_event
from ..models import Expense
from ..security import require

EXPENSE_CATEGORIES = ("Software & tools", "Advertising", "Hosting & domains", "Payment fees", "Contractors",
                      "Office & equipment", "Travel", "Taxes & licences", "Other")
FRAME_COLUMNS = ["id", "spent_on", "vendor", "category", "amount", "note"]


class ExpenseError(ValueError):
    pass


@dataclass(frozen=True)
class ExpenseInput:
    spent_on: date
    vendor: str
    category: str
    amount: Decimal
    note: str = ""


def add_expense(actor: Actor, data: ExpenseInput) -> int:
    require(actor, "expense.manage")
    problems = []
    if not data.vendor.strip():
        problems.append("Enter who you paid (vendor).")
    if data.amount <= 0:
        problems.append("The amount must be more than zero.")
    if data.spent_on > date.today():
        problems.append("The date cannot be in the future.")
    if problems:
        raise ExpenseError(" ".join(problems))
    with acting_as(actor), write_session() as s:
        row = Expense(spent_on=data.spent_on, vendor=data.vendor.strip(), category=data.category, amount=data.amount,
                      note=data.note.strip() or None, created_by_id=actor.user_id)
        s.add(row)
        s.flush()
        log_event(s, "CREATE", f"Added expense {row.vendor} ({row.amount})", entity_type="Expense", entity_id=row.id, actor=actor)
        return row.id


def delete_expense(actor: Actor, expense_id: int) -> None:
    require(actor, "expense.manage")
    with acting_as(actor), write_session() as s:
        row = s.get(Expense, expense_id)
        if row is None:
            raise ExpenseError("Expense not found.")
        log_event(s, "DELETE", f"Deleted expense {row.vendor} ({row.amount})", entity_type="Expense", entity_id=row.id, actor=actor,
                  changes={"vendor": row.vendor, "amount": str(row.amount), "spent_on": str(row.spent_on)})
        s.delete(row)


def load_expenses_frame() -> pd.DataFrame:
    """Every expense, newest first (money as float, like the invoice frame)."""
    with read_session() as s:
        rows = s.execute(select(Expense.id, Expense.spent_on, Expense.vendor, Expense.category, Expense.amount, Expense.note)
                         .order_by(Expense.spent_on.desc(), Expense.id.desc())).all()
    df = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    df["amount"] = df["amount"].astype(float)
    df["spent_on"] = pd.to_datetime(df["spent_on"])
    df["note"] = df["note"].fillna("")
    return df
