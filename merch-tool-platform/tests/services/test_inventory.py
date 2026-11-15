import pytest

from merch_platform.domain.commands import AdjustInventoryRequest
from merch_platform.domain.errors import NoChange, SkuNotFound, StockBelowReserved
from merch_platform.services.container import Services

pytestmark = pytest.mark.anyio


async def test_positions_flag_oversold(services: Services) -> None:
    [pos] = await services.inventory.get_positions(["TRAP-OVERSOLD-001"])
    assert (pos.on_hand, pos.reserved, pos.available, pos.oversold) == (3, 12, 0, True)
    with pytest.raises(SkuNotFound):
        await services.inventory.get_positions(["NOPE-001"])


async def test_cannot_lower_stock_below_reservations(services: Services) -> None:
    # TRAP-RULES-001: 40 on hand, 2 reserved
    with pytest.raises(StockBelowReserved) as err:
        await services.inventory.plan_adjustment(
            AdjustInventoryRequest(sku="TRAP-RULES-001", new_on_hand=1, reason_code="damaged")
        )
    assert "at least 2" in err.value.hint


async def test_raising_oversold_stock_is_allowed_even_if_still_short(services: Services) -> None:
    plan = await services.inventory.plan_adjustment(
        AdjustInventoryRequest(sku="TRAP-OVERSOLD-001", new_on_hand=8, reason_code="received")
    )
    assert plan.new_available == 0
    await services.inventory.apply_adjustment(plan)
    [pos] = await services.inventory.get_positions(["TRAP-OVERSOLD-001"])
    assert pos.on_hand == 8 and pos.reserved == 12


async def test_no_change(services: Services) -> None:
    with pytest.raises(NoChange):
        await services.inventory.plan_adjustment(
            AdjustInventoryRequest(sku="TRAP-RULES-001", new_on_hand=40, reason_code="cycle_count")
        )
