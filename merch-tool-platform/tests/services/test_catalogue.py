from decimal import Decimal

import pytest

from merch_platform.domain.commands import ProductQuery, UpdateAttributesRequest, WriteLimits
from merch_platform.domain.errors import (
    BlastRadiusExceeded,
    CategoryNotFound,
    NoChange,
    ProtectedAttribute,
    SkuInactive,
    SkuNotFound,
)
from merch_platform.seed.generator import SeedData
from merch_platform.services.container import Services
from tests.services.helpers import active_skus

pytestmark = pytest.mark.anyio


async def test_search_requires_every_term(services: Services) -> None:
    result = await services.catalogue.search(ProductQuery(query="trap mug"))
    assert [i.sku for i in result.items] == ["TRAP-RULES-001"]


async def test_search_category_includes_descendants(
    services: Services, seed_data: SeedData
) -> None:
    result = await services.catalogue.search(ProductQuery(category_id="C-100", limit=50))
    bath_leaves = {"C-110", "C-120", "C-130", "C-140"}
    assert result.items and {i.category_id for i in result.items} <= bath_leaves


async def test_search_margin_filter_finds_negative_margin(services: Services) -> None:
    result = await services.catalogue.search(ProductQuery(max_margin_pct=Decimal("0")))
    assert "TRAP-NEGMARGIN-001" in [i.sku for i in result.items]
    assert all(i.margin_pct is not None and i.margin_pct <= 0 for i in result.items)


async def test_search_hides_disabled_by_default(services: Services) -> None:
    default = await services.catalogue.search(ProductQuery(query="trap pillow"))
    anything = await services.catalogue.search(ProductQuery(query="trap pillow", status="any"))
    assert default.count == 0 and [i.sku for i in anything.items] == ["TRAP-DISABLED-001"]


async def test_search_reports_truncation_and_sorts_by_sku(services: Services) -> None:
    result = await services.catalogue.search(ProductQuery(limit=5))
    assert result.count == 5 and result.truncated
    assert [i.sku for i in result.items] == sorted(i.sku for i in result.items)


async def test_search_escapes_like_wildcards(services: Services) -> None:
    # "%" must match a literal percent sign (only the trap throw's name has one),
    # not act as a SQL wildcard that matches every product.
    literal = await services.catalogue.search(ProductQuery(query="%", status="any"))
    assert [i.sku for i in literal.items] == ["TRAP-THINMARGIN-001"]
    assert (await services.catalogue.search(ProductQuery(query="_", status="any"))).count == 0


async def test_unknown_category(services: Services) -> None:
    with pytest.raises(CategoryNotFound):
        await services.catalogue.search(ProductQuery(category_id="C-999"))


async def test_get_product_combines_price_and_stock(services: Services) -> None:
    detail = await services.catalogue.get_product("TRAP-OVERSOLD-001")
    assert detail.stock is not None and detail.stock.oversold
    assert detail.margin_pct == Decimal("66.67")
    with pytest.raises(SkuNotFound):
        await services.catalogue.get_product("NOPE-1")


async def test_taxonomy_is_a_tree(services: Services) -> None:
    roots = await services.catalogue.taxonomy()
    assert [r.category.id for r in roots] == ["C-100", "C-200", "C-300", "C-400", "C-500", "C-600"]
    assert [c.category.id for c in roots[0].children] == ["C-110", "C-120", "C-130", "C-140"]


async def test_attribute_plan_then_apply(
    services: Services, seed_data: SeedData, merchandiser: WriteLimits
) -> None:
    skus = active_skus(seed_data, "C-110", 3)
    request = UpdateAttributesRequest(
        skus=skus, set_values={"colour": "Sage", "eco_label": True}, reason="range refresh"
    )
    plan = await services.catalogue.plan_attribute_update(request, merchandiser)
    assert plan.affected_count == 3
    assert all(line.new_value in ("Sage", True) for line in plan.lines)

    # Planning changed nothing...
    assert (await services.catalogue.get_product(skus[0])).product.attributes.get(
        "eco_label"
    ) is None
    await services.catalogue.apply_attribute_update(plan)
    # ...applying changed exactly what the plan said.
    attrs = (await services.catalogue.get_product(skus[0])).product.attributes
    assert attrs["colour"] == "Sage" and attrs["eco_label"] is True and "gsm" in attrs

    with pytest.raises(NoChange):
        await services.catalogue.plan_attribute_update(request, merchandiser)


async def test_attribute_update_guards(services: Services, seed_data: SeedData) -> None:
    tiny = WriteLimits(max_skus=2)
    skus = active_skus(seed_data, "C-110", 3)
    with pytest.raises(ProtectedAttribute):
        await services.catalogue.plan_attribute_update(
            UpdateAttributesRequest(
                skus=skus[:1], set_values={"status": "disabled"}, reason="hide item"
            ),
            tiny,
        )
    with pytest.raises(BlastRadiusExceeded) as err:
        await services.catalogue.plan_attribute_update(
            UpdateAttributesRequest(
                skus=skus, set_values={"colour": "Sage"}, reason="range refresh"
            ),
            tiny,
        )
    assert "batches of at most 2" in err.value.hint
    with pytest.raises(SkuInactive):
        await services.catalogue.plan_attribute_update(
            UpdateAttributesRequest(
                skus=["TRAP-DISABLED-001"], set_values={"colour": "Sage"}, reason="range refresh"
            ),
            tiny,
        )
