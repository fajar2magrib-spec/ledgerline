"""Reusable form pieces: the phone-number picker (country code drop-down) and the field renderer that
draws a form from its admin-edited layout (Customize page).

``client_form`` is used by every place that edits a client (Clients page, Invoice actions, New invoice),
so a field the administrator adds or hides shows up everywhere at once.
"""

from __future__ import annotations

import zlib
from datetime import date
from typing import Any, Optional

import streamlit as st

from .. import countries
from .. import customization as cz
from ..i18n import t
from ..services import invoices as inv
from .components import form_fields, settings

CLIENT_COLUMNS = 3


# ------------------------------------------------------------------------------------ phone
def phone_input(label: str, key: str, value: str = "", *, help: Optional[str] = None) -> str:
    """Country drop-down + number box. Returns ``"+92 3001234567"`` (or ``""`` when no number was typed).

    * the country list shows flag, name and dialling code; type in the box to search (e.g. "pak" or "92");
    * a leading 0 (0300 ...) is removed for you; a full number such as +44 7911 123456 pasted into the
      number box is understood too.
    """
    value = value or ""
    iso, local = countries.split_phone(value, settings().default_country)
    sig = zlib.crc32(value.encode("utf-8"))      # new stored value -> fresh widgets (no stale input)
    with st.container(key=f"ltr_{key}_{sig}"):
        chosen = st.selectbox(label, countries.ISO_CODES, index=countries.ISO_CODES.index(iso), key=f"{key}_{sig}_cc",
                              format_func=countries.label, help=help or t("Pick the country, then type the number without the country code."))
        number = st.text_input(label, value=local, key=f"{key}_{sig}_num", label_visibility="collapsed",
                               placeholder=t("Phone number, e.g. 300 1234567"), max_chars=24)
    return countries.join_phone(chosen, number)


# ------------------------------------------------------------------------------- field renderer
def _label(field: dict) -> str:
    text = cz.field_label(field, t)
    return f"{text} *" if field.get("required") else text


def _draw(field: dict, current: Any, key: str, options: Optional[list[str]], extra: dict) -> Any:
    """Draw ONE field and return its value."""
    kind, label = field["type"], _label(field)
    kwargs = dict(extra)
    key = kwargs.pop("key", key)
    if kind == "text":
        return st.text_input(label, value=str(current or ""), key=key, max_chars=kwargs.pop("max_chars", 255), **kwargs)
    if kind == "long_text":
        return st.text_area(label, value=str(current or ""), key=key, height=kwargs.pop("height", 84), **kwargs)
    if kind == "number":
        number = float(current or 0.0)
        return st.number_input(label, value=number, key=key, **{"step": 1.0, **kwargs})
    if kind == "date":
        return st.date_input(label, value=current if isinstance(current, date) else None, key=key, **kwargs)
    if kind == "checkbox":
        return st.checkbox(label, value=bool(current), key=key, **kwargs)
    if kind == "phone":
        return phone_input(label, key, str(current or ""), help=kwargs.get("help"))
    if kind == "dropdown":
        opts = list(options if options is not None else field.get("options") or [])
        if str(current or "") and str(current) not in opts:
            opts.append(str(current))
        blank = not field.get("required") and not field.get("builtin")
        if blank:
            opts = ["", *opts]
        index = opts.index(str(current)) if str(current or "") in opts else 0
        return st.selectbox(label, opts, index=index, key=key, format_func=lambda v: (t(v) if v else "—"), **kwargs)
    raise ValueError(f"Unknown field type {kind!r}")


def render_fields(fields: list[dict], current: dict[str, Any], prefix: str, *, columns: int = CLIENT_COLUMNS,
                  options: Optional[dict[str, list[str]]] = None, overrides: Optional[dict[str, dict]] = None) -> dict[str, Any]:
    """Draw every VISIBLE field in a grid (long text and phone numbers get their own full-width cell).

    Returns ``{field key: value}`` for visible fields only. Hidden fields are left out, so the caller keeps
    the stored value instead of erasing it.
    """
    options, overrides = options or {}, overrides or {}
    visible = [f for f in fields if f.get("visible", True)]
    out: dict[str, Any] = {}
    row: list[dict] = []

    def flush() -> None:
        if not row:
            return
        for col, field in zip(st.columns(len(row), gap="medium"), row):
            with col:
                out[field["key"]] = _draw(field, current.get(field["key"]), f"{prefix}_{field['key']}",
                                          options.get(field["key"]), overrides.get(field["key"], {}))
        row.clear()

    for field in visible:
        if field["type"] == "long_text":
            flush()
            out[field["key"]] = _draw(field, current.get(field["key"]), f"{prefix}_{field['key']}",
                                      options.get(field["key"]), overrides.get(field["key"], {}))
            continue
        row.append(field)
        if len(row) == columns:
            flush()
    flush()
    return out


# ----------------------------------------------------------------------------------- client form
def client_values(view: Any, custom: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Current values for ``client_form`` from a client object (or None for a blank form)."""
    base: dict[str, Any] = {}
    if view is not None:
        base = {"name": view.name, "email": view.email or "", "phone": view.phone or "",
                "tax_id": view.tax_id or "", "address": view.address or ""}
    for field in form_fields("client"):
        if not field["builtin"]:
            base[field["key"]] = cz.decode_value(field, (custom or {}).get(field["key"]))
    return base


def client_form(prefix: str, current: dict[str, Any]) -> tuple[inv.ClientInput, dict[str, str], list[str]]:
    """Draw the client fields (as configured on the Customize page).

    Returns ``(ClientInput, extra-field texts to store, problems)``. ``problems`` is meant to be shown
    only after the form was submitted.
    """
    fields = form_fields("client")
    values = render_fields(fields, current, prefix)
    merged = {**current, **values}                       # hidden fields keep what is stored
    problems = cz.missing_required(fields, values, t)
    problems = [t("{field} is required.", field=name) for name in problems]
    for field in fields:
        if field["type"] == "phone" and field.get("visible", True):
            bad = countries.phone_problem(str(values.get(field["key"], "")))
            if bad:
                problems.append(t(bad))
    data = inv.ClientInput(str(merged.get("name", "")), str(merged.get("email", "")), str(merged.get("phone", "")),
                           str(merged.get("tax_id", "")), str(merged.get("address", "")))
    texts = {f["key"]: cz.encode_value(f, values[f["key"]]) for f in fields
             if not f["builtin"] and f.get("visible", True) and f["key"] in values}
    return data, texts, problems


def extras_html(entity: str, texts: dict[str, str]) -> list[tuple[str, str]]:
    """(label, display text) for every extra field that has a value, in layout order."""
    out = []
    for field in form_fields(entity):
        if field["builtin"]:
            continue
        shown = cz.display_value(field, texts.get(field["key"]))
        if shown:
            out.append((cz.field_label(field, t), shown))
    return out
