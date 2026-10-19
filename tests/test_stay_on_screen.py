"""Saving must never move the user to another screen: they stay exactly where they were working."""

from decimal import Decimal

import pytest
import streamlit as st

from test_ui import btn, exc, signed_in, ui  # noqa: F401  (`ui` is a fixture, `env` comes from conftest)

NEW_INVOICE = "➕ New Invoice"


@pytest.fixture(autouse=True)
def _no_cache_leak():
    """These tests seed data behind the UI's back; never let that cached data leak into the next test."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _prepare():
    from ledgerline.services import catalog, demo
    at = signed_in()
    user = at.session_state["user"]
    demo.seed_demo_data(user, 3)
    catalog.create_product(user, catalog.ProductInput("Logo design", Decimal("150"), Decimal("0"), "", "Service", ""))
    st.cache_data.clear()
    at = signed_in()
    return at, user


def _fill_and_save_new_invoice(at):
    at.radio(key="nav_page").set_value(NEW_INVOICE).run()
    form_id = f"new_{at.session_state['form_nonce']}"
    at.selectbox(key=f"catpick_{form_id}").select_index(0).run()
    btn(at, "➕ Add line").click().run()
    btn(at, "💾 Submit & Save Invoice").click().run()
    return at


def test_saving_a_new_invoice_stays_on_the_invoice_screen(env, ui):
    at, _ = _prepare()
    before = len(_invoice_ids())
    at = _fill_and_save_new_invoice(at)
    assert exc(at) == [] and [e.value for e in at.error] == []
    assert at.radio(key="nav_page").value == NEW_INVOICE            # did NOT jump to the dashboard
    assert "edit_invoice_id" not in at.session_state or not at.session_state["edit_invoice_id"]  # a new invoice, not an edit
    assert any("Add new invoice" in m.value for m in at.markdown)   # a fresh, empty form is ready for the next invoice
    assert len(_invoice_ids()) == before + 1                        # and the invoice really was saved


def test_saving_an_edit_stays_on_the_screen_and_can_be_saved_again(env, ui):
    at, user = _prepare()
    at = _fill_and_save_new_invoice(at)
    newest = max(i for i in _invoice_ids())
    at.session_state["edit_invoice_id"] = newest
    at.run()
    assert any("Edit invoice" in m.value for m in at.markdown)
    for _ in range(2):                                              # twice: proves the new version is picked up (no "someone else changed it")
        btn(at, "💾 Save changes").click().run()
        assert exc(at) == [] and [e.value for e in at.error] == [], [e.value for e in at.error]
        assert at.radio(key="nav_page").value == NEW_INVOICE        # still on the invoice screen
        assert at.session_state["edit_invoice_id"] == newest        # still editing the same invoice
        assert any("Edit invoice" in m.value for m in at.markdown)


def test_cancel_editing_stays_on_the_screen(env, ui):
    at, _ = _prepare()
    at = _fill_and_save_new_invoice(at)
    at.session_state["edit_invoice_id"] = max(_invoice_ids())
    at.run()
    btn(at, "Cancel editing").click().run()
    assert exc(at) == [] and at.radio(key="nav_page").value == NEW_INVOICE
    assert any("Add new invoice" in m.value for m in at.markdown)


def test_other_saves_stay_on_their_own_screen(env, ui):
    at, _ = _prepare()
    # Products
    at.radio(key="nav_page").set_value("🏷️ Products").run()
    n = at.session_state["product_form_nonce"]
    at.text_input(key=f"np{n}_name").set_value("Hosting").run()
    btn(at, "Save product").click().run()
    assert exc(at) == [] and at.radio(key="nav_page").value == "🏷️ Products"
    # Expenses
    at.radio(key="nav_page").set_value("🧮 Expenses").run()
    n = at.session_state["expense_form_nonce"]
    at.text_input(key=f"ex_vendor_{n}").set_value("Canva").run()
    at.number_input(key=f"ex_amt_{n}").set_value(25.0).run()
    btn(at, "Save expense").click().run()
    assert exc(at) == [] and at.radio(key="nav_page").value == "🧮 Expenses"
    # Clients
    at.radio(key="nav_page").set_value("👥 Clients").run()
    assert exc(at) == [] and at.radio(key="nav_page").value == "👥 Clients"


def _invoice_ids():
    from sqlalchemy import select
    from ledgerline.db import read_session
    from ledgerline.models import Invoice
    with read_session() as s:
        return list(s.scalars(select(Invoice.id)))
