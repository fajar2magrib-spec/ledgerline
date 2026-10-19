"""Error boundaries: a failing database or a bug must never show a stack trace to a client.

``guarded`` wraps a section of the page. On failure it logs the full exception with a short
reference id, shows a friendly card with that id and a Retry button, and lets the rest of
the page (sidebar, navigation, sign out) keep working.

Streamlit's own control-flow exceptions (st.rerun, st.stop) derive from BaseException, so
``except Exception`` below never swallows them.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from html import escape
from typing import Iterator

import streamlit as st
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from ..i18n import t
from ..security import PermissionDenied

log = logging.getLogger(__name__)


def _retry_button(key: str) -> None:
    if st.button(t("Try again"), key=f"retry_{key}"):
        st.cache_data.clear()  # drop possibly stale/failed results, then rerun
        st.rerun()


@contextmanager
def guarded(section: str, key: str = "section") -> Iterator[None]:
    try:
        yield
    except PermissionDenied as exc:
        st.warning(str(exc))
    except OperationalError:
        ref = uuid.uuid4().hex[:8]
        log.exception("Database unavailable in %s [%s]", section, ref)
        st.markdown(f'<div class="error-card"><b>{t("The database is temporarily unavailable.")}</b>'
                    f'{t("Your data is safe. Please try again in a moment.")}<br><small>{t("Reference")} {ref}</small></div>',
                    unsafe_allow_html=True)
        _retry_button(key)
    except SQLAlchemyError:
        ref = uuid.uuid4().hex[:8]
        log.exception("Database error in %s [%s]", section, ref)
        st.markdown(f'<div class="error-card"><b>{t("We could not load {section}.", section=escape(t(section)))}</b>'
                    f'{t("The problem has been logged.")}<br><small>{t("Reference")} {ref}</small></div>', unsafe_allow_html=True)
        _retry_button(key)
    except Exception as exc:  # noqa: BLE001  (last line of defence for unexpected bugs)
        ref = uuid.uuid4().hex[:8]
        log.exception("Unexpected error in %s [%s]", section, ref)
        # Short technical hint (error type and message only, never a stack trace) so a screenshot is enough to diagnose.
        hint = escape(f"{type(exc).__name__}: {exc}"[:300])
        st.markdown(f'<div class="error-card"><b>{t("Something went wrong in {section}.", section=escape(t(section)))}</b>'
                    f'{t("It has been logged and nothing was lost.")}<br><small>{t("Reference")} {ref}</small>'
                    f'<br><small>{t("Details:")} {hint}</small></div>',
                    unsafe_allow_html=True)
        _retry_button(key)
