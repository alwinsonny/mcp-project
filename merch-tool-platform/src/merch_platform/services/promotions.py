"""Promotion creation: the riskiest write, because customers see it immediately.

Checks, in order:
1. Dates: a promotion cannot start in the past.
2. Code: promotion codes are unique.
3. Scope: categories must exist; SKUs must exist and be active; the scope must not be empty.
4. Blast radius: the number of affected SKUs must be within the caller's limit.
5. Exclusivity: an exclusive promotion may not overlap (same SKUs, overlapping dates)
   with any other promotion, and nothing may overlap an existing exclusive one.
6. Below cost: after today's price rules AND the discount, no SKU may sell below cost.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from merch_platform.domain.commands import (
    CreatePromotionRequest,
    PromotionLine,
    PromotionPlan,
    WriteLimits,
)
from merch_platform.domain.errors import (
    CategoryNotFound,
    InvalidDateRange,
    NoChange,
    PromotionBelowCost,
    PromotionConflict,
)
from merch_platform.domain.models import HUNDRED, Promotion, margin_pct, money
from merch_platform.domain.ports import (
    CatalogueRepository,
    Clock,
    PricingRepository,
    PromotionRepository,
)
from merch_platform.domain.pricing import explain_price
from merch_platform.services.catalogue import descendants
from merch_platform.services.checks import check_blast_radius, preview, require_active, require_skus

SAMPLE_SIZE = 20


class PromotionService:
    def __init__(
        self,
        catalogue: CatalogueRepository,
        pricing: PricingRepository,
        promotions: PromotionRepository,
        clock: Clock,
    ) -> None:
        self._catalogue = catalogue
        self._pricing = pricing
        self._promotions = promotions
        self._clock = clock

    async def resolve_scope(
        self, category_ids: list[str] | tuple[str, ...], skus: list[str] | tuple[str, ...]
    ) -> set[str]:
        scope: set[str] = set()
        if category_ids:
            categories = await self._catalogue.list_categories()
            known = {c.id for c in categories}
            unknown = set(category_ids) - known
            if unknown:
                raise CategoryNotFound(
                    f"Unknown category IDs: {preview(unknown)}.",
                    hint="Read catalog://taxonomy for valid category IDs.",
                )
            expanded: set[str] = set()
            for cid in category_ids:
                expanded |= descendants(categories, cid)
            scope |= await self._catalogue.skus_in_categories(expanded)
        if skus:
            products = await self._catalogue.get_products(skus)
            require_skus(products, skus)
            require_active(products)
            scope |= set(skus)
        return scope

    async def plan_promotion(
        self, request: CreatePromotionRequest, limits: WriteLimits
    ) -> PromotionPlan:
        today = self._clock.today()
        if request.starts < today:
            raise InvalidDateRange(
                f"Promotion would start on {request.starts}, which is in the past "
                f"(today is {today}).",
                hint=f"Choose a start date of {today} or later.",
            )
        if await self._promotions.code_exists(request.code):
            raise PromotionConflict(
                f"Promotion code {request.code} is already in use.",
                hint="Choose a different code.",
            )

        scope = await self.resolve_scope(request.category_ids, request.skus)
        if not scope:
            raise NoChange(
                "The requested categories and SKUs contain no active products.",
                hint="Check the category IDs with catalog://taxonomy.",
            )
        check_blast_radius(len(scope), limits)

        # --- exclusivity
        conflicts: dict[str, int] = {}
        for existing in await self._promotions.list_overlapping(request.starts, request.ends):
            if not (existing.exclusive or request.exclusive):
                continue
            overlap = scope & await self.resolve_scope(existing.category_ids, existing.skus)
            if overlap:
                conflicts[existing.code] = len(overlap)
        if conflicts:
            described = ", ".join(f"{code} ({n} SKUs)" for code, n in sorted(conflicts.items()))
            raise PromotionConflict(
                f"Overlaps with promotions where exclusivity applies: {described}.",
                hint="Change the dates, narrow the scope, or make neither promotion exclusive.",
                details={"conflicts": conflicts},
            )

        # --- below-cost check, using the price customers would actually see
        products = await self._catalogue.get_products(scope)
        prices = await self._pricing.get_prices(scope)
        require_skus(prices, scope)
        rules = await self._pricing.list_rules()

        lines: list[PromotionLine] = []
        below_cost: dict[str, Decimal] = {}  # sku -> max safe discount %
        for sku in sorted(scope):
            record = prices[sku]
            effective = explain_price(products[sku], record, rules, request.starts).final_price
            discounted = money(effective * (1 - request.discount_pct / HUNDRED))
            if effective > 0 and discounted < record.cost:
                safe = ((1 - record.cost / effective) * HUNDRED).quantize(
                    Decimal("0.01"), rounding=ROUND_FLOOR
                )
                below_cost[sku] = max(safe, Decimal("0"))
            lines.append(
                PromotionLine(
                    sku=sku,
                    price_before=effective,
                    price_after=discounted,
                    cost=record.cost,
                    margin_after_pct=margin_pct(discounted, record.cost),
                )
            )

        if below_cost:
            max_safe = min(below_cost.values())
            raise PromotionBelowCost(
                f"A {request.discount_pct}% discount would sell {len(below_cost)} SKU(s) "
                f"below cost: "
                f"{preview(below_cost)}.",
                hint=(
                    f"The largest discount that keeps every SKU at or above cost is {max_safe}%. "
                    "Or exclude the listed SKUs."
                ),
                details={"max_safe_discount_pct": str(max_safe), "skus": sorted(below_cost)},
            )

        def sort_key(line: PromotionLine) -> Decimal:
            return line.margin_after_pct if line.margin_after_pct is not None else Decimal("-1000")

        lines.sort(key=sort_key)
        promotion = Promotion(
            id=f"PROMO-{request.code}",
            code=request.code,
            name=request.name,
            discount_pct=request.discount_pct,
            category_ids=tuple(request.category_ids),
            skus=tuple(request.skus),
            starts=request.starts,
            ends=request.ends,
            exclusive=request.exclusive,
        )
        return PromotionPlan(
            promotion=promotion,
            affected_count=len(scope),
            lowest_margin_after_pct=lines[0].margin_after_pct if lines else None,
            sample_lines=tuple(lines[:SAMPLE_SIZE]),
        )

    async def apply_promotion(self, plan: PromotionPlan) -> None:
        await self._promotions.add_promotion(plan.promotion)
