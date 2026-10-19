"""Customize page (administrators): change the app WITHOUT touching code.

* Appearance   - colours, font, roundness, backgrounds, your own CSS
* Client form / Invoice form - show or hide fields, rename, reorder, add your own boxes, delete them
* Dropdown lists - categories, payment methods ...
* Languages    - company default language and country, fix any translation, add a new language
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from .. import countries, i18n
from .. import customization as cz
from ..i18n import t
from ..security import PermissionDenied
from ..services import formconfig
from ..services.settings import save_settings
from .components import actor, button_width, card_header, flash, invalidate, page_header, settings

PAGE_SIZE = 40


def _save_error(exc: Exception) -> None:
    st.error(str(exc) if isinstance(exc, PermissionDenied) else t("Could not save. Please try again."))


# =============================================================================== appearance
def _theme_defaults(theme: dict) -> None:
    ss = st.session_state
    if ss.get("_th_loaded"):
        return
    ss.update(th_accent=theme["accent"], th_accent2=theme["accent2"], th_font=theme["font"], th_radius=theme["radius"],
              th_background=theme["background"], th_sidebar=theme["sidebar"], th_density=theme["density"],
              th_css=theme["custom_css"], th_mode=theme["mode"], _th_loaded=True)


def _apply_preset(name: str) -> None:
    st.session_state["th_accent"] = cz.PRESETS[name]["accent"]
    st.session_state["th_accent2"] = cz.PRESETS[name]["accent2"]


def _appearance_tab() -> None:
    theme = formconfig.get_theme()
    _theme_defaults(theme)
    ss = st.session_state
    st.caption(t("Pick colours and styles, check the preview, then press Save. Everyone sees the change straight away."))
    st.markdown("**" + t("Quick colour sets") + "**")
    cols = st.columns(len(cz.PRESETS))
    for col, name in zip(cols, cz.PRESETS):
        col.button(t(name), key=f"th_preset_{name}", on_click=_apply_preset, args=(name,), width="stretch")
    c1, c2, c3 = st.columns(3, gap="medium")
    c1.color_picker(t("Main colour"), key="th_accent", help=t("Buttons, menu, tabs, charts and headings."))
    c2.color_picker(t("Second colour"), key="th_accent2", help=t("Used for the heading gradient."))
    c3.selectbox(t("Font"), list(cz.FONTS), key="th_font", format_func=t)
    st.selectbox(t("Light or dark"), cz.MODES, key="th_mode",
                 format_func=lambda v: t({"auto": "Follow the device", "light": "Always light", "dark": "Always dark"}[v]),
                 help=t("Light or dark for everyone. After saving, refresh the page once to see it everywhere."))
    d1, d2, d3, d4 = st.columns(4, gap="medium")
    d1.slider(t("Roundness of cards and buttons"), 0, 28, key="th_radius")
    d2.selectbox(t("Page background"), cz.BACKGROUNDS, key="th_background",
                 format_func=lambda v: t({"soft": "Soft glow", "tinted": "Tinted", "plain": "Plain"}[v]))
    d3.selectbox(t("Side menu"), cz.SIDEBARS, key="th_sidebar",
                 format_func=lambda v: t({"default": "Normal", "tinted": "Tinted"}[v]))
    d4.selectbox(t("Spacing"), cz.DENSITIES, key="th_density",
                 format_func=lambda v: t({"comfortable": "Comfortable", "compact": "Compact"}[v]))
    with st.expander(t("Advanced: add your own CSS")):
        st.text_area(t("Custom CSS"), key="th_css", height=140, help=t("For people who know CSS. Leave empty if unsure."))
    draft = cz.sanitize_theme({"accent": ss["th_accent"], "accent2": ss["th_accent2"], "font": ss["th_font"],
                               "radius": ss["th_radius"], "background": ss["th_background"], "sidebar": ss["th_sidebar"],
                               "density": ss["th_density"], "mode": ss["th_mode"], "custom_css": ss["th_css"]})
    on = cz.text_on(draft["accent"])
    st.markdown("**" + t("Preview") + "**")
    st.markdown(
        f'<div style="padding:1rem 1.2rem;border:1px solid rgba(128,128,128,.3);border-radius:{draft["radius"]}px">'
        f'<div style="font-size:1.4rem;font-weight:800;background:linear-gradient(90deg,{draft["accent"]},{draft["accent2"]} 70%);'
        f'-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent">Ledgerline</div>'
        f'<span style="display:inline-block;margin-top:.6rem;padding:.5rem 1rem;border-radius:{min(draft["radius"], 16)}px;'
        f'background:{draft["accent"]};color:{on};font-weight:700">{t("Save")}</span> '
        f'<span style="display:inline-block;margin-left:.5rem;padding:.2rem .7rem;border-radius:999px;'
        f'background:{draft["accent"]}22;color:{draft["accent"]};font-weight:700">{t("Paid")}</span></div>',
        unsafe_allow_html=True)
    b1, b2 = st.columns([1, 1], gap="medium")
    if b1.button(t("💾 Save appearance"), type="primary", key="th_save", width="stretch"):
        try:
            formconfig.save_theme(actor(), draft)
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            invalidate()
            flash(t("Appearance saved"))
            st.rerun()
    if b2.button(t("↩️ Back to the original look"), key="th_reset", width="stretch"):
        try:
            formconfig.save_theme(actor(), dict(cz.DEFAULT_THEME))
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            st.session_state.pop("_th_loaded", None)
            invalidate()
            flash(t("Appearance reset"))
            st.rerun()


# =============================================================================== form designer
def _wk(entity: str) -> str:
    return f"cz_work_{entity}"


def _k(entity: str, key: str, part: str) -> str:
    return f"cz_{entity}_{key}_{part}"


def _collect(entity: str) -> list[dict]:
    """Working list updated with whatever the person typed/ticked in the row widgets."""
    out = []
    for f in st.session_state[_wk(entity)]:
        f = dict(f)
        key, ss = f["key"], st.session_state
        text = (ss.get(_k(entity, key, "label"), "") or "").strip()
        if f["builtin"]:
            f["custom_label"] = "" if (not text or text == t(f["label"])) else text
        elif text:
            f["label"] = text
        if not f["locked"]:
            f["visible"] = bool(ss.get(_k(entity, key, "vis"), f["visible"]))
            f["required"] = bool(ss.get(_k(entity, key, "req"), f["required"]))
        if entity == "invoice":
            f["pdf"] = bool(ss.get(_k(entity, key, "pdf"), f.get("pdf", False)))
        if f["type"] == "dropdown" and not f["builtin"]:
            f["options"] = cz.sanitize_list((ss.get(_k(entity, key, "opts"), "") or "").splitlines(), f["options"])
        out.append(f)
    return out


def _move(entity: str, index: int, step: int) -> None:
    fields = _collect(entity)
    j = index + step
    if 0 <= j < len(fields) and not fields[j]["locked"] and not fields[index]["locked"]:
        fields[index], fields[j] = fields[j], fields[index]
    st.session_state[_wk(entity)] = fields


def _clear(entity: str) -> None:
    """Forget the row widgets' typed text so the form reloads cleanly from what was saved."""
    for name in [k for k in st.session_state if str(k).startswith(f"cz_{entity}_")]:
        del st.session_state[name]
    st.session_state.pop(_wk(entity), None)


