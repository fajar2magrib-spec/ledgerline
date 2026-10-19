"""Stylesheet loader.

The CSS lives in real .css files (editable with syntax highlighting, diff-friendly):
  assets/ledgerline.base.css    typography, KPI cards, badges, sidebar skin, login
  assets/ledgerline.layout.css  layout stability: sidebar toggle, fixed sizes, no flicker
They are read once per process (``lru_cache``) and injected with ``st.markdown``.
Injection happens on every run: Streamlit replaces the element in place, so there is no
flash of unstyled content and nothing accumulates.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).parent / "assets"
FILES = ("ledgerline.base.css", "ledgerline.layout.css")  # order matters: layout wins


@lru_cache(maxsize=1)
def _stylesheet() -> str:
    return "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in FILES)


def inject() -> None:
    st.markdown(f"<style>{_stylesheet()}</style>", unsafe_allow_html=True)


def _native_theme(theme: dict) -> None:
    """Also tell Streamlit itself (widgets, tables, charts) when the admin forces light or dark.
    Streamlit reads this for NEW browser sessions, so a page refresh shows the full effect; the CSS
    in theme_css already switches the main surfaces immediately. Best effort: never raises."""
    try:
        from streamlit import config as sc
        mode = theme.get("mode", "auto")
        sc.set_option("theme.base", mode if mode in ("light", "dark") else None)
        sc.set_option("theme.primaryColor", theme["accent"])
    except Exception:  # noqa: BLE001
        pass


def inject_theme() -> None:
    """Admin-chosen theme (Customize -> Appearance) and right-to-left layout for Urdu/Arabic ...
    Drawn after the shipped CSS so it wins; a failure here must never stop the app."""
    from .. import customization, i18n
    try:
        from . import charts, components
        theme = components.theme_cfg()
        charts.apply_theme(theme["accent"])
        _native_theme(theme)
        css = customization.theme_css(theme, rtl=i18n.is_rtl())
    except Exception:  # noqa: BLE001
        css = customization.theme_css(None, rtl=i18n.is_rtl())
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def hide_sidebar() -> None:
    """Login/setup screens: hide the sidebar visually while keeping it mounted, so its
    expanded state is already initialised when the user signs in."""
    st.markdown("<style>section[data-testid='stSidebar'], [data-testid='stExpandSidebarButton']"
                " { visibility: hidden !important; }</style>", unsafe_allow_html=True)
