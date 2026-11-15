"""Catalogue operations: search, product detail, taxonomy and bulk attribute updates."""

from __future__ import annotations

from merch_platform.domain.commands import (
    PROTECTED_ATTRIBUTES,
    AttributeChangeLine,
    AttributeChangePlan,
    ProductDetail,
    ProductQuery,
    ProductSearchResult,
    TaxonomyNode,
    UpdateAttributesRequest,
    WriteLimits,
)
from merch_platform.domain.errors import CategoryNotFound, NoChange, ProtectedAttribute, SkuNotFound
from merch_platform.domain.models import AttributeValue, Category, margin_pct
from merch_platform.domain.ports import CatalogueRepository, InventoryRepository, PricingRepository
from merch_platform.services.checks import check_blast_radius, require_active, require_skus


def descendants(categories: list[Category], root_id: str) -> set[str]:
    """The category itself plus every category below it in the tree."""
    children: dict[str, list[str]] = {}
    for c in categories:
        if c.parent_id is not None:
            children.setdefault(c.parent_id, []).append(c.id)
    result, stack = set(), [root_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue  # defends against an accidental cycle in the data
        result.add(current)
        stack.extend(children.get(current, []))
    return result


class CatalogueService:
    def __init__(
        self,
        catalogue: CatalogueRepository,
        pricing: PricingRepository,
        inventory: InventoryRepository,
    ) -> None:
        self._catalogue = catalogue
        self._pricing = pricing
        self._inventory = inventory

    # ------------------------------------------------------------------ reads
    async def search(self, query: ProductQuery) -> ProductSearchResult:
        category_ids: set[str] | None = None
        if query.category_id is not None:
            categories = await self._catalogue.list_categories()
            if query.category_id not in {c.id for c in categories}:
                raise CategoryNotFound(
                    f"Category {query.category_id} does not exist.",
                    hint="Read catalog://taxonomy for valid category IDs.",
                )
            category_ids = descendants(categories, query.category_id)

        rows = await self._catalogue.search_products(query, category_ids)
        truncated = len(rows) > query.limit
        items = tuple(rows[: query.limit])
        return ProductSearchResult(items=items, count=len(items), truncated=truncated)

    async def get_product(self, sku: str) -> ProductDetail:
        products = await self._catalogue.get_products([sku])
        prices = await self._pricing.get_prices([sku])
        if sku not in products or sku not in prices:
            raise SkuNotFound(
                f"SKU {sku} not found.", hint="Use search_products to find valid SKUs."
            )
        stock = (await self._inventory.get_stock([sku])).get(sku)
        price = prices[sku]
        return ProductDetail(
            product=products[sku],
            price=price,
            stock=stock,
            margin_pct=margin_pct(price.base_price, price.cost),
        )

    async def taxonomy(self) -> list[TaxonomyNode]:
        categories = await self._catalogue.list_categories()
        by_parent: dict[str | None, list[Category]] = {}
        for c in sorted(categories, key=lambda c: c.id):
            by_parent.setdefault(c.parent_id, []).append(c)

        def build(category: Category) -> TaxonomyNode:
            kids = tuple(build(child) for child in by_parent.get(category.id, []))
            return TaxonomyNode(category=category, children=kids)

        return [build(root) for root in by_parent.get(None, [])]

    # ------------------------------------------------------------------ writes
    async def plan_attribute_update(
        self, request: UpdateAttributesRequest, limits: WriteLimits
    ) -> AttributeChangePlan:
        protected = PROTECTED_ATTRIBUTES & set(request.set_values)
        if protected:
            raise ProtectedAttribute(
                "These fields cannot be changed with an attribute update: "
                f"{', '.join(sorted(protected))}.",
                hint="Use set_price for prices; category and status changes are not supported.",
            )
        check_blast_radius(len(request.skus), limits)

        products = await self._catalogue.get_products(request.skus)
        require_skus(products, request.skus)
        require_active(products)

        lines: list[AttributeChangeLine] = []
        after: dict[str, dict[str, AttributeValue]] = {}
        for sku in sorted(request.skus):
            current = dict(products[sku].attributes)
            changed = False
            for key, new_value in sorted(request.set_values.items()):
                old_value = current.get(key)
                if old_value == new_value and type(old_value) is type(new_value):
                    continue
                lines.append(
                    AttributeChangeLine(
                        sku=sku, attribute=key, old_value=old_value, new_value=new_value
                    )
                )
                current[key] = new_value
                changed = True
            if changed:
                after[sku] = current

        if not lines:
            raise NoChange(
                "Every SKU already has these attribute values; nothing would change.",
                hint="No action needed.",
            )
        return AttributeChangePlan(reason=request.reason, lines=tuple(lines), after=after)

    async def apply_attribute_update(self, plan: AttributeChangePlan) -> None:
        await self._catalogue.save_attributes(plan.after)
