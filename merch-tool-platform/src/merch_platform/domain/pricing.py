"""The price-rule engine: a pure function, easy to test exhaustively.

Semantics (modelled on Magento catalogue price rules):
1. Only rules active on the given day are considered.
2. Rules are evaluated in ascending priority; ties break on rule id, so the
   result never depends on database row order.
3. Each matching rule transforms the running price.
4. A matching rule with stop_further=True ends evaluation.
5. The price never goes below zero.

explain_price() records every rule's fate (applied, or skipped with a reason),
because "why is this SKU £7.20?" is the question merchandisers actually ask.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from merch_platform.domain.commands import PriceExplanation, PriceStep, SkippedRule
from merch_platform.domain.models import PriceRecord, PriceRule, Product, margin_pct


def order_rules(rules: Iterable[PriceRule]) -> list[PriceRule]:
    return sorted(rules, key=lambda r: (r.priority, r.id))


def explain_price(
    product: Product, price: PriceRecord, rules: Iterable[PriceRule], on: date
) -> PriceExplanation:
    running = price.base_price
    applied: list[PriceStep] = []
    skipped: list[SkippedRule] = []
    stopped_by: PriceRule | None = None

    for rule in order_rules(rules):
        if stopped_by is not None:
            skipped.append(
                SkippedRule(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    reason=f"stopped by {stopped_by.id} (stop further rules)",
                )
            )
            continue
        if not rule.active_on(on):
            skipped.append(
                SkippedRule(rule_id=rule.id, rule_name=rule.name, reason="not active on date")
            )
            continue
        matches, reason = rule.condition.check(product, price)
        if not matches:
            skipped.append(SkippedRule(rule_id=rule.id, rule_name=rule.name, reason=reason))
            continue

        before = running
        running = rule.action.apply(running)
        applied.append(
            PriceStep(
                rule_id=rule.id,
                rule_name=rule.name,
                priority=rule.priority,
                action=rule.action.describe(),
                price_before=before,
                price_after=running,
                stopped_further_rules=rule.stop_further,
            )
        )
        if rule.stop_further:
            stopped_by = rule

    return PriceExplanation(
        sku=product.sku,
        on=on,
        base_price=price.base_price,
        final_price=running,
        cost=price.cost,
        final_margin_pct=margin_pct(running, price.cost),
        applied=tuple(applied),
        skipped=tuple(skipped),
    )


def final_price(
    product: Product, price: PriceRecord, rules: Iterable[PriceRule], on: date
) -> PriceRecord:
    """Convenience: the effective selling price as a PriceRecord (base_price = final)."""
    result = explain_price(product, price, rules, on)
    return price.model_copy(update={"base_price": result.final_price})
