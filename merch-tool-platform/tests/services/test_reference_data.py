"""Phase 4: the service methods behind resources and prompts.

Like every service test, these run twice: in memory and against Postgres.
Seed dates (today is 2026-09-23):
* TOWEL10   10% off Bath towels (C-110), 2026-09-28 to 2026-10-13, not exclusive
* SPA-EXCL  15% off Diffusers (C-340),   2026-10-23 to 2026-11-06, exclusive
* R-005 expired in July and R-006 starts in November, so neither is in force today.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from merch_platform.domain.errors import CategoryNotFound, InvalidDateRange
from merch_platform.services.container import Services
from tests.conftest import AS_OF

pytestmark = pytest.mark.anyio


async def test_rules_in_force_today_are_in_evaluation_order(services: Services) -> None:
    rules = await services.pricing.rules_in_force()
    ids = [r.id for r in rules]
    assert "R-005" not in ids  # expired
    assert "R-006" not in ids  # not started yet
    assert {"R-001", "R-002", "R-T01"} <= set(ids)
    assert [r.priority for r in rules] == sorted(r.priority for r in rules)
    assert all(r.active_on(AS_OF) for r in rules)


async def test_rules_in_force_on_a_future_date(services: Services) -> None:
    ids = [r.id for r in await services.pricing.rules_in_force(AS_OF + timedelta(days=52))]
    assert "R-006" in ids


async def test_calendar_lists_promotions_running_in_the_range(services: Services) -> None:
    october = await services.promotions.calendar(date(2026, 10, 1), date(2026, 10, 31))
    assert [p.code for p in october] == ["TOWEL10", "SPA-EXCL"]  # sorted by start date
    november = await services.promotions.calendar(date(2026, 11, 1), date(2026, 11, 30))
    assert [p.code for p in november] == ["SPA-EXCL"]
    assert await services.promotions.calendar(date(2027, 3, 1), date(2027, 3, 31)) == []


async def test_calendar_includes_promotions_ending_or_starting_on_the_edges(
    services: Services,
) -> None:
    ends_on_first_day = await services.promotions.calendar(date(2026, 10, 13), date(2026, 10, 14))
    assert [p.code for p in ends_on_first_day] == ["TOWEL10"]


async def test_calendar_rejects_a_backwards_range(services: Services) -> None:
    with pytest.raises(InvalidDateRange):
        await services.promotions.calendar(date(2026, 10, 31), date(2026, 10, 1))


async def test_overlaps_count_shared_skus(services: Services) -> None:
    report = await services.promotions.overlaps(
        ["C-340"], [], date(2026, 10, 1), date(2026, 10, 31)
    )
    shared = {p.code: n for p, n in report}
    assert shared["TOWEL10"] == 0  # same dates, different products
    assert shared["SPA-EXCL"] > 0  # same products and dates: the one that matters


async def test_unknown_category_lists_the_valid_ones(services: Services) -> None:
    with pytest.raises(CategoryNotFound) as caught:
        await services.promotions.overlaps(["C-999"], [], date(2026, 10, 1), date(2026, 10, 2))
    err = caught.value
    assert err.code == "CATEGORY_NOT_FOUND"
    assert "C-999" in err.message
    assert err.details["valid_category_ids"]["C-110"] == "Bath towels"


async def test_rule_and_promotion_text_is_identical_in_both_stores(services: Services) -> None:
    """Postgres pads NUMERIC values (20.00) where memory keeps 20. Shown text must not differ:
    this test runs against both stores, so either one drifting fails it."""
    actions = {r.id: r.action.describe() for r in await services.pricing.rules_in_force()}
    assert actions["R-T01"] == "20% off"
    assert actions["R-004"] == "set to £5.00"
    explained = await services.pricing.explain_price("TRAP-RULES-001")
    assert [step.action for step in explained.applied][:1] == ["20% off"]


def test_plain_number() -> None:
    from decimal import Decimal

    from merch_platform.domain.models import plain_number

    assert [plain_number(Decimal(v)) for v in ["10.00", "10", "12.50", "0.00", "-0", "100"]] == [
        "10",
        "10",
        "12.5",
        "0",
        "0",
        "100",
    ]
