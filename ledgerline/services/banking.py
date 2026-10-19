"""Banking (accounts, transactions, reconciliation), receiving customer payments into an account, and business loans.

Everything here is plain functions over the ORM, like the other services. Money is Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import func, select

from ..calc import ZERO
from ..db import read_session, write_session
from ..events import Actor, acting_as, log_event
from ..models import BankAccount, BankTxn, Loan, LoanPayment
from ..security import require
from . import expenses as expense_svc
from . import invoices as inv

ACCOUNT_KINDS = ("bank", "cash", "card", "wallet")
TXN_CATEGORIES = ("Customer payment", "Loan", "Transfer", "Owner deposit", "Owner withdrawal", "Bank charges",
                  "Supplier payment", "Salary", "Rent & utilities", "Tax payment", "Other")
LOAN_INTEREST_CATEGORY = "Taxes & licences"      # interest is booked as an expense under the closest built-in category


class BankingError(ValueError):
    pass


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception as exc:  # noqa: BLE001
        raise BankingError("Enter a valid amount.") from exc


# ------------------------------------------------------------------------------------- accounts
def create_account(actor: Actor, name: str, kind: str, opening_balance) -> int:
    require(actor, "bank.manage")
    name = " ".join((name or "").split())
    if not name:
        raise BankingError("Enter a name for the account.")
    if kind not in ACCOUNT_KINDS:
        raise BankingError("Choose a valid account type.")
    with acting_as(actor), write_session() as s:
        if s.scalar(select(BankAccount.id).where(func.lower(BankAccount.name) == name.lower())):
            raise BankingError("An account with that name already exists.")
        row = BankAccount(name=name, kind=kind, opening_balance=_money(opening_balance or 0))
        s.add(row)
        s.flush()
        log_event(s, "CREATE", f"Added account {name}", entity_type="BankAccount", entity_id=row.id, actor=actor)
        return row.id


def accounts_frame() -> pd.DataFrame:
    """Every account with its balance, cleared balance and number of unreconciled transactions."""
    with read_session() as s:
        accounts = s.execute(select(BankAccount.id, BankAccount.name, BankAccount.kind, BankAccount.opening_balance,
                                    BankAccount.is_active).order_by(BankAccount.name)).all()
        sums = s.execute(select(BankTxn.account_id, BankTxn.direction, BankTxn.cleared, func.sum(BankTxn.amount), func.count())
                         .group_by(BankTxn.account_id, BankTxn.direction, BankTxn.cleared)).all()
    rows = []
    for acc_id, name, kind, opening, active in accounts:
        balance, cleared_balance, open_items = Decimal(opening or 0), Decimal(opening or 0), 0
        for a_id, direction, cleared, total, count in sums:
            if a_id != acc_id:
                continue
            signed = Decimal(total) if direction == "in" else -Decimal(total)
            balance += signed
            if cleared:
                cleared_balance += signed
            else:
                open_items += count
        rows.append((acc_id, name, kind, float(balance), float(cleared_balance), open_items, active))
    return pd.DataFrame(rows, columns=["id", "name", "kind", "balance", "cleared_balance", "uncleared", "is_active"])


# --------------------------------------------------------------------------------- transactions
def add_txn(actor: Actor, account_id: int, txn_date: date, direction: str, amount, description: str,
            category: str = "Other", reference: str = "", source: str = "manual", cleared: bool = False,
            session=None) -> int:
    require(actor, "bank.manage")
    amount = _money(amount)
    problems = []
    if direction not in ("in", "out"):
        problems.append("Choose money in or money out.")
    if amount <= 0:
        problems.append("The amount must be more than zero.")
    if txn_date > date.today():
        problems.append("The date cannot be in the future.")
    if problems:
        raise BankingError(" ".join(problems))

    def build(s) -> int:
        if s.get(BankAccount, account_id) is None:
            raise BankingError("Account not found.")
        row = BankTxn(account_id=account_id, txn_date=txn_date, direction=direction, amount=amount,
                      description=(description or "").strip()[:200], category=category, reference=(reference or "").strip() or None,
                      source=source, cleared=cleared)
        s.add(row)
        s.flush()
        log_event(s, "CREATE", f"Bank {'deposit' if direction == 'in' else 'withdrawal'} {amount}", entity_type="BankTxn",
                  entity_id=row.id, actor=actor)
        return row.id

    if session is not None:
        return build(session)
    with acting_as(actor), write_session() as s:
        return build(s)


def transfer(actor: Actor, from_id: int, to_id: int, txn_date: date, amount, note: str = "") -> None:
    require(actor, "bank.manage")
    if from_id == to_id:
        raise BankingError("Choose two different accounts.")
    amount = _money(amount)
    with acting_as(actor), write_session() as s:
        names = {a.id: a.name for a in s.scalars(select(BankAccount).where(BankAccount.id.in_([from_id, to_id])))}
        if len(names) != 2:
            raise BankingError("Account not found.")
        add_txn(actor, from_id, txn_date, "out", amount, f"Transfer to {names[to_id]}. {note}".strip(), "Transfer", source="transfer", session=s)
        add_txn(actor, to_id, txn_date, "in", amount, f"Transfer from {names[from_id]}. {note}".strip(), "Transfer", source="transfer", session=s)


def txns_frame(account_id: Optional[int] = None) -> pd.DataFrame:
    with read_session() as s:
        q = (select(BankTxn.id, BankTxn.account_id, BankAccount.name, BankTxn.txn_date, BankTxn.direction, BankTxn.amount,
                    BankTxn.description, BankTxn.category, BankTxn.reference, BankTxn.cleared, BankTxn.source)
             .join(BankAccount, BankAccount.id == BankTxn.account_id).order_by(BankTxn.txn_date.desc(), BankTxn.id.desc()))
        if account_id:
            q = q.where(BankTxn.account_id == account_id)
        rows = s.execute(q).all()
    df = pd.DataFrame(rows, columns=["id", "account_id", "account", "txn_date", "direction", "amount", "description",
                                     "category", "reference", "cleared", "source"])
    df["amount"] = df["amount"].astype(float)
    df["signed"] = df["amount"].where(df["direction"] == "in", -df["amount"])
    df["reference"] = df["reference"].fillna("")
    return df


def set_cleared(actor: Actor, cleared_ids: Iterable[int], uncleared_ids: Iterable[int]) -> None:
    """Reconciliation: tick off (or un-tick) transactions against the bank statement."""
    require(actor, "bank.manage")
    with acting_as(actor), write_session() as s:
        for txn_id, state in [(i, True) for i in cleared_ids] + [(i, False) for i in uncleared_ids]:
            row = s.get(BankTxn, int(txn_id))
            if row is not None and row.cleared != state:
                row.cleared = state
        log_event(s, "UPDATE", "Reconciled bank transactions", entity_type="BankTxn", actor=actor)


def delete_txn(actor: Actor, txn_id: int) -> None:
    require(actor, "bank.manage")
    with acting_as(actor), write_session() as s:
        row = s.get(BankTxn, txn_id)
        if row is None:
            raise BankingError("Transaction not found.")
        if row.source != "manual":
            raise BankingError("Only transactions you typed in yourself can be deleted here.")
        log_event(s, "DELETE", f"Deleted bank transaction {row.amount}", entity_type="BankTxn", entity_id=row.id, actor=actor)
        s.delete(row)


# ------------------------------------------------------------------------- receive a customer payment
def receive_payment(actor: Actor, invoice_id: int, amount, paid_on: date, method: str, account_id: Optional[int],
                    reference: str = "") -> None:
    """Record money received against an invoice and (optionally) deposit it into a bank account."""
    require(actor, "payment.record")
    amount = _money(amount)
    inv.add_payment(actor, invoice_id, amount, paid_on, method, reference=reference)
    if account_id:
        detail = inv.get_invoice_detail(invoice_id)
        label = f"Payment for {detail.number}" if detail else "Customer payment"
        add_txn(actor, account_id, paid_on, "in", amount, label, "Customer payment", reference, source="payment")


# ----------------------------------------------------------------------------------------- loans
@dataclass(frozen=True)
class LoanInput:
    lender: str
    principal: Decimal
    interest_rate: Decimal
    start_date: date
    term_months: int
    account_id: Optional[int] = None     # deposit the money received into this account
    note: str = ""


def emi(principal: float, yearly_rate: float, months: int) -> float:
    """Equal monthly instalment (0% interest = straight division)."""
    if months <= 0:
        return 0.0
    r = yearly_rate / 1200.0
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


def create_loan(actor: Actor, data: LoanInput) -> int:
    require(actor, "bank.manage")
    problems = []
    if not data.lender.strip():
        problems.append("Enter the name of the lender.")
    if _money(data.principal) <= 0:
        problems.append("The loan amount must be more than zero.")
    if not 0 <= data.interest_rate <= 100:
        problems.append("The interest rate must be between 0 and 100.")
    if not 1 <= data.term_months <= 600:
        problems.append("The loan term must be between 1 and 600 months.")
    if data.start_date > date.today():
        problems.append("The date cannot be in the future.")
    if problems:
        raise BankingError(" ".join(problems))
    with acting_as(actor), write_session() as s:
        loan = Loan(lender=data.lender.strip(), principal=_money(data.principal), interest_rate=_money(data.interest_rate),
                    start_date=data.start_date, term_months=data.term_months, note=data.note.strip() or None)
        s.add(loan)
        s.flush()
        log_event(s, "CREATE", f"Added loan from {loan.lender} ({loan.principal})", entity_type="Loan", entity_id=loan.id, actor=actor)
        if data.account_id:
            add_txn(actor, data.account_id, data.start_date, "in", loan.principal, f"Loan from {loan.lender}", "Loan",
                    source="loan", session=s)
        return loan.id


def repay_loan(actor: Actor, loan_id: int, paid_on: date, principal_part, interest_part, account_id: Optional[int] = None,
               note: str = "") -> None:
    """Record a repayment. The interest part is also booked as an expense so profit shows the real cost."""
    require(actor, "bank.manage")
    principal_part, interest_part = _money(principal_part or 0), _money(interest_part or 0)
    if principal_part < 0 or interest_part < 0 or principal_part + interest_part <= 0:
        raise BankingError("Enter the repayment amount.")
    if paid_on > date.today():
        raise BankingError("The date cannot be in the future.")
    with acting_as(actor), write_session() as s:
        loan = s.get(Loan, loan_id)
        if loan is None:
            raise BankingError("Loan not found.")
        repaid = s.scalar(select(func.coalesce(func.sum(LoanPayment.principal_part), 0)).where(LoanPayment.loan_id == loan_id)) or ZERO
        if principal_part > loan.principal - Decimal(repaid):
            raise BankingError("The principal part is larger than what is still owed on this loan.")
        s.add(LoanPayment(loan_id=loan_id, paid_on=paid_on, principal_part=principal_part, interest_part=interest_part,
                          note=(note or "").strip()[:200] or None))
        if Decimal(repaid) + principal_part >= loan.principal:
            loan.closed = True
        log_event(s, "CREATE", f"Loan repayment to {loan.lender} ({principal_part + interest_part})", entity_type="Loan",
                  entity_id=loan.id, actor=actor)
        if account_id:
            add_txn(actor, account_id, paid_on, "out", principal_part + interest_part, f"Loan repayment: {loan.lender}", "Loan",
                    source="loan", session=s)
        lender = loan.lender
    if interest_part > 0:
        expense_svc.add_expense(actor, expense_svc.ExpenseInput(paid_on, f"Loan interest: {lender}", LOAN_INTEREST_CATEGORY, interest_part))


def loans_frame() -> pd.DataFrame:
    with read_session() as s:
        loans = s.execute(select(Loan.id, Loan.lender, Loan.principal, Loan.interest_rate, Loan.start_date, Loan.term_months,
                                 Loan.closed, Loan.note).order_by(Loan.closed, Loan.start_date.desc())).all()
        paid = {r[0]: (r[1], r[2]) for r in s.execute(select(LoanPayment.loan_id, func.sum(LoanPayment.principal_part),
                                                              func.sum(LoanPayment.interest_part)).group_by(LoanPayment.loan_id))}
    rows = []
    for lid, lender, principal, rate, start, term, closed, note in loans:
        pr, it = paid.get(lid, (0, 0))
        pr, it = float(pr or 0), float(it or 0)
        p, r = float(principal), float(rate)
        rows.append((lid, lender, p, r, start, term, bool(closed), p - pr, pr, it, emi(p, r, term), note or ""))
    return pd.DataFrame(rows, columns=["id", "lender", "principal", "rate", "start_date", "term_months", "closed", "outstanding",
                                       "principal_paid", "interest_paid", "emi", "note"])


def loan_payments_frame(loan_id: int) -> pd.DataFrame:
    with read_session() as s:
        rows = s.execute(select(LoanPayment.paid_on, LoanPayment.principal_part, LoanPayment.interest_part, LoanPayment.note)
                         .where(LoanPayment.loan_id == loan_id).order_by(LoanPayment.paid_on.desc(), LoanPayment.id.desc())).all()
    df = pd.DataFrame(rows, columns=["paid_on", "principal", "interest", "note"])
    df[["principal", "interest"]] = df[["principal", "interest"]].astype(float)
    df["note"] = df["note"].fillna("")
    return df
