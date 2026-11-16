"""Phase 4: RESOURCES, read-only reference data the host app can load into the model's context.

Tools vs resources (who decides to use them):
* A TOOL is chosen by the MODEL during a conversation ("let me search for towels").
* A RESOURCE is chosen by the APPLICATION or the USER before or during a conversation
  ("attach the category tree"). It has an address (a URI), takes no actions, and is
  cheaper than a tool: loaded once, it saves the model calling a tool again and again.

Rule of thumb: read-only data an app could load up front is a resource; anything the
model must decide to do is a tool.

Three addresses:
* catalog://taxonomy                every category ID and name, as a tree
* pricing://rules/active            the price rules in force today, in evaluation order
* promotions://calendar/{month}     a TEMPLATE: promotions running in a month (YYYY-MM)

SDK note: static resources (no {placeholders} in the URI) cannot receive the Context
parameter, so all resources reach the services through `get_services`, a function the
server fills in at start-up. Template resources could take Context; using one approach
for all three keeps the code simple.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from collections.abc import Callable
from datetime import date
from typing import Any

from mcp.server import MCPServer

from merch_platform.domain.commands import TaxonomyNode
from merch_platform.domain.errors import DomainError
from merch_platform.domain.models import PriceRule, Promotion, plain_number
from merch_platform.server.results import protocol_error
from merch_platform.services.container import Services

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
RESOURCE_META = {"contract_version": "1", "risk_class": "read"}


def compact(data: Any) -> str:
    """JSON with no wasted spaces: resource text is read by the model, so size matters."""
    return json.dumps(data, separators=(",", ":"), default=str)


# ---------------------------------------------------------------- data shapes
def taxonomy_json(nodes: list[TaxonomyNode]) -> dict[str, Any]:
    def node(n: TaxonomyNode) -> dict[str, Any]:
        out: dict[str, Any] = {"id": n.category.id, "name": n.category.name}
        if n.children:
            out["children"] = [node(child) for child in n.children]
        return out

    return {
        "note": "Pass any ID as search_products category_id; sub-categories are included.",
        "categories": [node(n) for n in nodes],
    }


def rule_json(rule: PriceRule) -> dict[str, Any]:
    applies: dict[str, Any] = {}
    if rule.condition.category_ids:
        applies["category_ids"] = list(rule.condition.category_ids)
    if rule.condition.skus:
        applies["skus"] = list(rule.condition.skus)
    if rule.condition.min_base_price is not None:
        applies["min_base_price"] = f"{rule.condition.min_base_price:.2f}"
    return {
        "id": rule.id,
        "name": rule.name,
        "priority": rule.priority,
        "action": rule.action.describe(),
        "applies_to": applies or "all products",
        "stop_further_rules": rule.stop_further,
        "active_to": rule.active_to.isoformat() if rule.active_to else None,
    }


def promotion_json(p: Promotion) -> dict[str, Any]:
    return {
        "code": p.code,
        "name": p.name,
        "discount_pct": plain_number(p.discount_pct),
        "category_ids": list(p.category_ids),
        "skus": list(p.skus),
        "starts": p.starts.isoformat(),
        "ends": p.ends.isoformat(),
        "exclusive": p.exclusive,
    }


def parse_month(month: str) -> tuple[date, date]:
    """'2026-12' -> (2026-12-01, 2026-12-31), or a clear INVALID_MONTH error."""
    if not MONTH_PATTERN.fullmatch(month):
        raise protocol_error(
            "INVALID_MONTH",
            f"{month!r} is not a month.",
            "Use YYYY-MM, for example promotions://calendar/2026-12.",
        )
    year, number = int(month[:4]), int(month[5:])
    return date(year, number, 1), date(year, number, monthrange(year, number)[1])


# ---------------------------------------------------------------- registration
def register_resources(mcp: MCPServer, get_services: Callable[[], Services]) -> None:
    @mcp.resource(
        "catalog://taxonomy",
        name="taxonomy",
        title="Category tree",
        description="Every product category with its ID, name and sub-categories. "
        "Use these IDs as search_products' category_id.",
        mime_type="application/json",
        meta=RESOURCE_META,
    )
    async def taxonomy() -> str:
        return compact(taxonomy_json(await get_services().catalogue.taxonomy()))

    @mcp.resource(
        "pricing://rules/active",
        name="active_price_rules",
        title="Price rules in force today",
        description="The catalogue price rules active today, in the order they are applied "
        "(lowest priority number first), with what each one does and which products it targets.",
        mime_type="application/json",
        meta=RESOURCE_META,
    )
    async def active_price_rules() -> str:
        services = get_services()
        rules = await services.pricing.rules_in_force()
        return compact(
            {
                "on": services.clock.today().isoformat(),
                "note": "Rules apply in this order; a rule with stop_further_rules ends "
                "evaluation. explain_price shows the result for one SKU.",
                "rules": [rule_json(r) for r in rules],
            }
        )

    @mcp.resource(
        "promotions://calendar/{month}",
        name="promotion_calendar",
        title="Promotions in a month",
        description="Customer-visible promotions running at any point in a month "
        "(month as YYYY-MM, e.g. 2026-12): code, discount %, what they cover, dates, and "
        "whether they are exclusive.",
        mime_type="application/json",
        meta=RESOURCE_META,
    )
    async def promotion_calendar(month: str) -> str:
        first, last = parse_month(month)
        try:
            found = await get_services().promotions.calendar(first, last)
        except DomainError as err:  # pragma: no cover - parse_month already guards dates
            raise protocol_error(err.code, err.message, err.hint) from err
        return compact({"month": month, "promotions": [promotion_json(p) for p in found]})
