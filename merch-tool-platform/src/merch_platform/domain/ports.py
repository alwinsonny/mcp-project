"""Ports: the interfaces the domain depends on.

The domain says WHAT it needs ("give me these products"), never HOW (SQL, HTTP).
Adapters implement these Protocols: adapters/memory.py for fast tests and
adapters/postgres.py for real use. Any class with matching methods fits; no
inheritance is required (this is structural typing).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
from decimal import Decimal
from typing import Protocol

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
)


class Clock(Protocol):
    def today(self) -> date: ...


class CatalogueRepository(Protocol):
    async def get_products(self, skus: Collection[str]) -> dict[str, Product]:
        """Products keyed by SKU. Unknown SKUs are simply absent from the result."""
        ...

    async def search_products(
        self, query: ProductQuery, category_ids: Collection[str] | None
    ) -> list[ProductSummary]:
        """Every query term must appear in the name or SKU (case-insensitive).

        category_ids is the already-expanded set (category plus descendants),
        or None for no category filter. Results are ordered by SKU and capped at
        query.limit + 1, so callers can tell whether results were truncated.
        """
        ...

    async def list_categories(self) -> list[Category]: ...

    async def skus_in_categories(self, category_ids: Collection[str]) -> set[str]:
        """Active SKUs whose category is in the given set."""
        ...

    async def save_attributes(self, updates: Mapping[str, Mapping[str, AttributeValue]]) -> None:
        """Replace the full attribute dict of each SKU given."""
        ...


class PricingRepository(Protocol):
    async def get_prices(self, skus: Collection[str]) -> dict[str, PriceRecord]: ...

    async def list_rules(self) -> list[PriceRule]: ...

    async def save_base_prices(self, prices: Mapping[str, Decimal]) -> None: ...


class InventoryRepository(Protocol):
    async def get_stock(self, skus: Collection[str]) -> dict[str, StockLevel]: ...

    async def save_on_hand(self, sku: str, on_hand: int) -> None: ...


class PromotionRepository(Protocol):
    async def list_overlapping(self, starts: date, ends: date) -> list[Promotion]: ...

    async def code_exists(self, code: str) -> bool: ...

    async def add_promotion(self, promotion: Promotion) -> None: ...


class OrderRepository(Protocol):
    async def get_order(self, order_id: str) -> Order | None: ...

    async def add_adjustment(
        self, order_id: str, amount: Decimal, reason_code: AdjustmentReason, note: str
    ) -> None: ...
