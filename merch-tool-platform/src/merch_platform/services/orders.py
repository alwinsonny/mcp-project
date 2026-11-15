"""Order adjustments: refunds and goodwill credits. Money leaves the business here."""

from __future__ import annotations

from merch_platform.domain.commands import OrderAdjustmentPlan, OrderAdjustmentRequest
from merch_platform.domain.errors import AdjustmentExceedsPaid, OrderNotFound
from merch_platform.domain.models import money
from merch_platform.domain.ports import OrderRepository


class OrderService:
    def __init__(self, orders: OrderRepository) -> None:
        self._orders = orders

    async def plan_adjustment(self, request: OrderAdjustmentRequest) -> OrderAdjustmentPlan:
        order = await self._orders.get_order(request.order_id)
        if order is None:
            raise OrderNotFound(
                f"Order {request.order_id} not found.",
                hint="Check the order ID; order IDs look like ORD-000123.",
            )
        remaining = order.refundable_remaining
        if request.amount > remaining:
            raise AdjustmentExceedsPaid(
                f"£{request.amount} is more than the £{remaining} still refundable on "
                f"{order.order_id} (paid £{order.total_paid}, "
                f"already adjusted £{order.adjustments_total}).",
                hint=f"The most you can adjust on this order is £{remaining}.",
                details={"refundable_remaining": str(remaining)},
            )
        return OrderAdjustmentPlan(
            order_id=order.order_id,
            amount=request.amount,
            reason_code=request.reason_code,
            note=request.note,
            total_paid=order.total_paid,
            already_adjusted=order.adjustments_total,
            refundable_after=money(remaining - request.amount),
        )

    async def apply_adjustment(self, plan: OrderAdjustmentPlan) -> None:
        await self._orders.add_adjustment(plan.order_id, plan.amount, plan.reason_code, plan.note)
