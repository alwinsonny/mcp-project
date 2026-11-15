"""Postgres adapter: implements every repository port with async SQLAlchemy Core.

Rules followed throughout:
* Parameterised SQL only (never string formatting), so arguments cannot inject SQL.
* ORDER BY ... COLLATE "C" so ordering matches Python's sorted() byte for byte,
  which keeps this adapter's results identical to the in-memory one.
* Every write runs inside engine.begin(), so it commits or rolls back as one unit.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, bindparam, func, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from merch_platform.db import tables as t
from merch_platform.domain.commands import AdjustmentReason, ProductQuery, ProductSummary
from merch_platform.domain.models import (
    AttributeValue,
    Category,
    Order,
    OrderLine,
    PriceRecord,
    PriceRule,
    Product,
    Promotion,
    RuleAction,
    RuleCondition,
    StockLevel,
    margin_pct,
)


class PostgresStore:
    """One object that satisfies all five repository Protocols."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ catalogue
    async def get_products(self, skus: Collection[str]) -> dict[str, Product]:
        if not skus:
            return {}
        stmt = select(t.products).where(t.products.c.sku.in_(list(skus)))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return {r["sku"]: Product.model_validate(dict(r)) for r in rows}

    async def search_products(
        self, query: ProductQuery, category_ids: Collection[str] | None
    ) -> list[ProductSummary]:
        p, pr = t.products, t.prices
        margin = func.round(
            (pr.c.base_price - pr.c.cost) / func.nullif(pr.c.base_price, 0) * 100, 2
        )
        haystack = func.lower(p.c.name + " " + p.c.sku)

        conditions: list[Any] = [haystack.contains(term, autoescape=True) for term in query.terms]
        if query.status != "any":
            conditions.append(p.c.status == query.status)
        if category_ids is not None:
            conditions.append(p.c.category_id.in_(list(category_ids)))
        if query.min_margin_pct is not None:
            conditions.append(margin >= query.min_margin_pct)
        if query.max_margin_pct is not None:
            conditions.append(margin <= query.max_margin_pct)

        stmt = (
            select(p.c.sku, p.c.name, p.c.category_id, p.c.status, pr.c.base_price, pr.c.cost)
            .join(pr, pr.c.sku == p.c.sku)
            .where(and_(true(), *conditions))
            .order_by(p.c.sku.collate("C"))
            .limit(query.limit + 1)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            ProductSummary(
                sku=r["sku"],
                name=r["name"],
                category_id=r["category_id"],
                status=r["status"],
                base_price=r["base_price"],
                cost=r["cost"],
                margin_pct=margin_pct(r["base_price"], r["cost"]),
            )
            for r in rows
        ]

    async def list_categories(self) -> list[Category]:
        stmt = select(t.categories).order_by(t.categories.c.id.collate("C"))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [Category.model_validate(dict(r)) for r in rows]

    async def skus_in_categories(self, category_ids: Collection[str]) -> set[str]:
        if not category_ids:
            return set()
        stmt = select(t.products.c.sku).where(
            t.products.c.category_id.in_(list(category_ids)), t.products.c.status == "active"
        )
        async with self._engine.connect() as conn:
            return set((await conn.execute(stmt)).scalars().all())

    async def save_attributes(self, updates: Mapping[str, Mapping[str, AttributeValue]]) -> None:
        if not updates:
            return
        stmt = (
            update(t.products)
            .where(t.products.c.sku == bindparam("b_sku"))
            .values(attributes=bindparam("b_attributes"))
        )
        params = [{"b_sku": sku, "b_attributes": dict(attrs)} for sku, attrs in updates.items()]
        async with self._engine.begin() as conn:
            await conn.execute(stmt, params)

    # ------------------------------------------------------------------ pricing
    async def get_prices(self, skus: Collection[str]) -> dict[str, PriceRecord]:
        if not skus:
            return {}
        stmt = select(t.prices).where(t.prices.c.sku.in_(list(skus)))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return {r["sku"]: PriceRecord.model_validate(dict(r)) for r in rows}

    async def list_rules(self) -> list[PriceRule]:
        stmt = select(t.price_rules).order_by(
            t.price_rules.c.priority, t.price_rules.c.id.collate("C")
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            PriceRule(
                id=r["id"],
                name=r["name"],
                priority=r["priority"],
                stop_further=r["stop_further"],
                active_from=r["active_from"],
                active_to=r["active_to"],
                condition=RuleCondition.model_validate(r["condition"]),
                action=RuleAction(kind=r["action_kind"], amount=r["action_amount"]),
            )
            for r in rows
        ]

    async def save_base_prices(self, prices: Mapping[str, Decimal]) -> None:
        if not prices:
            return
        stmt = (
            update(t.prices)
            .where(t.prices.c.sku == bindparam("b_sku"))
            .values(base_price=bindparam("b_price"))
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt, [{"b_sku": s, "b_price": p} for s, p in prices.items()])

    # ------------------------------------------------------------------ inventory
    async def get_stock(self, skus: Collection[str]) -> dict[str, StockLevel]:
        if not skus:
            return {}
        stmt = select(t.stock).where(t.stock.c.sku.in_(list(skus)))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return {r["sku"]: StockLevel.model_validate(dict(r)) for r in rows}

    async def save_on_hand(self, sku: str, on_hand: int) -> None:
        stmt = pg_insert(t.stock).values(sku=sku, on_hand=on_hand, reserved=0)
        stmt = stmt.on_conflict_do_update(index_elements=[t.stock.c.sku], set_={"on_hand": on_hand})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    # ------------------------------------------------------------------ promotions
    @staticmethod
    def _promotion(row: RowMapping) -> Promotion:
        data = dict(row)
        data["category_ids"] = tuple(data["category_ids"])
        data["skus"] = tuple(data["skus"])
        return Promotion.model_validate(data)

    async def list_overlapping(self, starts: date, ends: date) -> list[Promotion]:
        stmt = (
            select(t.promotions)
            .where(t.promotions.c.starts <= ends, t.promotions.c.ends >= starts)
            .order_by(t.promotions.c.id.collate("C"))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [self._promotion(r) for r in rows]

    async def code_exists(self, code: str) -> bool:
        stmt = select(func.count()).select_from(t.promotions).where(t.promotions.c.code == code)
        async with self._engine.connect() as conn:
            return bool((await conn.execute(stmt)).scalar_one())

    async def add_promotion(self, promotion: Promotion) -> None:
        values = promotion.model_dump()
        values["category_ids"] = list(promotion.category_ids)
        values["skus"] = list(promotion.skus)
        async with self._engine.begin() as conn:
            await conn.execute(t.promotions.insert().values(**values))

    # ------------------------------------------------------------------ orders
    async def get_order(self, order_id: str) -> Order | None:
        async with self._engine.connect() as conn:
            head = (
                (await conn.execute(select(t.orders).where(t.orders.c.order_id == order_id)))
                .mappings()
                .first()
            )
            if head is None:
                return None
            lines = (
                (
                    await conn.execute(
                        select(t.order_lines)
                        .where(t.order_lines.c.order_id == order_id)
                        .order_by(t.order_lines.c.line_no)
                    )
                )
                .mappings()
                .all()
            )
        return Order(
            order_id=head["order_id"],
            placed_on=head["placed_on"],
            adjustments_total=head["adjustments_total"],
            lines=tuple(
                OrderLine(sku=r["sku"], qty=r["qty"], unit_price_paid=r["unit_price_paid"])
                for r in lines
            ),
        )

    async def add_adjustment(
        self, order_id: str, amount: Decimal, reason_code: AdjustmentReason, note: str
    ) -> None:
        async with self._engine.begin() as conn:  # both statements commit together, or neither
            await conn.execute(
                t.order_adjustments.insert().values(
                    order_id=order_id, amount=amount, reason_code=reason_code, note=note
                )
            )
            await conn.execute(
                update(t.orders)
                .where(t.orders.c.order_id == order_id)
                .values(adjustments_total=t.orders.c.adjustments_total + amount)
            )
