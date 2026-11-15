from datetime import date, timedelta
from decimal import Decimal

import pytest

from merch_platform.domain.commands import CreatePromotionRequest, WriteLimits
from merch_platform.domain.errors import (
    BlastRadiusExceeded,
    CategoryNotFound,
    InvalidDateRange,
    PromotionBelowCost,
    PromotionConflict,
)
from merch_platform.seed.generator import SeedData
from merch_platform.services.container import Services
from tests.conftest import AS_OF
from tests.services.helpers import active_skus

pytestmark = pytest.mark.anyio
D = Decimal


def promo(**overrides: object) -> CreatePromotionRequest:
    base: dict[str, object] = dict(
        code="TEST10",
        name="Test promo",
        discount_pct=D("10"),
        starts=AS_OF + timedelta(days=60),
        ends=AS_OF + timedelta(days=70),
    )
    return CreatePromotionRequest(**{**base, **overrides})


async def test_plan_and_apply(services: Services, seed_data: SeedData, senior: WriteLimits) -> None:
    skus = active_skus(seed_data, "C-620", 3)  # teapots: healthy margins
    plan = await services.promotions.plan_promotion(promo(skus=skus), senior)
    assert plan.affected_count == 3 and plan.promotion.id == "PROMO-TEST10"
    assert plan.sample_lines == tuple(
        sorted(plan.sample_lines, key=lambda line: line.margin_after_pct or 0)
    )
    await services.promotions.apply_promotion(plan)
    with pytest.raises(PromotionConflict, match="already in use"):
        await services.promotions.plan_promotion(promo(skus=skus), senior)


async def test_cannot_start_in_the_past(services: Services, senior: WriteLimits) -> None:
    with pytest.raises(InvalidDateRange):
        await services.promotions.plan_promotion(
            promo(
                category_ids=["C-620"],
                starts=AS_OF - timedelta(days=1),
                ends=AS_OF + timedelta(days=5),
            ),
            senior,
        )


async def test_overlap_with_exclusive_promotion_is_refused(
    services: Services, senior: WriteLimits
) -> None:
    # SPA-EXCL is exclusive on diffusers (C-340), running AS_OF+30 .. AS_OF+44
    with pytest.raises(PromotionConflict, match="SPA-EXCL") as err:
        await services.promotions.plan_promotion(
            promo(
                category_ids=["C-340"],
                starts=AS_OF + timedelta(days=40),
                ends=AS_OF + timedelta(days=50),
            ),
            senior,
        )
    assert "SPA-EXCL" in err.value.details["conflicts"]
    # Same scope, non-overlapping dates: fine.
    plan = await services.promotions.plan_promotion(
        promo(
            category_ids=["C-340"],
            starts=AS_OF + timedelta(days=45),
            ends=AS_OF + timedelta(days=50),
        ),
        senior,
    )
    assert plan.affected_count > 0


async def test_new_exclusive_promotion_cannot_overlap_non_exclusive(
    services: Services, senior: WriteLimits
) -> None:
    # TOWEL10 (non-exclusive) covers C-110 on AS_OF+5 .. AS_OF+20
    with pytest.raises(PromotionConflict, match="TOWEL10"):
        await services.promotions.plan_promotion(
            promo(
                category_ids=["C-110"],
                exclusive=True,
                starts=AS_OF + timedelta(days=10),
                ends=AS_OF + timedelta(days=12),
            ),
            senior,
        )


async def test_below_cost_after_rules_and_discount(services: Services, senior: WriteLimits) -> None:
    # TRAP-RULES-001 sells at £13.00 after rules; cost £8.00. Max safe discount = 38.46%.
    with pytest.raises(PromotionBelowCost) as err:
        await services.promotions.plan_promotion(
            promo(skus=["TRAP-RULES-001"], discount_pct=D("40")), senior
        )
    assert err.value.details["max_safe_discount_pct"] == "38.46"
    plan = await services.promotions.plan_promotion(
        promo(skus=["TRAP-RULES-001"], discount_pct=D("38.46")), senior
    )
    assert plan.sample_lines[0].price_after >= D("8.00")


async def test_blast_radius_and_unknown_category(services: Services) -> None:
    with pytest.raises(BlastRadiusExceeded):
        await services.promotions.plan_promotion(
            promo(category_ids=["C-100"]), WriteLimits(max_skus=5)
        )
    with pytest.raises(CategoryNotFound):
        await services.promotions.plan_promotion(
            promo(category_ids=["C-999"]), WriteLimits(max_skus=5)
        )


async def test_list_overlapping_is_inclusive_of_edges(services: Services, store: object) -> None:
    edge = AS_OF + timedelta(days=44)  # SPA-EXCL's last day
    found = await store.list_overlapping(edge, edge)  # type: ignore[attr-defined]
    assert "SPA-EXCL" in [p.code for p in found]
    assert date(2000, 1, 1) < edge


async def test_negative_margin_sku_blocks_whole_category_promotion(services: Services) -> None:
    # TRAP-NEGMARGIN-001 already sells below cost, so ANY discount on towels is refused
    # and the error names it, telling the caller to exclude it.
    with pytest.raises(PromotionBelowCost) as err:
        await services.promotions.plan_promotion(
            promo(
                category_ids=["C-110"],
                discount_pct=D("5"),
                starts=AS_OF + timedelta(days=25),
                ends=AS_OF + timedelta(days=28),
            ),
            WriteLimits(max_skus=500),
        )
    assert "TRAP-NEGMARGIN-001" in err.value.details["skus"]
    assert err.value.details["max_safe_discount_pct"] == "0"
