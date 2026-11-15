"""Wires services to a store. Phase 2's MCP server will call build_services() once."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from merch_platform.domain.ports import (
    CatalogueRepository,
    Clock,
    InventoryRepository,
    OrderRepository,
    PricingRepository,
    PromotionRepository,
)
from merch_platform.services.catalogue import CatalogueService
from merch_platform.services.checks import SystemClock
from merch_platform.services.inventory import InventoryService
from merch_platform.services.orders import OrderService
from merch_platform.services.pricing_service import PricingService
from merch_platform.services.promotions import PromotionService


class Store(
    CatalogueRepository,
    PricingRepository,
    InventoryRepository,
    PromotionRepository,
    OrderRepository,
    Protocol,
):
    """Anything implementing all five repository ports (InMemoryStore, PostgresStore)."""


@dataclass(frozen=True)
class Services:
    catalogue: CatalogueService
    pricing: PricingService
    inventory: InventoryService
    promotions: PromotionService
    orders: OrderService


def build_services(store: Store, clock: Clock | None = None) -> Services:
    clock = clock or SystemClock()
    return Services(
        catalogue=CatalogueService(store, store, store),
        pricing=PricingService(store, store, clock),
        inventory=InventoryService(store, store),
        promotions=PromotionService(store, store, store, clock),
        orders=OrderService(store),
    )
