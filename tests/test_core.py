"""Core behaviour: money maths, RBAC, audit trail, payments, sessions, concurrency, outbox.
Run:  pip install -r requirements-dev.txt  &&  pytest -q
"""

import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest

from ledgerline import calc, db, security
from ledgerline.models import AuditLog, Invoice, utcnow
from ledgerline.security import PermissionDenied
from ledgerline.services import invoices as inv
from ledgerline.services import outbox, users

TODAY = date.today()


def make_actors():
    users.create_user(None, "root", "Root-password-1", "admin")
    admin = users.list_users()[0].actor()
    users.create_user(admin, "mia", "Manager-pass-1", "manager")
    users.create_user(admin, "vic", "Viewer-pass-12", "viewer")
    by_name = {u.username: u.actor() for u in users.list_users()}
    return by_name["root"], by_name["mia"], by_name["vic"]


def invoice_data(**kw):
    base = dict(issue_date=TODAY, due_date=TODAY + timedelta(days=14), category="Design", payment_method="Cash",
                items=[inv.ItemInput("Logo", Decimal(2), Decimal("150"), Decimal("10"), Decimal(0), Decimal("18")),
                       inv.ItemInput("Hosting", Decimal(1), Decimal("40"), tax_rate=Decimal("5"))],
                new_client=inv.ClientInput("Zeta Ltd", email="z@z.test"))
    base.update(kw)
    return inv.InvoiceInput(**base)


def test_line_and_invoice_totals_round_per_line():
    line = calc.line_amounts(2, "150", 10, 0, 18)
    assert (line.subtotal, line.discount, line.tax, line.total) == (Decimal("300.00"), Decimal("30.00"), Decimal("48.60"), Decimal("318.60"))
    assert calc.line_amounts(1, 10, 0, 50, 0).discount == Decimal("10.00")  # discount never exceeds the line


def test_status_rules():
    assert calc.derive_status(Decimal(100), Decimal(0), False) == "Pending"
    assert calc.derive_status(Decimal(100), Decimal(40), False) == "Partially Paid"
    assert calc.derive_status(Decimal(100), Decimal(100), False) == "Paid"
    assert calc.display_status("Pending", TODAY - timedelta(days=1), TODAY) == "Overdue"


def test_orm_hook_calculates_totals_and_partial_payments(env):
    admin, mgr, _ = make_actors()
    iid = inv.create_invoice(mgr, invoice_data())
    d = inv.get_invoice_detail(iid)
    assert d.total == Decimal("360.60") and d.status == "Pending"
    inv.add_payment(mgr, iid, Decimal("100"), TODAY, "Cash")
    d = inv.get_invoice_detail(iid)
    assert d.status == "Partially Paid" and d.balance == Decimal("260.60")
    inv.add_payment(mgr, iid, d.balance, TODAY, "Stripe")
    assert inv.get_invoice_detail(iid).status == "Paid"
    with pytest.raises(inv.InvoiceError):
        inv.add_payment(mgr, iid, Decimal("1"), TODAY, "Cash")  # nothing left to pay


def test_rbac(env):
    admin, mgr, viewer = make_actors()
    with pytest.raises(PermissionDenied):
        inv.create_invoice(viewer, invoice_data())
    iid = inv.create_invoice(mgr, invoice_data())
    with pytest.raises(PermissionDenied):
        inv.delete_invoice(mgr, iid)
    with db.write_session() as s:  # make it older than the manager edit window
        s.get(Invoice, iid).created_at = utcnow() - timedelta(days=30)
    with pytest.raises(PermissionDenied):
        inv.update_invoice(mgr, iid, invoice_data(client_id=inv.get_invoice_detail(iid).client_id, new_client=None))
    inv.update_invoice(admin, iid, invoice_data(client_id=inv.get_invoice_detail(iid).client_id, new_client=None))
    inv.delete_invoice(admin, iid)
    assert inv.get_invoice_detail(iid) is None  # soft deleted


def test_audit_trail_records_who_and_what(env):
    admin, mgr, _ = make_actors()
    iid = inv.create_invoice(mgr, invoice_data())
    d = inv.get_invoice_detail(iid)
    inv.update_invoice(admin, iid, invoice_data(client_id=d.client_id, new_client=None, notes="changed"), d.version)
    inv.delete_invoice(admin, iid)
    with db.read_session() as s:
        rows = s.query(AuditLog).filter(AuditLog.invoice_id == iid).order_by(AuditLog.id).all()
    assert [(r.username, r.action) for r in rows if r.action in ("CREATE", "UPDATE", "DELETE")][:1] == [("mia", "CREATE")]
    assert {"CREATE", "UPDATE", "DELETE"} <= {r.action for r in rows}
    assert any(r.username == "root" and r.action == "DELETE" for r in rows)


