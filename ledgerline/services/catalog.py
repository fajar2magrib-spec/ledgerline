"""Product / service price list: reusable items for invoices."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from ..db import read_session, write_session
from ..events import Actor, acting_as, log_event
from ..models import Product
from ..security import require

PRODUCT_CATEGORIES = ("Digital product", "Template", "Course", "Subscription", "Service", "Consulting", "Support", "General")
MAX_PRICE = Decimal("1000000000")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ProductInput:
    name: str
    unit_price: Decimal
    tax_rate: Decimal = Decimal(0)
    sku: str = ""
    category: str = "General"
    description: str = ""


@dataclass(frozen=True)
class ProductView:
    id: int
    name: str
    sku: str
    category: str
    description: str
    unit_price: Decimal
    tax_rate: Decimal
    is_active: bool


def validate(data: ProductInput) -> list[str]:
    problems = []
    if not " ".join(data.name.split()):
        problems.append("The product name is required.")
    if not 0 <= data.unit_price <= MAX_PRICE:
        problems.append("The price must be zero or more.")
    if not 0 <= data.tax_rate <= 100:
        problems.append("The tax rate must be between 0 and 100.")
    return problems


def _view(p: Product) -> ProductView:
    return ProductView(p.id, p.name, p.sku or "", p.category, p.description or "", p.unit_price, p.tax_rate, bool(p.is_active))


def list_products(active_only: bool = True) -> list[ProductView]:
    with read_session() as s:
        query = select(Product).order_by(func.lower(Product.name))
        if active_only:
            query = query.where(Product.is_active.is_(True))
        return [_view(p) for p in s.scalars(query)]


def create_product(actor: Actor, data: ProductInput) -> int:
    require(actor, "product.manage")
    problems = validate(data)
    if problems:
        raise CatalogError(" ".join(problems))
    key = " ".join(data.name.split()).lower()
    with acting_as(actor), write_session() as s:
        if s.scalar(select(Product.id).where(Product.name_key == key)):
            raise CatalogError("A product with that name already exists.")
        product = Product(name=data.name, sku=data.sku.strip() or None, category=data.category,
                          description=data.description.strip() or None, unit_price=data.unit_price, tax_rate=data.tax_rate)
        s.add(product)
        s.flush()
        log_event(s, "CREATE", f"Created product {product.name}", entity_type="Product", entity_id=product.id, actor=actor)
        return product.id


def update_product(actor: Actor, product_id: int, data: ProductInput) -> None:
    require(actor, "product.manage")
    problems = validate(data)
    if problems:
        raise CatalogError(" ".join(problems))
    key = " ".join(data.name.split()).lower()
    with acting_as(actor), write_session() as s:
        product = s.get(Product, product_id)
        if product is None:
            raise CatalogError("Product not found.")
        if s.scalar(select(Product.id).where(Product.name_key == key, Product.id != product_id)):
            raise CatalogError("Another product already uses that name.")
        product.name, product.sku, product.category = data.name, data.sku.strip() or None, data.category
        product.description, product.unit_price, product.tax_rate = data.description.strip() or None, data.unit_price, data.tax_rate
        log_event(s, "UPDATE", f"Updated product {product.name}", entity_type="Product", entity_id=product.id, actor=actor)


def set_active(actor: Actor, product_id: int, active: bool) -> None:
    """Archive (hide from the invoice picker) or restore a product. Nothing is ever deleted."""
    require(actor, "product.manage")
    with acting_as(actor), write_session() as s:
        product = s.get(Product, product_id)
        if product is None:
            raise CatalogError("Product not found.")
        product.is_active = active
        log_event(s, "UPDATE", f"{'Restored' if active else 'Archived'} product {product.name}", entity_type="Product",
                  entity_id=product.id, actor=actor)
