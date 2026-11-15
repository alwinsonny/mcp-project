"""Requests reject bad SHAPES before any service runs."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from merch_platform.domain.commands import (
    CreatePromotionRequest,
    ProductQuery,
    SetPriceRequest,
    UpdateAttributesRequest,
)


def test_set_price_rejects_duplicates_zero_and_short_reason() -> None:
    with pytest.raises(ValidationError, match="duplicate SKUs: TW-00001"):
        SetPriceRequest(skus=["TW-00001", "TW-00001"], new_price=Decimal("10"), reason="price fix")
    with pytest.raises(ValidationError):
        SetPriceRequest(skus=["TW-00001"], new_price=Decimal("0"), reason="price fix")
    with pytest.raises(ValidationError):
        SetPriceRequest(skus=["TW-00001"], new_price=Decimal("10.999"), reason="price fix")
    with pytest.raises(ValidationError):
        SetPriceRequest(skus=["TW-00001"], new_price=Decimal("10"), reason="no")


def test_set_price_caps_request_size() -> None:
    with pytest.raises(ValidationError):
        SetPriceRequest(
            skus=[f"TW-{i:05d}" for i in range(501)], new_price=Decimal("10"), reason="bulk change"
        )


def test_attribute_keys_must_be_snake_case() -> None:
    with pytest.raises(ValidationError, match="lower_snake_case"):
        UpdateAttributesRequest(
            skus=["TW-00001"], set_values={"Colour": "Sage"}, reason="rename colour"
        )
    ok = UpdateAttributesRequest(
        skus=["TW-00001"], set_values={"colour": "Sage"}, reason="rename colour"
    )
    assert ok.set_values == {"colour": "Sage"}


def test_promotion_request_rules() -> None:
    base = dict(code="AUTUMN10", name="Autumn", discount_pct=Decimal("10"), category_ids=["C-110"])
    with pytest.raises(ValidationError, match="ends must be on or after starts"):
        CreatePromotionRequest(**base, starts=date(2026, 10, 10), ends=date(2026, 10, 1))
    with pytest.raises(ValidationError, match="at most 92 days"):
        CreatePromotionRequest(**base, starts=date(2026, 10, 1), ends=date(2027, 3, 1))
    with pytest.raises(ValidationError):
        CreatePromotionRequest(
            **{**base, "code": "lower case"}, starts=date(2026, 10, 1), ends=date(2026, 10, 2)
        )
    with pytest.raises(ValidationError, match="category_ids or skus"):
        CreatePromotionRequest(
            **{**base, "category_ids": []}, starts=date(2026, 10, 1), ends=date(2026, 10, 2)
        )


def test_query_margin_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        ProductQuery(min_margin_pct=Decimal("30"), max_margin_pct=Decimal("10"))
    assert ProductQuery(query="  Bath   TOWEL ").terms == ["bath", "towel"]