def test_optimistic_locking_blocks_stale_edit(env):
    admin, mgr, _ = make_actors()
    iid = inv.create_invoice(mgr, invoice_data())
    d = inv.get_invoice_detail(iid)
    data = invoice_data(client_id=d.client_id, new_client=None, notes="first")
    inv.update_invoice(mgr, iid, data, d.version)
    with pytest.raises(inv.ConcurrencyError):
        inv.update_invoice(mgr, iid, data, d.version)


def test_sessions_lockout_and_revocation(env):
    make_actors()
    assert users.authenticate("root", "wrong").user is None
    ok = users.authenticate("ROOT", "Root-password-1")
    assert ok.user and ok.user.role == "admin"
    token, jti, _ = users.start_session(ok.user.id)
    assert users.resolve_token(token)[0].username == "root"
    assert users.resolve_token(token[:-3] + "abc") is None  # tampered signature
    users.revoke_session(jti)
    assert users.resolve_token(token) is None
    for _ in range(5):
        users.authenticate("mia", "nope")
    assert "Too many" in users.authenticate("mia", "Manager-pass-1").error


def test_password_hash_roundtrip():
    stored = security.hash_password("correct horse 9", iterations=1000)
    assert security.verify_password("correct horse 9", stored) and not security.verify_password("x", stored)


def test_concurrent_writers_never_hit_database_is_locked(env):
    admin, mgr, _ = make_actors()
    errors = []

    def worker(w):
        for n in range(10):
            try:
                inv.create_invoice(mgr, invoice_data(number=f"T{w}-{n}", new_client=inv.ClientInput(f"Client {w}")))
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    with db.read_session() as s:
        assert s.query(Invoice).count() == 80


def test_encryption_at_rest(env, monkeypatch):
    from cryptography.fernet import Fernet
    from sqlalchemy import text
    from ledgerline import config
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    admin, mgr, _ = make_actors()
    inv.create_invoice(mgr, invoice_data())
    with db.get_engine().connect() as conn:
        raw = conn.execute(text("SELECT email FROM clients")).scalar()
    assert raw.startswith("enc:v1:") and "z@z.test" not in raw
    assert inv.list_clients()[0].email == "z@z.test"


def test_outbox_delivers_email_with_pdf_and_retries(env, monkeypatch):
    pytest.importorskip("aiosmtpd")
    import email
    from email import policy
    from aiosmtpd.controller import Controller
    from ledgerline import config
    from ledgerline.integrations import email as mail

    got = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):  # noqa: N802
            got.append(envelope)
            return "250 OK"

    server = Controller(Handler(), hostname="127.0.0.1", port=8037)
    server.start()
    try:
        for k, v in dict(SMTP_HOST="127.0.0.1", SMTP_PORT="8037", SMTP_SECURITY="NONE",
                         SMTP_FROM_ADDRESS="billing@test.local", SMTP_RETRIES="1").items():
            monkeypatch.setenv(k, v)
        config.get_config.cache_clear(); mail.get_email_service.cache_clear()
        admin, mgr, _ = make_actors()
        iid = inv.create_invoice(mgr, invoice_data())
        outbox.enqueue_email(mgr, iid, "client@test.local", "Your invoice", "Hello!\n\nThanks.")
        assert outbox.process_due_messages() == 1
        assert outbox.recent_messages(iid)[0].status == "sent"
        msg = email.message_from_bytes(got[0].content, policy=policy.default)
        assert [a.get_filename() for a in msg.iter_attachments()] == [f"{inv.get_invoice_detail(iid).number}.pdf"]
        assert msg.get_body(preferencelist=("html",)) is not None

        server.stop()  # server down: failure is retryable, message goes back to the queue with backoff
        outbox.enqueue_email(mgr, iid, "client@test.local", "Again", "Body")
        assert outbox.process_due_messages() == 1
        latest = outbox.recent_messages(iid)[0]
        assert latest.status == "queued" and latest.attempts == 1 and latest.last_error
    finally:
        try:
            server.stop()
        except Exception:  # noqa: BLE001
            pass