def _delete(entity: str, key: str) -> None:
    st.session_state[_wk(entity)] = [f for f in _collect(entity) if f["key"] != key]


def _add(entity: str) -> None:
    ss = st.session_state
    label = (ss.get(f"cz_{entity}_new_label") or "").strip()
    if not label:
        ss["_cz_msg"] = t("Type a name for the new field first.")
        return
    ftype = ss.get(f"cz_{entity}_new_type", "text")
    options = (ss.get(f"cz_{entity}_new_opts") or "").splitlines()
    fields = _collect(entity)
    fields.append(cz.new_custom_field(label, ftype, required=bool(ss.get(f"cz_{entity}_new_req")), options=options))
    ss[_wk(entity)] = fields
    ss[f"cz_{entity}_new_label"], ss[f"cz_{entity}_new_opts"], ss[f"cz_{entity}_new_req"] = "", "", False


def _designer(entity: str) -> None:
    ss = st.session_state
    if _wk(entity) not in ss:
        ss[_wk(entity)] = formconfig.get_fields(entity)
    fields = ss[_wk(entity)]
    st.caption(t("Tick Show to display a field, rename it, move it up or down, or delete the ones you added. "
                 "Press Save at the bottom when you are done."))
    head = st.columns([3.2, 1.6, .8, .9] + ([.9] if entity == "invoice" else []) + [1.3])
    for col, text in zip(head, [t("Field name"), t("Kind"), t("Show"), t("Required")] + ([t("Print on PDF")] if entity == "invoice" else []) + [""]):
        col.markdown(f"**{text}**" if text else "")
    for i, f in enumerate(fields):
        key = f["key"]
        cols = st.columns([3.2, 1.6, .8, .9] + ([.9] if entity == "invoice" else []) + [1.3], vertical_alignment="center")
        shown = f["custom_label"] or t(f["label"]) if f["builtin"] else f["label"]
        cols[0].text_input(t("Field name"), value=shown, key=_k(entity, key, "label"), label_visibility="collapsed", max_chars=cz.MAX_LABEL)
        cols[1].caption(t(cz.TYPE_NAMES[f["type"]]) + ("" if f["builtin"] else " ✦"))
        cols[2].checkbox(t("Show"), value=f["visible"], key=_k(entity, key, "vis"), disabled=f["locked"], label_visibility="collapsed")
        cols[3].checkbox(t("Required"), value=f["required"], key=_k(entity, key, "req"), disabled=f["locked"], label_visibility="collapsed")
        pos = 4
        if entity == "invoice":
            cols[4].checkbox(t("Print on PDF"), value=bool(f.get("pdf")), key=_k(entity, key, "pdf"),
                             disabled=f["key"] in ("number", "due_date", "category", "payment_method", "expenses"),
                             label_visibility="collapsed", help=t("Prints this field on the PDF invoice (not for internal costs)."))
            pos = 5
        with cols[pos]:
            a, b, c = st.columns(3, gap="small")
            a.button("⬆", key=_k(entity, key, "up"), on_click=_move, args=(entity, i, -1), disabled=f["locked"] or i == 0, help=t("Move up"))
            b.button("⬇", key=_k(entity, key, "down"), on_click=_move, args=(entity, i, 1), disabled=f["locked"] or i == len(fields) - 1, help=t("Move down"))
            if not f["builtin"]:
                c.button("🗑", key=_k(entity, key, "del"), on_click=_delete, args=(entity, key), help=t("Delete this field"))
        if f["type"] == "dropdown" and not f["builtin"]:
            st.text_area(t("Choices for {name} (one per line)", name=shown), value="\n".join(f["options"]),
                         key=_k(entity, key, "opts"), height=100)

    with st.container(border=True):
        card_header(t("➕ Add a new field"), t("A new box on the form, for anything you need to record."))
        n1, n2 = st.columns([3, 2], gap="medium")
        n1.text_input(t("Name of the new field"), key=f"cz_{entity}_new_label", max_chars=cz.MAX_LABEL, placeholder=t("e.g. Birthday, Project code, Company size"))
        n2.selectbox(t("Kind"), list(cz.FIELD_TYPES), key=f"cz_{entity}_new_type", format_func=lambda v: t(cz.TYPE_NAMES[v]))
        if ss.get(f"cz_{entity}_new_type") == "dropdown":
            st.text_area(t("Choices (one per line)"), key=f"cz_{entity}_new_opts", height=90)
        st.checkbox(t("Required"), key=f"cz_{entity}_new_req")
        st.button(t("➕ Add field"), key=f"cz_{entity}_add", on_click=_add, args=(entity,))
        if ss.get("_cz_msg"):
            st.warning(ss.pop("_cz_msg"))

    s1, s2 = st.columns(2, gap="medium")
    if s1.button(t("💾 Save form"), type="primary", key=f"cz_{entity}_save", width="stretch"):
        try:
            problems = formconfig.save_fields(actor(), entity, _collect(entity))
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            if problems:
                for text in problems:
                    st.error(t(text))
            else:
                _clear(entity)
                invalidate()
                flash(t("Form saved"))
                st.rerun()
    with s2.popover(t("↩️ Reset this form"), **button_width()):
        st.warning(t("This removes your changes and extra fields from the form. Information already saved stays safe."))
        if st.button(t("Yes, reset"), key=f"cz_{entity}_reset"):
            try:
                formconfig.save_fields(actor(), entity, [])
            except Exception as exc:  # noqa: BLE001
                _save_error(exc)
            else:
                _clear(entity)
                invalidate()
                flash(t("Form reset"))
                st.rerun()


