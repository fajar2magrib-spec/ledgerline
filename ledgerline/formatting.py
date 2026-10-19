"""Currency and number formatting shared by the UI, PDFs, emails and messages.

Currencies are labels only: amounts are never converted. The symbol set is global
(an admin setting) so a PDF, an email and the dashboard always agree.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

Number = Union[int, float, Decimal]

CURRENCIES = {
    # Original five first (order is the order shown in the selector).
    "USD": "$", "PKR": "₨", "EUR": "€", "GBP": "£", "AED": "د.إ",
    # Added for international sale: major markets for small-business invoicing.
    "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "CHF": "CHF", "SEK": "kr", "NOK": "kr", "DKK": "kr",
    "SGD": "S$", "HKD": "HK$", "INR": "₹", "BDT": "৳", "LKR": "Rs", "SAR": "SAR", "EGP": "E£",
    "TRY": "₺", "ZAR": "R", "NGN": "₦", "KES": "KSh", "BRL": "R$", "MXN": "MX$", "MYR": "RM",
    "PHP": "₱", "THB": "฿", "PLN": "zł",
}
DEFAULT_CURRENCY = "USD"
# Symbols the built-in PDF fonts can draw. Others fall back to the currency code.
PDF_SAFE = ("USD", "EUR", "GBP")


def symbol(code: Optional[str]) -> str:
    return CURRENCIES.get(code or DEFAULT_CURRENCY, CURRENCIES[DEFAULT_CURRENCY])


def prefix(code: Optional[str]) -> str:
    """Symbol placed before a number (multi-letter symbols get a non-breaking gap)."""
    sym = symbol(code)
    return sym + "\u00a0" if len(sym) > 1 else sym


def money(value: Number, code: Optional[str], decimals: int = 2) -> str:
    """Amount with the currency symbol, for on-screen widgets."""
    value = float(value)
    return f"{'-' if value < 0 else ''}{prefix(code)}{abs(value):,.{decimals}f}"


def money_html(value: Number, code: Optional[str], decimals: int = 0) -> str:
    """Like ``money`` but escapes '$' so Streamlit markdown never sees LaTeX."""
    return money(value, code, decimals).replace("$", "&#36;")


def text_money(value: Number, code: Optional[str], decimals: int = 2) -> str:
    """Amount for PDFs, email and WhatsApp: symbol if the PDF fonts can draw it,
    otherwise the ISO code ("PKR 1,200.00")."""
    code = code if code in CURRENCIES else DEFAULT_CURRENCY
    value = float(value)
    lead = CURRENCIES[code] if code in PDF_SAFE else f"{code} "
    return f"{'-' if value < 0 else ''}{lead}{abs(value):,.{decimals}f}"


_NAME_CODES = ("%B", "%b", "%A", "%a")


def fdate(value, fmt: str = "%b %d, %Y", translate=None) -> str:
    """``value.strftime(fmt)`` with month and weekday names in the chosen language.

    ``translate`` defaults to the app translator; pass ``str`` to keep English (e.g. in a script)."""
    if value is None or value != value:           # None / NaT
        return ""
    if translate is None:
        try:
            from .i18n import t as translate
        except Exception:  # noqa: BLE001
            translate = str
    for code in _NAME_CODES:
        if code in fmt:
            name = translate(value.strftime(code)).replace("%", "%%")
            fmt = fmt.replace(code, name)
    return value.strftime(fmt)
