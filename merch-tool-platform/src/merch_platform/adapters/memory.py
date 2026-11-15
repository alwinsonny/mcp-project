"""In-memory adapter: implements every repository port with plain dictionaries.

Tests use this so the whole domain suite runs in well under a second with no database.
The Postgres adapter must behave identically; tests/integration runs the same
contract tests against both to prove it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
from decimal import Decimal

from merch_platform.domain.commands import AdjustmentReason, ProductQuery, ProductSummary
from merch_platform.domain.models import (
    AttributeValue,
    Category,
    Order,
    PriceRecord,
    PriceRule,
    Product,
    Promotion,
    StockLevel,
    margin_pct,
)
from merch_platform.seed.generator import SeedData


class InMemoryStore:
    """One object that satisfies all five repository Protocols."""

    def __init__(self) -> None:
        self.categories: dict[str, Category] = {}
        self.products: dict[str, Product] = {}
        self.prices: dict[str, PriceRecord] = {}
        self.rules: dict[str, PriceRule] = {}
        self.stock: dict[str, StockLevel] = {}
        self.promotions: dict[str, Promotion] = {}
        self.orders: dict[str, Order] = {}
        self.adjustments: list[tuple[str, Decimal, str, str]] = []

    @classmethod
    def from_seed(cls, seed: SeedData) -> InMemoryStore:
        store = cls()
        store.categories = {c.id: c for c in seed.categories}
        store.products = {p.sku: p for p in seed.products}
        store.prices = {p.sku: p for p in seed.prices}
        store.rules = {r.id: r for r in seed.rules}
        store.stock = {s.sku: s for s in seed.stock}
        store.promotions = {p.id: p for p in seed.promotions}
        store.orders = {o.order_id: o for o in seed.orders}
        return store

    # ------------------------------------------------------------------ catalogue
    async def get_products(self, skus: Collection[str]) -> dict[str, Product]:
        return {s: self.products[s] for s in skus if s in self.products}

    async def search_products(
        self, query: ProductQuery, category_ids: Collection[str] | None
    ) -> list[ProductSummary]:
        terms = query.terms
        results: list[ProductSummary] = []
        for sku in sorted(self.products):
            product = self.products[sku]
            price = self.prices.get(sku)
            if price is None:
                continue
            if query.status != "any" and product.status != query.status:
                continue
            if category_ids is not None and product.category_id not in category_ids:
                continue
            haystack = f"{product.name} {product.sku}".lower()
            if not all(term in haystack for term in terms):
                continue
            margin = margin_pct(price.base_price, price.cost)
            if query.min_margin_pct is not None and (
                margin is None or margin < query.min_margin_pct
            ):
                continue
            if query.max_margin_pct is not None and (
                margin is None or margin > query.max_margin_pct
            ):
                continue
            results.append(
                ProductSummary(
                    sku=sku,
                    name=product.name,
                    category_id=product.category_id,
                    status=product.status,
                    base_price=price.base_price,
                    cost=price.cost,
                    margin_pct=margin,
                )
            )
            if len(results) > query.limit:
                break
        return results

    async def list_categories(self) -> list[Category]:
        return sorted(self.categories.values(), key=lambda c: c.id)

    async def skus_in_categories(self, category_ids: Collection[str]) -> set[str]:
        wanted = set(category_ids)
        return {
            p.sku
            for p in self.products.values()
            if p.category_id in wanted and p.status == "active"
        }

    async def save_attributes(self, updates: Mapping[str, Mapping[str, AttributeValue]]) -> None:
        for sku, attributes in updates.items():
            self.products[sku] = self.products[sku].model_copy(
                update={"attributes": dict(attributes)}
            )

    # ------------------------------------------------------------------ pricing
    async def get_prices(self, skus: Collection[str]) -> dict[str, PriceRecord]:
        return {s: self.prices[s] for s in skus if s in self.prices}

    async def list_rules(self) -> list[PriceRule]:
        return sorted(self.rules.values(), key=lambda r: (r.priority, r.id))

    async def save_base_prices(self, prices: Mapping[str, Decimal]) -> None:
        for sku, new_price in prices.items():
            self.prices[sku] = self.prices[sku].model_copy(update={"base_price": new_price})

    # ------------------------------------------------------------------ inventory
    async def get_stock(self, skus: Collection[str]) -> dict[str, StockLevel]:
        return {s: self.stock[s] for s in skus if s in self.stock}

    async def save_on_hand(self, sku: str, on_hand: int) -> None:
        current = self.stock.get(sku) or StockLevel(sku=sku, on_hand=0, reserved=0)
        self.stock[sku] = current.model_copy(update={"on_hand": on_hand})

    # ------------------------------------------------------------------ promotions
    async def list_overlapping(self, starts: date, ends: date) -> list[Promotion]:
        return sorted(
            (p for p in self.promotions.values() if p.overlaps_dates(starts, ends)),
            key=lambda p: p.id,
        )

    async def code_exists(self, code: str) -> bool:
        return any(p.code == code for p in self.promotions.values())

    async def add_promotion(self, promotion: Promotion) -> None:
        self.promotions[promotion.id] = promotion

    # ------------------------------------------------------------------ orders
    async def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    async def add_adjustment(
        self, order_id: str, amount: Decimal, reason_code: AdjustmentReason, note: str
    ) -> None:
        order = self.orders[order_id]
        self.orders[order_id] = order.model_copy(
            update={"adjustments_total": order.adjustments_total + amount}
        )
        self.adjustments.append((order_id, amount, reason_code, note))