def _client_tab() -> None:
    cfg = settings()
    _designer("client")
    st.divider()
    st.markdown("**" + t("Phone numbers") + "**")
    iso = st.selectbox(t("Country pre-selected in phone pickers"), countries.ISO_CODES, key="cz_default_country",
                       index=countries.ISO_CODES.index(cfg.default_country if cfg.default_country in countries.BY_ISO else "PK"),
                       format_func=countries.label)
    if iso != cfg.default_country and st.button(t("Save country"), key="cz_country_save"):
        try:
            problems = save_settings(actor(), replace(cfg, default_country=iso))
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            if problems:
                for text in problems:
                    st.error(t(text))
            else:
                invalidate()
                flash(t("Settings saved"))
                st.rerun()


# =============================================================================== dropdown lists
def _lists_tab() -> None:
    current = formconfig.get_lists()
    st.caption(t("One choice per line. These lists appear in the invoice, product and expense forms."))
    cols = st.columns(2, gap="medium")
    texts = {}
    for i, (name, title) in enumerate(cz.LIST_TITLES.items()):
        texts[name] = cols[i % 2].text_area(t(title), value="\n".join(current[name]), key=f"cz_list_{name}", height=190)
    if st.button(t("💾 Save lists"), type="primary", key="cz_lists_save"):
        try:
            formconfig.save_lists(actor(), {k: v.splitlines() for k, v in texts.items()})
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            invalidate()
            flash(t("Lists saved"))
            st.rerun()


