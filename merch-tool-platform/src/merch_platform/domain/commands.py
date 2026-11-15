"""Typed inputs and outputs for every domain operation.

Requests validate SHAPE (types, ranges, formats). Business rules that need data
(margin floors, conflicts, stock levels) are checked in the services, because a
schema cannot see the database.

Every write is two steps: plan_*() builds a *plan* (a preview with before and after
values) without changing anything, and apply_*() carries a plan out. Phase 8 builds
dry runs, change sets and rollback directly on top of these plans.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from merch_platform.domain.models import (
    AttributeValue,
    Category,
    DomainModel,
    PriceRecord,
    Product,
    ProductStatus,
    Promotion,
    StockLevel,
)

Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=40)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
MAX_SKUS_PER_REQUEST = 500

# Attributes that must never be changed through a bulk attribute update.
PROTECTED_ATTRIBUTES = frozenset({"sku", "category_id", "status", "price", "cost"})


def _unique(skus: list[str]) -> list[str]:
    """Reject repeated SKUs: a duplicate usually means the caller built the list wrongly."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for sku in skus:
        if sku in seen:
            dupes.add(sku)
        seen.add(sku)
    if dupes:
        raise ValueError(f"duplicate SKUs: {', '.join(sorted(dupes))}")
    return skus


class WriteLimits(DomainModel):
    """Per-caller limits. In Phase 7 each principal gets its own values."""

    max_skus: int = Field(ge=1, le=MAX_SKUS_PER_REQUEST)
    margin_floor_pct: Decimal = Field(default=Decimal("10"), ge=0, lt=100)


# --------------------------------------------------------------------------- reads
class ProductQuery(DomainModel):
    query: str = Field(default="", max_length=100)
    category_id: str | None = None
    min_margin_pct: Decimal | None = Field(default=None, ge=-1000, le=100)
    max_margin_pct: Decimal | None = Field(default=None, ge=-1000, le=100)
    status: Literal["active", "disabled", "any"] = "active"
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _range(self) -> ProductQuery:
        if (
            self.min_margin_pct is not None
            and self.max_margin_pct is not None
            and self.min_margin_pct > self.max_margin_pct
        ):
            raise ValueError("min_margin_pct cannot be greater than max_margin_pct")
        return self

    @property
    def terms(self) -> list[str]:
        return [t for t in self.query.lower().split() if t]


class ProductSummary(DomainModel):
    sku: str
    name: str
    category_id: str
    status: ProductStatus
    base_price: Decimal
    cost: Decimal
    margin_pct: Decimal | None


class ProductSearchResult(DomainModel):
    items: tuple[ProductSummary, ...]
    count: int
    truncated: bool


class ProductDetail(DomainModel):
    product: Product
    price: PriceRecord
    stock: StockLevel | None
    margin_pct: Decimal | None


class InventoryPosition(DomainModel):
    sku: str
    on_hand: int
    reserved: int
    available: int
    oversold: bool


class TaxonomyNode(DomainModel):
    category: Category
    children: tuple[TaxonomyNode, ...] = ()


class PriceStep(DomainModel):
    rule_id: str
    rule_name: str
    priority: int
    action: str
    price_before: Decimal
    price_after: Decimal
    stopped_further_rules: bool


class SkippedRule(DomainModel):
    rule_id: str
    rule_name: str
    reason: str


class PriceExplanation(DomainModel):
    sku: str
    on: date
    base_price: Decimal
    final_price: Decimal
    cost: Decimal
    final_margin_pct: Decimal | None
    applied: tuple[PriceStep, ...]
    skipped: tuple[SkippedRule, ...]


# --------------------------------------------------------------------------- set_price
class SetPriceRequest(DomainModel):
    skus: list[Sku] = Field(min_length=1, max_length=MAX_SKUS_PER_REQUEST)
    new_price: Decimal = Field(gt=0, le=100000, decimal_places=2)
    reason: Reason

    _unique = field_validator("skus")(_unique)


class PriceChangeLine(DomainModel):
    sku: str
    cost: Decimal
    old_price: Decimal
    new_price: Decimal
    old_margin_pct: Decimal | None
    new_margin_pct: Decimal | None


