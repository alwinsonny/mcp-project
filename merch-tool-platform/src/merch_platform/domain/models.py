"""Core domain models for the commerce catalogue.

Everything here is pure data plus small, side-effect-free helpers. Nothing in this
module knows about MCP, HTTP, SQL or AI. That independence is the point of Phase 1.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")

AttributeValue = str | int | float | bool
ProductStatus = Literal["active", "disabled"]


# --------------------------------------------------------------------------- money helpers
def money(value: Decimal) -> Decimal:
    """Round a monetary value to pennies using normal (half-up) rounding."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def margin_pct(price: Decimal, cost: Decimal) -> Decimal | None:
    """Gross margin as a percentage of price. None when the price is zero."""
    if price <= 0:
        return None
    return ((price - cost) / price * HUNDRED).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def min_price_for_margin(cost: Decimal, floor_pct: Decimal) -> Decimal:
    """Lowest price (rounded UP to the penny) that keeps margin at or above the floor.

    margin = (price - cost) / price  =>  price = cost / (1 - floor)
    Rounding up matters: rounding down could land a fraction of a penny below the floor.
    """
    if not Decimal("0") <= floor_pct < HUNDRED:
        raise ValueError("floor_pct must be in [0, 100)")
    return (cost / (1 - floor_pct / HUNDRED)).quantize(TWO_PLACES, rounding=ROUND_CEILING)


# Pydantic describes Decimal fields with regexes that start with a lookahead "(?!...)",
# for example ^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$ (and a variant for "2 decimal places").
# Some MCP clients, including the MCP Inspector, cannot evaluate lookaheads and reject
# the whole result. We publish one simple, portable pattern instead. Money is always sent
# as a string like "13.00" or "-18.75", so this pattern fits our data exactly.
# Note: pydantic 2.14+ publishes no Decimal pattern at all, so after upgrading pydantic
# this hook does nothing and the catalogue snapshot changes. That is why versions are
# pinned in requirements.txt: upgrade on purpose, then review `make snapshot`.
_PYDANTIC_DECIMAL_PREFIX = "^(?!^[-+.]*$)"
_PORTABLE_DECIMAL_PATTERN = r"^-?\d+(\.\d+)?$"


def _portable_json_schema(schema: dict[str, Any]) -> None:
    """Rewrite Decimal patterns anywhere in a model's JSON schema (runs on every model)."""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if isinstance(pattern, str) and pattern.startswith(_PYDANTIC_DECIMAL_PREFIX):
                node["pattern"] = _PORTABLE_DECIMAL_PATTERN
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def plain_number(value: Decimal) -> str:
    """Decimal("10.00") and Decimal("10") both -> "10"; Decimal("12.50") -> "12.5".

    Postgres returns NUMERIC values padded to their column's scale (10.00) while the
    in-memory store keeps what was typed (10). Text shown to people or models goes
    through here (or :.2f for money) so both stores produce exactly the same words.
    """
    text = f"{value.normalize():f}"
    return "0" if text == "-0" else text


class DomainModel(BaseModel):
    """Base for all domain models: immutable, strict about unknown fields, and with a
    JSON schema every MCP client can evaluate."""

    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_extra=_portable_json_schema)


# --------------------------------------------------------------------------- catalogue
class Category(DomainModel):
    id: str
    name: str
    parent_id: str | None = None


class Product(DomainModel):
    sku: str
    name: str
    category_id: str
    status: ProductStatus = "active"
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)


class PriceRecord(DomainModel):
    sku: str
    base_price: Decimal = Field(ge=0, decimal_places=2)
    cost: Decimal = Field(ge=0, decimal_places=2)
    currency: Literal["GBP"] = "GBP"

    @property
    def margin_pct(self) -> Decimal | None:
        return margin_pct(self.base_price, self.cost)


# --------------------------------------------------------------------------- price rules
class RuleCondition(DomainModel):
    """Which products a rule applies to. Empty lists mean 'no restriction on this field'."""

    category_ids: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    min_base_price: Decimal | None = None

    def check(self, product: Product, price: PriceRecord) -> tuple[bool, str]:
        """Return (matches, reason). The reason is shown by explain_price."""
        if self.skus and product.sku not in self.skus:
            return False, "SKU not in rule's SKU list"
        if self.category_ids and product.category_id not in self.category_ids:
            return False, f"category {product.category_id} not targeted"
        if self.min_base_price is not None and price.base_price < self.min_base_price:
            return False, f"base price below {self.min_base_price}"
        return True, "matched"


class RuleAction(DomainModel):
    kind: Literal["percent_off", "fixed_off", "set_price"]
    amount: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def _percent_in_range(self) -> RuleAction:
        if self.kind == "percent_off" and self.amount > HUNDRED:
            raise ValueError("percent_off amount cannot exceed 100")
        return self

    def apply(self, current: Decimal) -> Decimal:
        match self.kind:
            case "percent_off":
                result = current * (1 - self.amount / HUNDRED)
            case "fixed_off":
                result = current - self.amount
            case "set_price":
                result = self.amount
        return money(max(result, Decimal("0")))

    def describe(self) -> str:
        match self.kind:
            case "percent_off":
                return f"{plain_number(self.amount)}% off"
            case "fixed_off":
                return f"£{self.amount:.2f} off"
            case "set_price":
                return f"set to £{self.amount:.2f}"


class PriceRule(DomainModel):
    """A catalogue price rule, modelled on Magento catalogue price rules.

    Rules run in ascending priority order (lower number first). A rule with
    stop_further=True prevents every later rule from applying.
    """

    id: str
    name: str
    priority: int = Field(ge=0)
    stop_further: bool = False
    active_from: date
    active_to: date | None = None
    condition: RuleCondition = Field(default_factory=RuleCondition)
    action: RuleAction

    def active_on(self, day: date) -> bool:
        return self.active_from <= day and (self.active_to is None or day <= self.active_to)


# --------------------------------------------------------------------------- stock
class StockLevel(DomainModel):
    sku: str
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)

    @property
    def available(self) -> int:
        return max(self.on_hand - self.reserved, 0)

    @property
    def oversold(self) -> bool:
        """More units promised to open orders than are physically on the shelf."""
        return self.reserved > self.on_hand


# --------------------------------------------------------------------------- promotions
class Promotion(DomainModel):
    id: str
    code: str
    name: str
    discount_pct: Decimal = Field(gt=0, le=90)
    category_ids: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    starts: date
    ends: date
    exclusive: bool = False

    @model_validator(mode="after")
    def _valid(self) -> Promotion:
        if self.ends < self.starts:
            raise ValueError("ends must be on or after starts")
        if not self.category_ids and not self.skus:
            raise ValueError("a promotion needs category_ids or skus")
        return self

    def overlaps_dates(self, starts: date, ends: date) -> bool:
        return self.starts <= ends and starts <= self.ends


# --------------------------------------------------------------------------- orders
class OrderLine(DomainModel):
    sku: str
    qty: int = Field(ge=1)
    unit_price_paid: Decimal = Field(ge=0, decimal_places=2)


class Order(DomainModel):
    order_id: str
    placed_on: date
    lines: tuple[OrderLine, ...]
    adjustments_total: Decimal = Decimal("0.00")

    @property
    def total_paid(self) -> Decimal:
        return money(sum((line.unit_price_paid * line.qty for line in self.lines), Decimal("0")))

    @property
    def refundable_remaining(self) -> Decimal:
        return money(self.total_paid - self.adjustments_total)