# =============================================================================== languages
def _language_defaults() -> None:
    cfg = settings()
    langs = i18n.available_languages()
    codes = list(langs)
    pick = st.selectbox(t("Language new visitors see first"), codes, key="cz_default_lang",
                        index=codes.index(cfg.default_language) if cfg.default_language in codes else 0,
                        format_func=lambda c: langs[c][0])
    if pick != cfg.default_language and st.button(t("Save default language"), key="cz_lang_save"):
        try:
            problems = save_settings(actor(), replace(cfg, default_language=pick))
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            if problems:
                for text in problems:
                    st.error(t(text))
            else:
                invalidate()
                flash(t("Settings saved"))
                st.rerun()


def _add_language() -> None:
    with st.expander(t("➕ Add another language")):
        c1, c2, c3 = st.columns([1, 2, 1], gap="medium")
        code = c1.text_input(t("Code (2 or 3 letters)"), key="cz_newlang_code", max_chars=3, placeholder="pa").strip().lower()
        name = c2.text_input(t("Name in that language"), key="cz_newlang_name", max_chars=30, placeholder="ਪੰਜਾਬੀ").strip()
        rtl = c3.checkbox(t("Right-to-left"), key="cz_newlang_rtl")
        if st.button(t("Add language"), key="cz_newlang_btn"):
            existing = i18n.available_languages()
            if not (code.isalpha() and 2 <= len(code) <= 3) or not name:
                st.error(t("Enter a 2 or 3 letter code and the language name."))
            elif code in existing:
                st.error(t("That language already exists."))
            else:
                try:
                    custom = formconfig.get_custom_languages()
                    custom[code] = {"name": name, "rtl": rtl}
                    formconfig.save_custom_languages(actor(), custom)
                except Exception as exc:  # noqa: BLE001
                    _save_error(exc)
                else:
                    invalidate()
                    flash(t("Language added. Now fill in its translations below."))
                    st.rerun()


