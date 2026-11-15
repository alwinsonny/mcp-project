"""Inventory operations: stock positions and on-hand corrections."""

from __future__ import annotations

from collections.abc import Sequence

from merch_platform.domain.commands import (
    AdjustInventoryRequest,
    InventoryAdjustmentPlan,
    InventoryPosition,
)
from merch_platform.domain.errors import NoChange, StockBelowReserved
from merch_platform.domain.ports import CatalogueRepository, InventoryRepository
from merch_platform.services.checks import require_skus

MAX_POSITIONS = 50


class InventoryService:
    def __init__(self, catalogue: CatalogueRepository, inventory: InventoryRepository) -> None:
        self._catalogue = catalogue
        self._inventory = inventory

    async def get_positions(self, skus: Sequence[str]) -> list[InventoryPosition]:
        if not 1 <= len(skus) <= MAX_POSITIONS:
            raise ValueError(f"ask for between 1 and {MAX_POSITIONS} SKUs")
        products = await self._catalogue.get_products(skus)
        require_skus(products, skus)
        stock = await self._inventory.get_stock(skus)
        positions = []
        for sku in sorted(set(skus)):
            level = stock.get(sku)
            on_hand = level.on_hand if level else 0
            reserved = level.reserved if level else 0
            positions.append(
                InventoryPosition(
                    sku=sku,
                    on_hand=on_hand,
                    reserved=reserved,
                    available=max(on_hand - reserved, 0),
                    oversold=reserved > on_hand,
                )
            )
        return positions

    async def plan_adjustment(self, request: AdjustInventoryRequest) -> InventoryAdjustmentPlan:
        products = await self._catalogue.get_products([request.sku])
        require_skus(products, [request.sku])
        level = (await self._inventory.get_stock([request.sku])).get(request.sku)
        old_on_hand = level.on_hand if level else 0
        reserved = level.reserved if level else 0

        if request.new_on_hand == old_on_hand:
            raise NoChange(
                f"{request.sku} already has {old_on_hand} on hand.", hint="No action needed."
            )
        # Lowering stock below what customers have already been promised creates
        # (or worsens) an oversell. Raising it is always allowed, even if still short.
        if request.new_on_hand < reserved and request.new_on_hand < old_on_hand:
            raise StockBelowReserved(
                f"{request.sku} has {reserved} units reserved for open orders; "
                f"setting on-hand to {request.new_on_hand} would oversell.",
                hint=f"Set on-hand to at least {reserved}, or resolve the open orders first.",
                details={"reserved": reserved, "requested": request.new_on_hand},
            )
        return InventoryAdjustmentPlan(
            sku=request.sku,
            reason_code=request.reason_code,
            note=request.note,
            old_on_hand=old_on_hand,
            new_on_hand=request.new_on_hand,
            reserved=reserved,
            new_available=max(request.new_on_hand - reserved, 0),
        )

    async def apply_adjustment(self, plan: InventoryAdjustmentPlan) -> None:
        await self._inventory.save_on_hand(plan.sku, plan.new_on_hand)