class PriceChangePlan(DomainModel):
    kind: Literal["price_change"] = "price_change"
    reason: str
    lines: tuple[PriceChangeLine, ...]

    @property
    def affected_count(self) -> int:
        return len(self.lines)


# --------------------------------------------------------------------------- attributes
class UpdateAttributesRequest(DomainModel):
    skus: list[Sku] = Field(min_length=1, max_length=MAX_SKUS_PER_REQUEST)
    set_values: dict[str, AttributeValue] = Field(min_length=1, max_length=20)
    reason: Reason

    _unique = field_validator("skus")(_unique)

    @field_validator("set_values")
    @classmethod
    def _key_format(cls, v: dict[str, AttributeValue]) -> dict[str, AttributeValue]:
        for key in v:
            if not key or len(key) > 40 or not key.replace("_", "").isalnum() or key != key.lower():
                raise ValueError(f"attribute name {key!r} must be lower_snake_case, max 40 chars")
        return v


class AttributeChangeLine(DomainModel):
    sku: str
    attribute: str
    old_value: AttributeValue | None
    new_value: AttributeValue


class AttributeChangePlan(DomainModel):
    kind: Literal["attribute_change"] = "attribute_change"
    reason: str
    lines: tuple[AttributeChangeLine, ...]
    # Full attribute dict per SKU after the change: apply() writes exactly these.
    after: dict[str, dict[str, AttributeValue]]

    @property
    def affected_count(self) -> int:
        return len(self.after)


# --------------------------------------------------------------------------- inventory
StockReason = Literal["cycle_count", "damaged", "received", "returned", "correction"]


class AdjustInventoryRequest(DomainModel):
    sku: Sku
    new_on_hand: int = Field(ge=0, le=1_000_000)
    reason_code: StockReason
    note: str = Field(default="", max_length=500)


class InventoryAdjustmentPlan(DomainModel):
    kind: Literal["inventory_adjustment"] = "inventory_adjustment"
    sku: str
    reason_code: StockReason
    note: str
    old_on_hand: int
    new_on_hand: int
    reserved: int
    new_available: int


# --------------------------------------------------------------------------- promotions
PromoCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_-]{3,20}$")]


class CreatePromotionRequest(DomainModel):
    code: PromoCode
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)]
    discount_pct: Decimal = Field(gt=0, le=90, decimal_places=2)
    category_ids: list[str] = Field(default_factory=list, max_length=50)
    skus: list[Sku] = Field(default_factory=list, max_length=MAX_SKUS_PER_REQUEST)
    starts: date
    ends: date
    exclusive: bool = False

    @model_validator(mode="after")
    def _valid(self) -> CreatePromotionRequest:
        if self.ends < self.starts:
            raise ValueError("ends must be on or after starts")
        if not self.category_ids and not self.skus:
            raise ValueError("give category_ids or skus (or both)")
        if (self.ends - self.starts).days > 92:
            raise ValueError("a promotion may run for at most 92 days")
        return self


class PromotionLine(DomainModel):
    sku: str
    price_before: Decimal
    price_after: Decimal
    cost: Decimal
    margin_after_pct: Decimal | None


class PromotionPlan(DomainModel):
    kind: Literal["promotion_create"] = "promotion_create"
    promotion: Promotion
    affected_count: int
    lowest_margin_after_pct: Decimal | None
    sample_lines: tuple[PromotionLine, ...]  # the 20 lowest-margin SKUs, for review


# --------------------------------------------------------------------------- orders
AdjustmentReason = Literal["goodwill", "damaged", "late_delivery", "price_match"]


class OrderAdjustmentRequest(DomainModel):
    order_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=40)]
    amount: Decimal = Field(gt=0, le=10000, decimal_places=2)
    reason_code: AdjustmentReason
    note: str = Field(default="", max_length=500)


class OrderAdjustmentPlan(DomainModel):
    kind: Literal["order_adjustment"] = "order_adjustment"
    order_id: str
    amount: Decimal
    reason_code: AdjustmentReason
    note: str
    total_paid: Decimal
    already_adjusted: Decimal
    refundable_after: Decimal
