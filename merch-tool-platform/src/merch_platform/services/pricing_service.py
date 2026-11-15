"""Pricing operations: explain a price, and plan/apply base price changes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from merch_platform.domain.commands import (
    PriceChangeLine,
    PriceChangePlan,
    PriceExplanation,
    SetPriceRequest,
    WriteLimits,
)
from merch_platform.domain.errors import MarginFloorBreached, NoChange, SkuNotFound
from merch_platform.domain.models import margin_pct, min_price_for_margin
from merch_platform.domain.ports import CatalogueRepository, Clock, PricingRepository
from merch_platform.domain.pricing import explain_price
from merch_platform.services.checks import check_blast_radius, preview, require_active, require_skus


class PricingService:
    def __init__(
        self, catalogue: CatalogueRepository, pricing: PricingRepository, clock: Clock
    ) -> None:
        self._catalogue = catalogue
        self._pricing = pricing
        self._clock = clock

    async def explain_price(self, sku: str, on: date | None = None) -> PriceExplanation:
        products = await self._catalogue.get_products([sku])
        prices = await self._pricing.get_prices([sku])
        if sku not in products or sku not in prices:
            raise SkuNotFound(
                f"SKU {sku} not found.", hint="Use search_products to find valid SKUs."
            )
        rules = await self._pricing.list_rules()
        return explain_price(products[sku], prices[sku], rules, on or self._clock.today())

    async def plan_price_change(
        self, request: SetPriceRequest, limits: WriteLimits
    ) -> PriceChangePlan:
        """Preview a base price change. Changes nothing.

        The margin floor is checked against the new BASE price. Promotions have their
        own below-cost check (see PromotionService).
        """
        check_blast_radius(len(request.skus), limits)
        products = await self._catalogue.get_products(request.skus)
        require_skus(products, request.skus)
        require_active(products)
        prices = await self._pricing.get_prices(request.skus)
        require_skus(prices, request.skus)

        breaches: dict[str, Decimal] = {}
        lines: list[PriceChangeLine] = []
        for sku in sorted(request.skus):
            record = prices[sku]
            floor_price = min_price_for_margin(record.cost, limits.margin_floor_pct)
            if request.new_price < floor_price:
                breaches[sku] = floor_price
                continue
            if record.base_price == request.new_price:
                continue
            lines.append(
                PriceChangeLine(
                    sku=sku,
                    cost=record.cost,
                    old_price=record.base_price,
                    new_price=request.new_price,
                    old_margin_pct=record.margin_pct,
                    new_margin_pct=margin_pct(request.new_price, record.cost),
                )
            )

        if breaches:
            lowest_safe = max(breaches.values())
            raise MarginFloorBreached(
                f"£{request.new_price} would put {len(breaches)} SKU(s) below the "
                f"{limits.margin_floor_pct}% margin floor: {preview(breaches)}.",
                hint=(
                    f"The lowest price that keeps every requested SKU at or above the floor is "
                    f"£{lowest_safe}. Or remove the listed SKUs; per-SKU minimums are in details."
                ),
                details={
                    "min_allowed_price": {k: str(v) for k, v in breaches.items()},
                    "floor_pct": str(limits.margin_floor_pct),
                },
            )
        if not lines:
            raise NoChange(
                "Every SKU already has this price; nothing would change.", hint="No action needed."
            )
        return PriceChangePlan(reason=request.reason, lines=tuple(lines))

    async def apply_price_change(self, plan: PriceChangePlan) -> None:
        await self._pricing.save_base_prices({line.sku: line.new_price for line in plan.lines})
