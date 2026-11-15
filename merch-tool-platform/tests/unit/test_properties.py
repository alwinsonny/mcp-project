"""Property-based tests: Hypothesis generates hundreds of random requests.

The property: whatever price and SKU set is requested, the planner either refuses
with MarginFloorBreached, or EVERY line it returns is at or above the floor. There is
no input that sneaks a below-floor price into a plan.
"""

import asyncio
from datetime import date
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from merch_platform.adapters.memory import InMemoryStore
from merch_platform.domain.commands import SetPriceRequest, WriteLimits
from merch_platform.domain.errors import MarginFloorBreached, NoChange
from merch_platform.domain.models import min_price_for_margin
from merch_platform.seed.generator import generate
from merch_platform.services.checks import FixedClock
from merch_platform.services.container import build_services

SEED = generate(as_of=date(2026, 9, 23), n_products=200)
ACTIVE = sorted(p.sku for p in SEED.products if p.status == "active")
COST = {p.sku: p.cost for p in SEED.prices}

prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("300"), places=2)
floors = st.sampled_from([Decimal("0"), Decimal("10"), Decimal("25"), Decimal("50")])
sku_sets = st.lists(st.sampled_from(ACTIVE), min_size=1, max_size=20, unique=True)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(new_price=prices, floor=floors, skus=sku_sets)
def test_no_plan_ever_breaches_the_floor(
    new_price: Decimal, floor: Decimal, skus: list[str]
) -> None:
    services = build_services(InMemoryStore.from_seed(SEED), FixedClock(date(2026, 9, 23)))
    request = SetPriceRequest(skus=skus, new_price=new_price, reason="property test")
    limits = WriteLimits(max_skus=50, margin_floor_pct=floor)
    try:
        plan = asyncio.run(services.pricing.plan_price_change(request, limits))
    except MarginFloorBreached as err:
        # Refusal must be justified: at least one SKU really is below its minimum.
        assert any(new_price < min_price_for_margin(COST[s], floor) for s in skus)
        assert set(err.details["min_allowed_price"]) <= set(skus)
        return
    except NoChange:
        return
    for line in plan.lines:
        assert line.new_price >= min_price_for_margin(line.cost, floor)
