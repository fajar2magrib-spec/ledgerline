"""Demo data so a fresh install has something to look at (admin-only button)."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from ..db import write_session
from ..events import Actor, acting_as, log_event
from ..models import Client, Expense, Invoice, InvoiceItem, Payment, Product
from ..security import require
from sqlalchemy import select, func

CLIENTS = ["Northwind Studio", "Brightpath Media", "Alder & Finch", "Quill Labs", "Harbor Coffee Co.",
           "Sable Architects", "Orbit Fitness", "Lumen Dental", "Kite Analytics", "Redwood Legal",
           "Pixel Forge", "Atlas Freight", "Nimbus Cloud", "Verde Landscaping", "Summit Realty"]
CATALOG = {
    "Consulting": [("Strategy workshop", 900), ("Advisory hours", 120)],
    "Design": [("Brand identity", 1400), ("Landing page design", 800)],
    "Development": [("Feature development", 95), ("API integration", 1600)],
    "Subscriptions": [("Monthly plan", 79), ("Annual plan", 790)],
    "Products": [("Starter kit", 49), ("Pro bundle", 149)],
    "Support": [("Support retainer", 450), ("Incident response", 180)],
}
PRODUCTS = [("Strategy workshop", "Consulting", 900), ("Advisory hours", "Consulting", 120),
            ("Brand identity", "Service", 1400), ("Landing page design", "Service", 800),
            ("Feature development", "Service", 95), ("API integration", "Service", 1600),
            ("Monthly plan", "Subscription", 79), ("Annual plan", "Subscription", 790),
            ("Starter kit", "Digital product", 49), ("Pro bundle", "Digital product", 149),
            ("Support retainer", "Support", 450), ("Incident response", "Support", 180)]
EXPENSES = [("Figma", "Software & tools", 45), ("Google Ads", "Advertising", 620), ("Meta Ads", "Advertising", 410),
            ("AWS", "Hosting & domains", 180), ("Namecheap", "Hosting & domains", 18), ("Stripe fees", "Payment fees", 95),
            ("Freelance developer", "Contractors", 850), ("Notion", "Software & tools", 16)]
METHODS = ["Credit Card", "Bank Transfer", "PayPal", "Stripe", "Cash"]


def seed_demo_data(actor: Actor, count: int = 140, prefix: str = "DEMO", tax_rate: Decimal = Decimal("10")) -> int:
    """Create ``count`` realistic invoices with items, tax and partial payments, plus a price list and expenses.

    Writes one audit entry for the whole seeding run instead of one per row.
    """
    require(actor, "data.import")
    rng, today, created = random.Random(7), date.today(), 0
    with acting_as(actor), write_session() as s:
        s.info["skip_audit"] = True
        clients = {}
        for name in CLIENTS:
            existing = s.scalar(select(Client).where(Client.name_key == name.lower()))
            clients[name] = existing or Client(
                name=name, email=f"billing@{name.split()[0].lower().strip('.,&')}.example",
                phone=f"+1555{rng.randint(1000000, 9999999)}", address=f"{rng.randint(10, 999)} Market Street, Demo City")
            s.add(clients[name])
        for pname, pcat, pprice in PRODUCTS:  # price list; skipped if the product already exists
            if not s.scalar(select(Product).where(Product.name_key == pname.lower())):
                s.add(Product(name=pname, category=pcat, unit_price=Decimal(pprice), tax_rate=tax_rate))
        for _ in range(36):  # about three expenses a month for the last year
            vendor, ecat, base = rng.choice(EXPENSES)
            s.add(Expense(spent_on=today - timedelta(days=rng.randint(0, 360)), vendor=vendor, category=ecat,
                          amount=(Decimal(base) * Decimal(str(round(rng.uniform(.8, 1.3), 2)))).quantize(Decimal("0.01")),
                          note="Demo expense", created_by_id=actor.user_id))
        start = s.scalar(select(func.count(Invoice.id))) or 0
        for n in range(count):
            age = int(rng.betavariate(1, 1.6) * 360)  # skewed towards recent dates
            issued = today - timedelta(days=age)
            category = rng.choice(list(CATALOG))
            inv = Invoice(number=f"{prefix}-{start + n + 1:04d}", client=clients[rng.choice(CLIENTS)],
                          issue_date=issued, due_date=issued + timedelta(days=14), category=category,
                          payment_method=rng.choice(METHODS), created_by_id=actor.user_id)
            for desc, price in rng.sample(CATALOG[category], k=rng.randint(1, 2)):
                inv.items.append(InvoiceItem(
                    description=desc, quantity=Decimal(rng.randint(1, 6)), unit_price=Decimal(price) * Decimal(str(round(rng.uniform(.8, 1.3), 2))),
                    discount_pct=Decimal(rng.choice([0, 0, 0, 5, 10])), tax_rate=rng.choice([Decimal(0), tax_rate])))
            inv.expenses = (sum((i.quantity * i.unit_price for i in inv.items), Decimal(0)) * Decimal(str(round(rng.uniform(.2, .4), 2)))).quantize(Decimal("0.01"))
            s.add(inv)
            s.flush()  # totals are calculated here, so payments below can use inv.total
            roll = rng.random()
            if roll < .04:
                inv.status = "Cancelled"
            elif roll < .62 or age > 60 and roll < .85:
                inv.payments.append(Payment(amount=inv.total, paid_on=min(today, issued + timedelta(days=rng.randint(1, 20))),
                                            method=inv.payment_method, created_by_id=actor.user_id))
            elif roll < .74:
                inv.payments.append(Payment(amount=(inv.total * Decimal("0.4")).quantize(Decimal("0.01")),
                                            paid_on=min(today, issued + timedelta(days=5)), method=inv.payment_method,
                                            reference="Deposit", created_by_id=actor.user_id))
            created += 1
        log_event(s, "DEMO_SEED", f"Loaded {created} demo invoices", entity_type="System", actor=actor)
    return created
