from decimal import Decimal

import pytest

from merch_platform.domain.models import (
    Order,
    OrderLine,
    StockLevel,
    margin_pct,
    min_price_for_margin,
    money,
)

D = Decimal


def test_money_rounds_half_up() -> None:
    assert money(D("1.005")) == D("1.01")
    assert money(D("1.004")) == D("1.00")


def test_margin_pct() -> None:
    assert margin_pct(D("50.00"), D("44.75")) == D("10.50")
    assert margin_pct(D("8.00"), D("9.50")) == D("-18.75")  # negative margin is representable
    assert margin_pct(D("0"), D("5")) is None  # undefined at zero price


def test_min_price_for_margin_rounds_up() -> None:
    # 44.75 / 0.9 = 49.7222... -> must round UP, or 49.72 would sit just below 10%
    assert min_price_for_margin(D("44.75"), D("10")) == D("49.73")
    assert margin_pct(D("49.73"), D("44.75")) >= D("10")  # type: ignore[operator]
    assert (D("49.72") - D("44.75")) / D("49.72") * 100 < 10


def test_min_price_rejects_floor_of_100() -> None:
    with pytest.raises(ValueError):
        min_price_for_margin(D("10"), D("100"))


def test_stock_available_and_oversold() -> None:
    assert StockLevel(sku="X-1", on_hand=10, reserved=3).available == 7
    oversold = StockLevel(sku="X-1", on_hand=3, reserved=12)
    assert oversold.available == 0 and oversold.oversold


def test_order_totals() -> None:
    order = Order(
        order_id="ORD-1",
        placed_on=__import__("datetime").date(2026, 9, 1),
        lines=(OrderLine(sku="A-1", qty=2, unit_price_paid=D("15.00")),),
        adjustments_total=D("25.00"),
    )
    assert order.total_paid == D("30.00")
    assert order.refundable_remaining == D("5.00")
