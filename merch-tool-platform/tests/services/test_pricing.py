from datetime import date
from decimal import Decimal

import pytest

from merch_platform.domain.commands import SetPriceRequest, WriteLimits
from merch_platform.domain.errors import (
    BlastRadiusExceeded,
    MarginFloorBreached,
    NoChange,
    SkuInactive,
    SkuNotFound,
)
from merch_platform.seed.generator import SeedData
from merch_platform.services.container import Services
from tests.services.helpers import active_skus

pytestmark = pytest.mark.anyio
D = Decimal


async def test_explain_trap_mug(services: Services) -> None:
    e = await services.pricing.explain_price("TRAP-RULES-001")
    assert e.final_price == D("13.00")
    assert [s.rule_id for s in e.applied] == ["R-T01", "R-T02"]
    reasons = {s.rule_id: s.reason for s in e.skipped}
    assert "stopped by R-T02" in reasons["R-T03"] and "stopped by R-T02" in reasons["R-007"]


async def test_explain_on_future_date_uses_future_rules(
    services: Services, seed_data: SeedData
) -> None:
    towel = active_skus(seed_data, "C-110", 1)[0]
    today = await services.pricing.explain_price(towel)
    black_friday = await services.pricing.explain_price(towel, on=date(2026, 11, 14))
    assert [s.rule_id for s in today.applied] == ["R-001"]
    assert [s.rule_id for s in black_friday.applied] == ["R-006", "R-001"]


async def test_thin_margin_breach_gives_exact_minimum(
    services: Services, merchandiser: WriteLimits
) -> None:
    request = SetPriceRequest(
        skus=["TRAP-THINMARGIN-001"], new_price=D("49.50"), reason="match competitor"
    )
    with pytest.raises(MarginFloorBreached) as err:
        await services.pricing.plan_price_change(request, merchandiser)
    assert err.value.details["min_allowed_price"] == {"TRAP-THINMARGIN-001": "49.73"}
    assert "£49.73" in err.value.hint

    ok = SetPriceRequest(
        skus=["TRAP-THINMARGIN-001"], new_price=D("49.73"), reason="match competitor"
    )
    plan = await services.pricing.plan_price_change(ok, merchandiser)
    assert plan.lines[0].new_margin_pct == D("10.01")


async def test_plan_changes_nothing_until_applied(
    services: Services, seed_data: SeedData, merchandiser: WriteLimits
) -> None:
    skus = active_skus(seed_data, "C-120", 3)  # bathrobes: cost well under £200
    plan = await services.pricing.plan_price_change(
        SetPriceRequest(skus=skus, new_price=D("199.00"), reason="premium repositioning"),
        merchandiser,
    )
    assert plan.affected_count == 3
    assert (await services.catalogue.get_product(skus[0])).price.base_price != D("199.00")
    await services.pricing.apply_price_change(plan)
    for sku in skus:
        assert (await services.catalogue.get_product(sku)).price.base_price == D("199.00")


async def test_price_guards(services: Services, seed_data: SeedData) -> None:
    limits = WriteLimits(max_skus=2)
    with pytest.raises(BlastRadiusExceeded):
        await services.pricing.plan_price_change(
            SetPriceRequest(
                skus=active_skus(seed_data, "C-120", 3), new_price=D("150"), reason="reprice robes"
            ),
            limits,
        )
    with pytest.raises(SkuNotFound) as err:
        await services.pricing.plan_price_change(
            SetPriceRequest(skus=["NOPE-001"], new_price=D("10"), reason="reprice robes"), limits
        )
    assert err.value.details["missing"] == ["NOPE-001"]
    with pytest.raises(SkuInactive):
        await services.pricing.plan_price_change(
            SetPriceRequest(skus=["TRAP-DISABLED-001"], new_price=D("30"), reason="reprice pillow"),
            limits,
        )
    with pytest.raises(NoChange):
        await services.pricing.plan_price_change(
            SetPriceRequest(skus=["TRAP-RULES-001"], new_price=D("20.00"), reason="same price"),
            limits,
        )
