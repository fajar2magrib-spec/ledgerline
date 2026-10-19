"""Help & Guide: a plain-language walkthrough for first-time users."""

from __future__ import annotations

from ..i18n import t
import streamlit as st

from .components import (PAGE_CLIENTS, PAGE_DASHBOARD, PAGE_INVOICE, PAGE_REPORTS, can, card_header, go_to, html_table,
                         page_header)

STEPS = (
    ("1", "Add a client", "Save the name and contact of the person you work with. You can also skip this: "
          "typing a new client while creating an invoice saves them for you.", PAGE_CLIENTS, "Open Clients", "client.edit"),
    ("2", "Create an invoice", "Pick the client, add what you sold, and choose Unpaid or Paid in full. "
          "Totals and tax are calculated for you.", PAGE_INVOICE, "New invoice", "invoice.create"),
    ("3", "Get paid", "When money arrives, open the invoice on the dashboard and press “Mark as fully paid”, "
          "or record a part payment.", PAGE_DASHBOARD, "Open dashboard", None),
    ("4", "Check your business", "See who owes you, your best clients and your profit. Download any report as a file.",
          PAGE_REPORTS, "Open reports", None),
)

FAQ = (
    ("How do I mark an invoice as paid?",
     "While creating the invoice, choose “Paid in full” under Payment. For an existing invoice, go to the dashboard, "
     "scroll to Invoice actions, pick the invoice and press “Mark as fully paid”."),
    ("The client paid only part of the money. What do I do?",
     "Choose “Partly paid” when creating the invoice, or use “Record a payment” in Invoice actions and enter the amount. "
     "The invoice becomes Partially Paid and keeps showing the remaining balance."),
    ("How do I send an invoice to a client?",
     "In Invoice actions, open the invoice. Use the PDF & WhatsApp tab to download the PDF or open a WhatsApp message, "
     "or the Email tab to email it."),
    ("What does “Overdue” mean?",
     "The due date has passed and the invoice is not fully paid. The Reports page shows how late each one is."),
    ("What is the difference between revenue and profit?",
     "Revenue is what you sold, after discounts and before tax. Profit is revenue minus the internal cost you enter "
     "as Expenses on the invoice. Clients never see your expenses."),
    ("I made a mistake on an invoice.",
     "Managers can edit or cancel invoices created in the last 7 days. Admins can edit any invoice. "
     "Open Invoice actions, then the Manage tab."),
    ("How do I add my logo and brand colour?",
     "Go to Administration, then Settings. Upload your logo (PNG or JPG) and pick a colour. Both appear on every PDF invoice."),
    ("How do I save time when creating invoices?",
     "Add what you sell on the Products page once. In New Invoice, search the product list and press Add line."),
    ("How do I record business costs like ads or software?",
     "Open Expenses and add each cost. Reports then shows your real profit after all expenses (Profit & loss tab)."),
    ("Is my data safe?",
     "Yes. Passwords are protected, every change is logged, and admins can download a backup from the sidebar."),
)

ROLES = (
    ("Viewer", "See the dashboard, clients, payments and reports. Download PDFs."),
    ("Manager", "Everything a viewer can do, plus create invoices, record payments, edit clients, send reminders and export data."),
    ("Admin", "Everything, plus users, settings, imports, the audit log, and deleting records."),
)


def render() -> None:
    page_header(t("Help & Guide"), t("New here? Follow these four steps. Everything is one click away."))
    cols = st.columns(4, gap="medium")
    for col, (num, title, text, page, label, perm) in zip(cols, STEPS):
        with col, st.container(border=True):
            st.markdown(f'<div class="step-num">{num}</div><p class="card-title">{t(title)}</p><p class="card-sub">{t(text)}</p>',
                        unsafe_allow_html=True)
            if perm is None or can(perm):
                if st.button(t(label), key=f"help_step_{num}", width="stretch"):
                    go_to(page)

    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True):
        card_header(t("Common questions"), t("Tap a question to read the answer"))
        for question, answer in FAQ:
            with st.expander(t(question)):
                st.write(t(answer))
    with right, st.container(border=True):
        card_header(t("Who can do what"), t("Your role decides which buttons you see"))
        html_table([t("Role"), t("What they can do")], [[f'<span class="strong">{t(role)}</span>', f'<span style="white-space:normal">{t(desc)}</span>']
                                                  for role, desc in ROLES])
        st.markdown(f'<p class="card-sub" style="margin-top:.8rem">{t("Tip: the arrow at the top left opens and closes the menu.")}</p>',
                    unsafe_allow_html=True)
