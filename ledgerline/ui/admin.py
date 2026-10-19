"""Administration (admin role only): users, audit trail, business settings, data tools."""

from __future__ import annotations

from dataclasses import replace
from html import escape
from decimal import Decimal
from pathlib import Path

import streamlit as st

from ..i18n import t
from ..config import BASE_DIR, get_config
from ..db import database_info
from ..formatting import fdate, CURRENCIES
from ..integrations.email import get_email_service
from ..integrations.messaging import get_provider
from ..pdf import REPORTLAB_AVAILABLE
from ..security import MIN_PASSWORD_LENGTH, ROLES, PermissionDenied
from ..services import audit, backup, demo, legacy, users
from ..services.settings import save_logo, save_settings
from . import auth_ui
from .components import fail, tr_error
from .components import actor, button_width, flash, html_table, invalidate, logo_bytes, settings, who_cell


def _users_tab() -> None:
    me = actor()
    rows = users.list_users()
    html_table([t("User"), t("Name"), t("Role"), t("Status"), t("Last login (UTC)")],
               [[who_cell(u.username), escape(u.full_name or "-"),
                 f'<span class="pill pill--info">{escape(t(str(u.role)))}</span>',
                 f'<span class="pill pill--{"up" if u.is_active else "neutral"}">{t("Active") if u.is_active else t("Disabled")}</span>',
                 f'<span class="muted">{fdate(u.last_login_at, "%b %d, %Y %H:%M")}</span>' if u.last_login_at else f'<span class="muted">{t("Never")}</span>']
                for u in rows], empty=t("No users yet."))
    left, right = st.columns(2, gap="large")
    with left, st.form("create_user", border=True):
        st.markdown("**" + t("Add a user") + "**")
        username = st.text_input(t("Username"))
        full_name = st.text_input(t("Full name"))
        role = st.selectbox(t("Role"), ROLES, index=ROLES.index("manager"),
                            help=t("viewer: read only. manager: create/edit recent invoices, payments, messages. admin: everything."))
        password = st.text_input(t("Temporary password (min {MIN_PASSWORD_LENGTH})", MIN_PASSWORD_LENGTH=MIN_PASSWORD_LENGTH), type="password")
        if st.form_submit_button(t("Create user"), type="primary"):
            try:
                users.create_user(me, username, password, role, full_name)
            except (users.UserError, PermissionDenied) as exc:
                fail(exc)
            else:
                flash(t("User {v1} created", v1=username.strip().lower()))
                st.rerun()
    with right, st.form("edit_user", border=True):
        st.markdown("**" + t("Change a user") + "**")
        others = [u for u in rows if u.id != me.user_id]  # nobody edits their own role (avoids self lock-out)
        if not others:
            st.caption(t("Add another user first."))
            st.form_submit_button(t("Apply"), disabled=True)
            return
        target = st.selectbox(t("User"), others, format_func=lambda u: f"{u.username} ({u.role})")
        new_role = st.selectbox(t("Role"), ROLES, index=ROLES.index(target.role), key="edit_role")
        active = st.checkbox(t("Account is active"), value=target.is_active)
        new_pw = st.text_input(t("Reset password (leave empty to keep)"), type="password")
        if st.form_submit_button(t("Apply changes")):
            try:
                users.update_user(me, target.id, role=new_role, is_active=active)
                if new_pw:
                    users.reset_password(me, target.id, new_pw)
            except (users.UserError, PermissionDenied) as exc:
                fail(exc)
            else:
                flash(t("Updated {username}. Their sessions were signed out.", username=target.username))
                st.rerun()


def _audit_tab() -> None:
    values = audit.audit_filter_values(actor())
    c1, c2, c3, c4 = st.columns(4, gap="medium")
    user = c1.selectbox(t("User"), [""] + values["users"], format_func=lambda v: v or t("All users"))
    entity = c2.selectbox(t("Entity"), [""] + values["entities"], format_func=lambda v: v or t("All entities"))
    action = c3.selectbox(t("Action"), [""] + values["actions"], format_func=lambda v: v or t("All actions"))
    text = c4.text_input(t("Contains"), placeholder=t("e.g. INV-1001"))
    frame = audit.query_audit(actor(), username=user, entity_type=entity, action=action, text=text, limit=500)
    if not frame.empty:
        frame["Changes"] = frame["Changes"].map(lambda v: "" if v is None else str(v))
    st.dataframe(frame, hide_index=True, width="stretch", height=480)
    st.caption(t("Newest first, up to 500 entries. The audit log is append-only: the app never edits or deletes it."))


