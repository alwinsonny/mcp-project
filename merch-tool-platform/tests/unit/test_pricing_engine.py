"""The price-rule engine is pure, so every rule interaction can be pinned down exactly."""

from datetime import date, timedelta
from decimal import Decimal

from merch_platform.domain.models import PriceRecord, PriceRule, Product, RuleAction, RuleCondition
from merch_platform.domain.pricing import explain_price

D = Decimal
DAY = date(2026, 9, 23)
PRODUCT = Product(sku="MG-00001", name="Mug", category_id="C-610")
PRICE = PriceRecord(sku="MG-00001", base_price=D("20.00"), cost=D("8.00"))


def rule(rid: str, priority: int, kind: str, amount: str, **kw: object) -> PriceRule:
    return PriceRule(
        id=rid,
        name=rid,
        priority=priority,
        active_from=kw.pop("active_from", DAY - timedelta(days=1)),
        action=RuleAction(kind=kind, amount=D(amount)),
        **kw,
    )


def test_no_rules_means_base_price() -> None:
    e = explain_price(PRODUCT, PRICE, [], DAY)
    assert e.final_price == D("20.00") and e.applied == () and e.final_margin_pct == D("60.00")


def test_rules_apply_in_priority_order_not_list_order() -> None:
    # 20 -> £5 off -> 15 -> 10% off -> 13.50.  (The other order would give 13.00.)
    rules = [rule("B", 20, "percent_off", "10"), rule("A", 10, "fixed_off", "5")]
    e = explain_price(PRODUCT, PRICE, rules, DAY)
    assert [s.rule_id for s in e.applied] == ["A", "B"]
    assert e.final_price == D("13.50")


def test_equal_priority_breaks_ties_on_rule_id() -> None:
    rules = [rule("Z", 10, "fixed_off", "5"), rule("A", 10, "percent_off", "50")]
    e = explain_price(PRODUCT, PRICE, rules, DAY)
    assert [s.rule_id for s in e.applied] == ["A", "Z"]
    assert e.final_price == D("5.00")  # 20 * 0.5 = 10, minus 5


def test_stop_further_blocks_later_rules_and_says_why() -> None:
    rules = [
        rule("R1", 5, "percent_off", "20"),
        rule("R2", 10, "fixed_off", "3", stop_further=True),
        rule("R3", 20, "percent_off", "50"),
    ]
    e = explain_price(PRODUCT, PRICE, rules, DAY)
    assert e.final_price == D("13.00")
    assert e.applied[-1].stopped_further_rules
    assert e.skipped[0].rule_id == "R3" and "stopped by R2" in e.skipped[0].reason


def test_inactive_rules_are_skipped_with_reason() -> None:
    expired = rule(
        "OLD",
        1,
        "percent_off",
        "50",
        active_from=DAY - timedelta(days=30),
        active_to=DAY - timedelta(days=1),
    )
    future = rule("NEW", 2, "percent_off", "50", active_from=DAY + timedelta(days=1))
    e = explain_price(PRODUCT, PRICE, [expired, future], DAY)
    assert e.final_price == D("20.00")
    assert {s.reason for s in e.skipped} == {"not active on date"}


def test_rule_active_on_its_last_day() -> None:
    last_day = rule("R", 1, "fixed_off", "1", active_from=DAY - timedelta(days=5), active_to=DAY)
    assert explain_price(PRODUCT, PRICE, [last_day], DAY).final_price == D("19.00")


def test_conditions_category_sku_and_min_price() -> None:
    other_cat = rule("C", 1, "fixed_off", "1", condition=RuleCondition(category_ids=("C-999",)))
    other_sku = rule("S", 2, "fixed_off", "1", condition=RuleCondition(skus=("XX-1",)))
    too_cheap = rule("M", 3, "fixed_off", "1", condition=RuleCondition(min_base_price=D("25")))
    e = explain_price(PRODUCT, PRICE, [other_cat, other_sku, too_cheap], DAY)
    assert e.applied == () and len(e.skipped) == 3
    assert "C-610 not targeted" in e.skipped[0].reason


def test_set_price_and_floor_at_zero() -> None:
    set_rule = rule("SET", 1, "set_price", "5.00")
    big_cut = rule("CUT", 2, "fixed_off", "99")
    e = explain_price(PRODUCT, PRICE, [set_rule, big_cut], DAY)
    assert [str(s.price_after) for s in e.applied] == ["5.00", "0.00"]
    assert e.final_price == D("0.00") and e.final_margin_pct is None


def test_percent_off_rounds_to_pennies() -> None:
    odd = PriceRecord(sku="MG-00001", base_price=D("19.99"), cost=D("8.00"))
    assert explain_price(PRODUCT, odd, [rule("P", 1, "percent_off", "15")], DAY).final_price == D(
        "16.99"
    )