def _translator() -> None:
    langs = {c: v for c, v in i18n.available_languages().items() if c != "en"}
    st.markdown("**" + t("Fix or add translations") + "**")
    code = st.selectbox(t("Language to edit"), list(langs), key="cz_tr_lang", format_func=lambda c: f"{langs[c][0]} ({c})")
    done, total = i18n.coverage(code)
    st.progress(done / total if total else 0.0, text=t("{done} of {total} sentences translated", done=done, total=total))
    f1, f2 = st.columns([3, 1], gap="medium")
    needle = f1.text_input(t("Search"), key="cz_tr_search", placeholder=t("Search the English text or the translation")).strip().lower()
    only_missing = f2.checkbox(t("Only missing"), key="cz_tr_missing")
    shipped = i18n.file_translation(code)
    table = i18n.merged_table(code)
    keys = [k for k in i18n.english_keys()
            if (not only_missing or not table.get(k)) and (not needle or needle in k.lower() or needle in table.get(k, "").lower())]
    pages = max(1, -(-len(keys) // PAGE_SIZE))
    page = st.number_input(t("Page"), 1, pages, 1, key=f"cz_tr_page_{code}_{only_missing}_{needle}") if pages > 1 else 1
    chunk = keys[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    st.caption(t("{n} sentences", n=len(keys)))
    import pandas as pd
    frame = pd.DataFrame({"English": chunk, "Translation": [table.get(k, "") for k in chunk]})
    edited = st.data_editor(frame, hide_index=True, width="stretch", disabled=["English"], num_rows="fixed",
                            key=f"cz_tr_editor_{code}_{page}_{only_missing}_{needle}",
                            column_config={"English": st.column_config.TextColumn(t("English"), width="large"),
                                           "Translation": st.column_config.TextColumn(t("Translation"), width="large")})
    st.caption(t("Keep words like {name} or {number} exactly as they are: they are replaced by real values."))
    if st.button(t("💾 Save translations"), type="primary", key="cz_tr_save"):
        overrides = formconfig.get_translation_overrides(code)
        for english, text in zip(edited["English"], edited["Translation"]):
            text = (text or "").strip()
            if text and text != shipped.get(english):
                overrides[english] = text
            else:
                overrides.pop(english, None)
        try:
            formconfig.save_translation_overrides(actor(), code, overrides)
        except Exception as exc:  # noqa: BLE001
            _save_error(exc)
        else:
            invalidate()
            flash(t("Translations saved"))
            st.rerun()
    custom = formconfig.get_custom_languages()
    if code in custom:
        with st.popover(t("🗑 Delete this language")):
            st.warning(t("The language and your translations for it are removed."))
            if st.button(t("Yes, delete"), key="cz_lang_delete"):
                custom.pop(code, None)
                try:
                    formconfig.save_custom_languages(actor(), custom)
                    formconfig.save_translation_overrides(actor(), code, {})
                except Exception as exc:  # noqa: BLE001
                    _save_error(exc)
                else:
                    invalidate()
                    flash(t("Language deleted"))
                    st.rerun()


def _languages_tab() -> None:
    _language_defaults()
    _add_language()
    st.divider()
    _translator()


# ======================================================================================== page
def render() -> None:
    page_header(t("Customize"), t("Change how Ledgerline looks and what its forms ask for. No programming needed."))
    tabs = st.tabs([t("🎨 Appearance"), t("👥 Client form"), t("🧾 Invoice form"), t("📋 Dropdown lists"), t("🌐 Languages")])
    with tabs[0]:
        _appearance_tab()
    with tabs[1]:
        _client_tab()
    with tabs[2]:
        _designer("invoice")
    with tabs[3]:
        _lists_tab()
    with tabs[4]:
        _languages_tab()