def _settings_tab() -> None:
    cfg = settings()
    with st.form("settings_form", border=True):
        c1, c2 = st.columns(2, gap="medium")
        name = c1.text_input(t("Company name"), value=cfg.company_name)
        email = c2.text_input(t("Company email"), value=cfg.company_email)
        address = c1.text_area(t("Company address (printed on invoices)"), value=cfg.company_address, height=90)
        tax_id = c2.text_input(t("Company tax ID"), value=cfg.company_tax_id)
        c1, c2, c3, c4, c5 = st.columns(5, gap="medium")
        currency = c1.selectbox(t("Currency"), list(CURRENCIES), index=list(CURRENCIES).index(cfg.currency),
                                help=t("Symbol only; amounts are not converted."))
        tax_label = c2.text_input(t("Tax label"), value=cfg.tax_label, help=t("VAT, GST, Sales tax ..."))
        tax_rate = c3.number_input(t("Default tax %"), min_value=0.0, max_value=100.0, value=float(cfg.default_tax_rate), step=1.0)
        terms = c4.number_input(t("Payment terms (days)"), min_value=0, max_value=365, value=int(cfg.payment_terms_days))
        prefix = c5.text_input(t("Invoice prefix"), value=cfg.invoice_prefix)
        b1, b2, b3 = st.columns([1, 2, 1], gap="medium")
        brand = b1.color_picker(t("Brand colour"), value=cfg.brand_color, help=t("Invoice header colour. Pick a medium or dark colour."))
        logo_file = b2.file_uploader(t("Company logo (PNG or JPG, under 500 KB)"), type=["png", "jpg", "jpeg"])
        remove_logo = b3.checkbox(t("Remove current logo"), value=False, disabled=logo_bytes() is None)
        if st.form_submit_button(t("Save settings"), type="primary"):
            new = replace(cfg, company_name=name, company_email=email, company_address=address, company_tax_id=tax_id,
                          currency=currency, tax_label=tax_label.strip() or "Tax", default_tax_rate=Decimal(str(tax_rate)),
                          payment_terms_days=int(terms), invoice_prefix=prefix.strip(), brand_color=brand)
            problems = save_settings(actor(), new)
            if not problems and (logo_file is not None or remove_logo):
                problems = save_logo(actor(), logo_file.getvalue() if logo_file is not None else None)
            if problems:
                for p in problems:
                    st.error(tr_error(p))
            else:
                invalidate()
                flash(t("Settings saved"))
                st.rerun()


def _status_row(label: str, ok: bool, detail: str) -> None:
    st.markdown(f"{'🟢' if ok else '🟠'} **{t(label)}**: {t(detail)}")


def _backup_section() -> None:
    st.markdown("##### " + t("Backups"))
    cfg = backup.current_settings()
    if backup.sqlite_path() is None:
        st.info(t("This installation uses PostgreSQL/MySQL. Back it up with pg_dump / mysqldump or your hosting provider's "
                "automatic backups (turn on point-in-time recovery)."))
        return
    newest = backup.latest_backup()
    _status_row("Automatic backups", cfg.enabled,
                f"every {cfg.interval_hours:g} h, keeping the last {cfg.keep}, in `{cfg.directory}`" if cfg.enabled
                else "turned off (BACKUP_ENABLED=true to enable)")
    _status_row("Latest backup", newest is not None,
                f"{fdate(newest.created, '%b %d, %Y %H:%M')} ({newest.size / 1024:,.0f} KB)" if newest else t("none yet"))
    _status_row("Second location", cfg.extra_directory is not None,
                f"`{cfg.extra_directory}`" if cfg.extra_directory else
                "not set. Set BACKUP_EXTRA_DIR to a USB drive or a synced cloud folder so one dead disk cannot lose everything")
    c1, c2 = st.columns(2)
    if c1.button(t("Back up now"), **button_width()):
        try:
            path = backup.run_backup()
            flash(t("Backup saved: {name}", name=path.name))
        except backup.BackupError as exc:
            fail(exc)
        else:
            st.rerun()
    if newest is not None:
        c2.download_button(t("Download latest backup"), newest.path.read_bytes(), newest.path.name,
                           "application/octet-stream", **button_width())
    with st.expander(t("How to restore a backup")):
        st.markdown("1. Stop the app.  2. Copy the backup file over `data/ledgerline.db`.  "
                    "3. Delete `ledgerline.db-wal` and `ledgerline.db-shm` next to it, if present.  4. Start the app.")


