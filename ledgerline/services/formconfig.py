"""Storage for everything the Customize page changes (form layouts, dropdown lists, theme,
languages) plus the values of admin-defined extra fields.

Layouts, lists, theme and translations are small JSON documents kept in ``app_settings``.
Extra-field VALUES live in the ``custom_values`` table. All writes are admin-only
(``settings.manage``) except saving values, which follows the permission of the form itself.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from sqlalchemy import select

from .. import customization as cz
from ..db import read_session, write_session
from ..events import Actor, log_event
from ..models import AppSetting, CustomValue
from ..security import require

KEY_FORM = {"client": "form_client", "invoice": "form_invoice"}
KEY_LISTS, KEY_THEME, KEY_LANGS = "lists", "theme", "i18n_languages"


def _key_i18n(code: str) -> str:
    return f"i18n_{code}"


# ------------------------------------------------------------------------------ JSON documents
def get_json(key: str, default: Any = None) -> Any:
    with read_session() as s:
        row = s.get(AppSetting, key)
        raw = row.value if row else ""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _put_json(actor: Actor, key: str, value: Any, summary: str) -> None:
    require(actor, "settings.manage")
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with write_session() as s:
        row = s.get(AppSetting, key)
        if row is None:
            s.add(AppSetting(key=key, value=text))
        else:
            row.value = text
        log_event(s, "SETTINGS_UPDATED", summary, entity_type="Settings", actor=actor)


# --------------------------------------------------------------------------------- form layouts
def get_fields(entity: str) -> list[dict]:
    return cz.resolve_fields(entity, get_json(KEY_FORM[entity], None))


def save_fields(actor: Actor, entity: str, fields: list[dict]) -> list[str]:
    """Persist a layout. Returns problems (empty list means saved)."""
    require(actor, "settings.manage")
    clean = cz.resolve_fields(entity, fields)
    problems = cz.validate_fields(entity, clean)
    if problems:
        return problems
    _put_json(actor, KEY_FORM[entity], clean, f"{entity.title()} form layout changed")
    return []


# -------------------------------------------------------------------------------- dropdown lists
def get_lists() -> dict[str, list[str]]:
    return cz.get_lists(get_json(KEY_LISTS, None))


def save_lists(actor: Actor, lists: dict[str, Iterable[str]]) -> None:
    require(actor, "settings.manage")
    clean = {name: cz.sanitize_list(lists.get(name), default) for name, default in cz.DEFAULT_LISTS.items()}
    _put_json(actor, KEY_LISTS, clean, "Dropdown lists changed")


# ------------------------------------------------------------------------------------------ theme
def get_theme() -> dict[str, Any]:
    return cz.sanitize_theme(get_json(KEY_THEME, None))


def save_theme(actor: Actor, theme: dict) -> None:
    require(actor, "settings.manage")
    _put_json(actor, KEY_THEME, cz.sanitize_theme(theme), "Theme changed")


# --------------------------------------------------------------------------------- translations
def get_custom_languages() -> dict[str, dict]:
    data = get_json(KEY_LANGS, {})
    return data if isinstance(data, dict) else {}


def save_custom_languages(actor: Actor, languages: dict[str, dict]) -> None:
    _put_json(actor, KEY_LANGS, languages, "Languages changed")


def get_translation_overrides(code: str) -> dict[str, str]:
    data = get_json(_key_i18n(code), {})
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_translation_overrides(actor: Actor, code: str, overrides: dict[str, str]) -> None:
    clean = {k: v.strip() for k, v in overrides.items() if k and isinstance(v, str) and v.strip()}
    _put_json(actor, _key_i18n(code), clean, f"Translations edited ({code})")


# ------------------------------------------------------------------------- extra-field values
def get_values(entity: str, entity_id: int) -> dict[str, str]:
    with read_session() as s:
        rows = s.execute(select(CustomValue.field_key, CustomValue.value)
                         .where(CustomValue.entity == entity, CustomValue.entity_id == entity_id)).all()
    return {k: v for k, v in rows}


def get_values_many(entity: str, ids: Iterable[int]) -> dict[int, dict[str, str]]:
    wanted = list({int(i) for i in ids})
    out: dict[int, dict[str, str]] = {i: {} for i in wanted}
    if not wanted:
        return out
    with read_session() as s:
        rows = s.execute(select(CustomValue.entity_id, CustomValue.field_key, CustomValue.value)
                         .where(CustomValue.entity == entity, CustomValue.entity_id.in_(wanted))).all()
    for entity_id, key, value in rows:
        out[int(entity_id)][key] = value
    return out


def save_values(actor: Actor, entity: str, entity_id: int, values: dict[str, str], permission: Optional[str] = None) -> None:
    """Upsert the given field values for one client/invoice. An empty text removes the stored row."""
    require(actor, permission or ("client.edit" if entity == "client" else "invoice.create"))
    if not values:
        return
    with write_session() as s:
        existing = {r.field_key: r for r in s.scalars(
            select(CustomValue).where(CustomValue.entity == entity, CustomValue.entity_id == entity_id))}
        for key, text in values.items():
            row, text = existing.get(key), (text or "")
            if not text:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(CustomValue(entity=entity, entity_id=entity_id, field_key=key, value=text[:4000]))
            else:
                row.value = text[:4000]
