"""Pure money maths: no database, no Streamlit.

The ORM hooks, the invoice form's live preview, the PDF engine and the tests all call
these functions, so a total can never differ between screen, PDF and database.

Rounding rule: every line is rounded to the cent (half up) and totals are the sum of the
rounded lines. That is what appears on the printed invoice, so it always adds up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)

STORED_STATUSES = ("Pending", "Partially Paid", "Paid", "Cancelled")
DISPLAY_STATUSES = ("Paid", "Partially Paid", "Pending", "Overdue", "Cancelled")


def D(value: Any) -> Decimal:
    """Forgiving Decimal conversion: None, '', NaN and bad text become 0."""
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value if value.is_finite() else Decimal(0)
    try:
        result = Decimal(str(value).strip().replace(",", ""))
    except Exception:
        return Decimal(0)
    return result if result.is_finite() else Decimal(0)


def q2(value: Any) -> Decimal:
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineAmounts:
    subtotal: Decimal  # quantity x unit price
    discount: Decimal  # percentage + flat amount, never more than the subtotal
    tax: Decimal       # charged on the discounted amount
    total: Decimal     # subtotal - discount + tax


def line_amounts(quantity: Any, unit_price: Any, discount_pct: Any = 0,
                 discount_amount: Any = 0, tax_rate: Any = 0) -> LineAmounts:
    subtotal = q2(D(quantity) * D(unit_price))
    discount = min(subtotal, q2(subtotal * D(discount_pct) / HUNDRED) + q2(discount_amount))
    taxable = subtotal - discount
    tax = q2(taxable * D(tax_rate) / HUNDRED)
    return LineAmounts(subtotal, discount, tax, taxable + tax)


@dataclass(frozen=True)
class InvoiceTotals:
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal

    @property
    def net_sales(self) -> Decimal:
        """Revenue excluding tax, after discounts."""
        return self.subtotal - self.discount_total


def invoice_totals(lines: Iterable[LineAmounts]) -> InvoiceTotals:
    lines = list(lines)
    return InvoiceTotals(
        subtotal=sum((l.subtotal for l in lines), ZERO),
        discount_total=sum((l.discount for l in lines), ZERO),
        tax_total=sum((l.tax for l in lines), ZERO),
        total=sum((l.total for l in lines), ZERO),
    )


def derive_status(total: Decimal, amount_paid: Decimal, cancelled: bool) -> str:
    """Stored status from the payments received. 'Overdue' is NOT stored because it
    changes with the calendar; see ``display_status``."""
    if cancelled:
        return "Cancelled"
    if total > 0 and amount_paid >= total:
        return "Paid"
    return "Partially Paid" if amount_paid > 0 else "Pending"


def display_status(status: str, due_date: date, today: date) -> str:
    if status in ("Pending", "Partially Paid") and due_date < today:
        return "Overdue"
    return status