def _data_tab() -> None:
    cfg, info = get_config(), database_info()
    st.markdown("##### " + t("System status"))
    _status_row("Database", True, f"{info['backend']} ({info.get('journal_mode', 'n/a')} journal) at `{info['url']}`")
    _status_row("Signing secret", not cfg.auth_secret_is_ephemeral,
                "AUTH_SECRET is set" if not cfg.auth_secret_is_ephemeral else "temporary key in use: everyone is signed out on restart. Set AUTH_SECRET.")
    _status_row("Personal-data encryption", bool(cfg.encryption_key), "ENCRYPTION_KEY set (client email/phone/tax ID encrypted)" if cfg.encryption_key else "not enabled (set ENCRYPTION_KEY)")
    _status_row("Persistent sessions", auth_ui.cookies_supported(), "extra-streamlit-components installed" if auth_ui.cookies_supported() else "package missing")
    _status_row("Secure cookies", cfg.cookie_secure, "cookies only sent over HTTPS" if cfg.cookie_secure else "off (fine on localhost, enable behind HTTPS)")
    _status_row("Email (SMTP)", get_email_service().configured, f"{cfg.smtp.host}:{cfg.smtp.port}" if cfg.smtp.host else "not configured")
    provider = get_provider()
    _status_row("WhatsApp automation", provider is not None, provider.name if provider else "manual wa.me links only")
    _status_row("PDF engine", REPORTLAB_AVAILABLE, "reportlab installed" if REPORTLAB_AVAILABLE else "pip install reportlab")

    st.markdown("##### " + t("Export"))
    c1, c2 = st.columns(2)
    c1.download_button(t("Invoices (CSV)"), audit.export_invoices_csv(actor()), "invoices.csv", "text/csv", **button_width())
    c2.download_button(t("Line items (CSV)"), audit.export_items_csv(actor()), "invoice_items.csv", "text/csv", **button_width())

    _backup_section()

    st.markdown("##### " + t("Import and sample data"))
    with st.expander(t("Import from Ledgerline v2.0")):
        db_path = st.text_input(t("Old database file"), value=str(BASE_DIR / "database.db"))
        csv_path = st.text_input(t("Old CSV file (optional)"), value=str(BASE_DIR / "invoices.csv"))
        if st.button(t("Import legacy data")):
            report = legacy.import_legacy(actor(), Path(db_path), Path(csv_path))
            invalidate()
            st.success(t("Imported {created} invoices, skipped {skipped} already present.", created=report.created, skipped=report.skipped))
            for err in report.errors[:10]:
                st.warning(tr_error(err))
    if st.button(t("Load 140 sample invoices (+ products, expenses)")):
        flash(t("Loaded {v1} sample invoices", v1=demo.seed_demo_data(actor())))
        invalidate()
        st.rerun()


def render() -> None:
    st.markdown('<div class="dash-header"><div><h1 class="dash-title">Administration</h1>'
                '<div class="dash-sub">Users, audit trail, business settings and system status</div></div></div>',
                unsafe_allow_html=True)
    t1, t2, t3, t4 = st.tabs([t("👥 Users"), t("🧾 Audit log"), t("⚙️ Settings"), t("🧰 Data & system")])
    with t1:
        _users_tab()
    with t2:
        _audit_tab()
    with t3:
        _settings_tab()
    with t4:
        _data_tab()
