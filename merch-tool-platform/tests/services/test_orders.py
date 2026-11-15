from decimal import Decimal

import pytest

from merch_platform.domain.commands import OrderAdjustmentRequest
from merch_platform.domain.errors import AdjustmentExceedsPaid, OrderNotFound
from merch_platform.services.container import Services

pytestmark = pytest.mark.anyio
D = Decimal


async def test_cannot_refund_more_than_remaining(services: Services) -> None:
    # ORD-TRAP-001: paid £30.00, already adjusted £25.00
    with pytest.raises(AdjustmentExceedsPaid) as err:
        await services.orders.plan_adjustment(
            OrderAdjustmentRequest(
                order_id="ORD-TRAP-001", amount=D("5.01"), reason_code="goodwill"
            )
        )
    assert "£5.00" in err.value.hint


async def test_adjustments_accumulate(services: Services) -> None:
    for amount in ("3.00", "2.00"):
        plan = await services.orders.plan_adjustment(
            OrderAdjustmentRequest(
                order_id="ORD-TRAP-001", amount=D(amount), reason_code="late_delivery"
            )
        )
        await services.orders.apply_adjustment(plan)
    with pytest.raises(AdjustmentExceedsPaid):
        await services.orders.plan_adjustment(
            OrderAdjustmentRequest(
                order_id="ORD-TRAP-001", amount=D("0.01"), reason_code="goodwill"
            )
        )


async def test_unknown_order(services: Services) -> None:
    with pytest.raises(OrderNotFound):
        await services.orders.plan_adjustment(
            OrderAdjustmentRequest(order_id="ORD-999999", amount=D("1"), reason_code="goodwill")
        )
