"""Phase 3: the compact text each tool sends to the MODEL.

Rules every summary follows:
1. Keep every fact the model needs to answer (SKUs, prices, margins, stock, rule ids),
   because the model reads this text, not structured_content.
2. Drop everything else: JSON brackets, quotes, repeated field names, nulls.
3. Put the answer first, then the detail, one item per line.
4. Show money as £0.00 and percentages as 0.00%, so the model never has to guess units.
5. End with what to do next when the result is incomplete (for example, truncated).

Tests in tests/server/test_contracts.py check that every SKU and key number in the
structured data also appears in the summary, so nothing important can be dropped.
"""

from __future__ import annotations

from decimal import Decimal

from merch_platform.domain.commands import (
    InventoryPosition,
    PriceExplanation,
    ProductDetail,
    ProductSearchResult,
)


def gbp(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}£{abs(value):.2f}"


def pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def search(result: ProductSearchResult) -> str:
    if result.count == 0:
        return "No products matched. Try fewer or different words, or remove a filter."
    header = f"{result.count} product(s), sorted by SKU. Columns: SKU | name | category | "
    header += "base price | cost | margin"
    lines = [header]
    for item in result.items:
        disabled = " | DISABLED" if item.status != "active" else ""
        lines.append(
            f"{item.sku} | {item.name} | {item.category_id} | {gbp(item.base_price)} | "
            f"{gbp(item.cost)} | {pct(item.margin_pct)}{disabled}"
        )
    if result.truncated:
        lines.append("More products match than shown. Narrow the search to see the rest.")
    return "\n".join(lines)


def product(detail: ProductDetail) -> str:
    p, price = detail.product, detail.price
    attributes = ", ".join(f"{k}={v}" for k, v in sorted(p.attributes.items())) or "none"
    if detail.stock is None:
        stock = "no stock record (treat as 0 on hand)"
    else:
        available = max(detail.stock.on_hand - detail.stock.reserved, 0)
        stock = (
            f"{detail.stock.on_hand} on hand, {detail.stock.reserved} reserved, "
            f"{available} available"
        )
        if detail.stock.oversold:
            stock += " (OVERSOLD)"
    return "\n".join(
        [
            f"{p.sku}: {p.name}",
            f"Category {p.category_id} | status {p.status}",
            f"Base price {gbp(price.base_price)} | cost {gbp(price.cost)} | "
            f"margin {pct(detail.margin_pct)} (before price rules; see explain_price)",
            f"Stock: {stock}",
            f"Attributes: {attributes}",
        ]
    )


def inventory(positions: tuple[InventoryPosition, ...]) -> str:
    lines = ["SKU | on hand | reserved | available"]
    for pos in positions:
        flag = " | OVERSOLD" if pos.oversold else ""
        lines.append(f"{pos.sku} | {pos.on_hand} | {pos.reserved} | {pos.available}{flag}")
    oversold = sum(1 for pos in positions if pos.oversold)
    if oversold:
        lines.append(f"{oversold} SKU(s) oversold: more units reserved than on hand.")
    return "\n".join(lines)


def price_explanation(e: PriceExplanation) -> str:
    lines = [
        f"{e.sku} on {e.on.isoformat()}: final price {gbp(e.final_price)} "
        f"(base {gbp(e.base_price)}, cost {gbp(e.cost)}, final margin {pct(e.final_margin_pct)})"
    ]
    if e.applied:
        lines.append("Applied, in order:")
        for step in e.applied:
            stop = " [stopped further rules]" if step.stopped_further_rules else ""
            lines.append(
                f'- {step.rule_id} "{step.rule_name}" (priority {step.priority}): {step.action}, '
                f"{gbp(step.price_before)} -> {gbp(step.price_after)}{stop}"
            )
    else:
        lines.append("No price rules applied; the selling price is the base price.")
    if e.skipped:
        skipped = "; ".join(f"{s.rule_id}: {s.reason}" for s in e.skipped)
        lines.append(f"Skipped (in evaluation order): {skipped}")
    return "\n".join(lines)
