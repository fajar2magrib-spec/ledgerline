"""Interface language support.

How it works
* Every on-screen sentence in the code is written in English and passed through ``t("...")``.
  English is the key: if a language has no translation for a sentence, English is shown, so a
  half-finished language never breaks the app.
* Translations ship as ``ledgerline/locales/<code>.json`` (``{"English": "Translation"}``).
  Administrators can fix any sentence or add a whole new language from the Customize page; those
  edits are stored in the database (``formconfig``) and win over the shipped files.
* The chosen language lives in the browser session (``_lang``) and in the URL (``?lang=ur``), so a
  refresh or a bookmark keeps it. Right-to-left languages (Urdu, Arabic ...) flip the layout (CSS in
  customization.theme_css).

``N_("text")`` marks a sentence that is translated later (module-level constants); it returns the text
unchanged, and the sentence-collector script finds it just like ``t(...)``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import streamlit as st

LOCALES = Path(__file__).parent / "locales"
META_KEY = "__meta__"
DEFAULT_LANGUAGE = "en"
ENGLISH = ("English", False)


def N_(text: str) -> str:  # noqa: N802  (gettext-style marker)
    return text


# ------------------------------------------------------------------------------- shipped files
@lru_cache(maxsize=None)
def _file_table(code: str) -> dict[str, str]:
    path = LOCALES / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k != META_KEY and isinstance(v, str)}


@lru_cache(maxsize=None)
def _file_meta(code: str) -> tuple[str, bool]:
    try:
        meta = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8")).get(META_KEY, {})
        return str(meta.get("name", code)), bool(meta.get("rtl", False))
    except (OSError, ValueError, AttributeError):
        return code, False


@lru_cache(maxsize=1)
def shipped_languages() -> dict[str, tuple[str, bool]]:
    langs = {DEFAULT_LANGUAGE: ENGLISH}
    for path in sorted(LOCALES.glob("*.json")):
        code = path.stem
        if code not in langs and not code.startswith("_"):
            langs[code] = _file_meta(code)
    return langs


@lru_cache(maxsize=1)
def english_keys() -> tuple[str, ...]:
    """Every sentence the app can show (the master list, generated from the code)."""
    try:
        data = json.loads((LOCALES / "_keys.json").read_text(encoding="utf-8"))
        return tuple(data)
    except (OSError, ValueError):
        return ()


def file_translation(code: str) -> dict[str, str]:
    return dict(_file_table(code))


# ----------------------------------------------------------------------------- database part
@st.cache_data(ttl=60, show_spinner=False)
def _db_languages() -> dict[str, tuple[str, bool]]:
    try:
        from .services import formconfig
        raw = formconfig.get_custom_languages()
    except Exception:  # noqa: BLE001  (a database problem must never break translation)
        return {}
    return {code: (str(v.get("name", code)), bool(v.get("rtl", False))) for code, v in raw.items() if isinstance(v, dict)}


@st.cache_data(ttl=60, show_spinner=False)
def _db_overrides(code: str) -> dict[str, str]:
    try:
        from .services import formconfig
        return formconfig.get_translation_overrides(code)
    except Exception:  # noqa: BLE001
        return {}


def available_languages() -> dict[str, tuple[str, bool]]:
    """code -> (name written in that language, is right-to-left). English first."""
    return {**shipped_languages(), **_db_languages()}


def is_rtl(code: Optional[str] = None) -> bool:
    code = code or current_lang()
    return bool(available_languages().get(code, ENGLISH)[1])


# --------------------------------------------------------------------------------- current language
def current_lang() -> str:
    try:
        return st.session_state.get("_lang") or DEFAULT_LANGUAGE
    except Exception:  # noqa: BLE001  (no Streamlit session, e.g. in a script or a test)
        return DEFAULT_LANGUAGE


def merged_table(code: str) -> dict[str, str]:
    if code == DEFAULT_LANGUAGE:
        return {}
    return {**_file_table(code), **_db_overrides(code)}


def init(default_language: str = DEFAULT_LANGUAGE) -> str:
    """Call once per run, early: decides the language (session > URL > company default) and loads its table."""
    ss = st.session_state
    langs = available_languages()
    ss["_lang_default"] = default_language if default_language in langs else DEFAULT_LANGUAGE
    if ss.get("_lang") not in langs:
        wanted = None
        try:
            wanted = st.query_params.get("lang")
        except Exception:  # noqa: BLE001
            pass
        ss["_lang"] = wanted if wanted in langs else ss["_lang_default"]
    ss["_i18n_table"] = merged_table(ss["_lang"])
    return ss["_lang"]


def t(text: str, **values) -> str:
    """Translate ``text`` into the current language, then fill ``{placeholders}`` from ``values``."""
    if not text:
        return text
    try:
        out = st.session_state.get("_i18n_table", {}).get(text, text)
    except Exception:  # noqa: BLE001
        out = text
    if values:
        try:
            return out.format(**values)
        except (KeyError, IndexError, ValueError):
            try:
                return text.format(**values)   # a broken translation falls back to English
            except (KeyError, IndexError, ValueError):
                return text
    return out


def _template_regex(key: str):
    """Regex matching a message produced from the template ``key`` (``{x}`` = any text), or None."""
    import re
    if "{" not in key:
        return None
    parts = re.split(r"(\{[A-Za-z_][A-Za-z_0-9]*\})", key)
    pattern = "".join(f"(?P<{p[1:-1]}>.+?)" if p.startswith("{") and p.endswith("}") else re.escape(p) for p in parts)
    try:
        return re.compile(f"^{pattern}$", re.S)
    except re.error:
        return None


def translator(code: str):
    """A ``tr(text, **values)`` function for ONE language, independent of the browser session (used by PDFs)."""
    table = merged_table(code) if code != DEFAULT_LANGUAGE else {}

    def tr(text: str, **values) -> str:
        out = table.get(text, text)
        if not values:
            return out
        try:
            return out.format(**values)
        except (KeyError, IndexError, ValueError):
            try:
                return text.format(**values)
            except (KeyError, IndexError, ValueError):
                return text
    return tr


def tr_message(text: str) -> str:
    """Translate a message that was built by the services in English (``"Invoice number INV-7 already exists."``).

    Exact sentences are looked up directly; sentences with values are matched against the templates
    in the translation table. Anything unknown stays as it is (English), never an error."""
    text = (text or "").strip()
    if not text:
        return text
    try:
        table = st.session_state.get("_i18n_table", {})
    except Exception:  # noqa: BLE001
        return text
    if text in table:
        return table[text]
    if not table:
        return text
    cache = st.session_state.setdefault("_i18n_templates", {})
    lang = st.session_state.get("_lang")
    if cache.get("lang") != lang:
        compiled = []
        for key, value in table.items():
            rx_ = _template_regex(key)
            if rx_ is not None:
                compiled.append((rx_, value))
        cache.clear()
        cache.update(lang=lang, items=compiled)
    for rx_, value in cache["items"]:
        m = rx_.match(text)
        if m:
            try:
                return value.format(**m.groupdict())
            except (KeyError, IndexError, ValueError):
                return text
    return text


# --------------------------------------------------------------------------------------- picker
def _on_pick(where: str) -> None:
    code = st.session_state.get(f"lang_pick_{where}")
    if code in available_languages():
        st.session_state["_lang"] = code
        st.session_state["_i18n_table"] = merged_table(code)
        try:                                    # keep the choice in the URL, also on the login page
            if code != st.session_state.get("_lang_default", DEFAULT_LANGUAGE):
                st.query_params["lang"] = code
            elif "lang" in st.query_params:
                del st.query_params["lang"]
        except Exception:  # noqa: BLE001
            pass


def language_picker(where: str, *, label_visible: bool = True) -> None:
    """The language drop-down (sidebar and login page). Changing it re-draws the whole app."""
    langs = available_languages()
    codes = list(langs)
    cur = current_lang()
    st.selectbox("🌐 " + t("Language"), codes, index=codes.index(cur) if cur in codes else 0,
                 key=f"lang_pick_{where}", on_change=_on_pick, args=(where,),
                 format_func=lambda c: langs[c][0], label_visibility="visible" if label_visible else "collapsed")


def coverage(code: str) -> tuple[int, int]:
    """(translated sentences, total sentences) for a language."""
    keys = english_keys()
    table = merged_table(code)
    return sum(1 for k in keys if table.get(k)), len(keys)
