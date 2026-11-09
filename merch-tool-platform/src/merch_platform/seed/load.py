"""Load SeedData into Postgres. Wipes the commerce tables first, so it is repeatable."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from merch_platform.db import tables as t
from merch_platform.seed.generator import SeedData

# Children before parents, so foreign keys never block the wipe.
WIPE_ORDER = [
    "order_adjustments",
    "order_lines",
    "orders",
    "promotions",
    "stock",
    "prices",
    "price_rules",
    "products",
    "categories",
    "change_sets",
    "idempotency_keys",
    "audit_log",
]


def _rows(items: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump() for item in items]


async def load_seed(engine: AsyncEngine, data: SeedData) -> dict[str, int]:
    rule_rows = [
        {
            "id": r.id,
            "name": r.name,
            "priority": r.priority,
            "stop_further": r.stop_further,
            "active_from": r.active_from,
            "active_to": r.active_to,
            "condition": r.condition.model_dump(mode="json"),
            "action_kind": r.action.kind,
            "action_amount": r.action.amount,
        }
        for r in data.rules
    ]
    promo_rows = []
    for p in data.promotions:
        row = p.model_dump()
        row["category_ids"], row["skus"] = list(p.category_ids), list(p.skus)
        promo_rows.append(row)
    order_rows = [
        {"order_id": o.order_id, "placed_on": o.placed_on, "adjustments_total": o.adjustments_total}
        for o in data.orders
    ]
    line_rows = [
        {"order_id": o.order_id, "line_no": i, **line.model_dump()}
        for o in data.orders
        for i, line in enumerate(o.lines, start=1)
    ]
    # Parents first: a category's parent must exist before it does.
    categories = sorted(data.categories, key=lambda c: (c.parent_id is not None, c.id))

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(WIPE_ORDER)} RESTART IDENTITY CASCADE"))
        await conn.execute(t.categories.insert(), _rows(categories))
        await conn.execute(t.products.insert(), _rows(data.products))
        await conn.execute(t.prices.insert(), _rows(data.prices))
        await conn.execute(t.stock.insert(), _rows(data.stock))
        await conn.execute(t.price_rules.insert(), rule_rows)
        await conn.execute(t.promotions.insert(), promo_rows)
        await conn.execute(t.orders.insert(), order_rows)
        await conn.execute(t.order_lines.insert(), line_rows)

    return {
        "categories": len(data.categories),
        "products": len(data.products),
        "prices": len(data.prices),
        "stock": len(data.stock),
        "price_rules": len(data.rules),
        "promotions": len(data.promotions),
        "orders": len(data.orders),
        "order_lines": len(line_rows),
    }
