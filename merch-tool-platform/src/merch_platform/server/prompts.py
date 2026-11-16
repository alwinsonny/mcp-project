"""Phase 4: PROMPTS, reusable merchandising workflows a USER picks (like slash commands).

A prompt does not run anything. It returns ready-written instructions (and sometimes
live facts) that start a conversation, so every user gets the same careful method
instead of improvising one. The model then uses the tools to do the work.

Three prompts:
* weekly_margin_review   find low-margin products in a category and recommend prices
* clearance_plan         find overstocked products and recommend safe discounts
* promo_conflict_check   check a planned promotion against what is already scheduled

Design rules:
1. Facts that code can compute exactly are computed here and EMBEDDED in the prompt
   (promo_conflict_check embeds which promotions overlap and how many SKUs they share).
   The model is left with judgement, not arithmetic over data it cannot see.
2. Every number the model must calculate comes with its formula.
3. Until Phase 8 nothing may change data, so every prompt says: recommend, do not apply.
4. MCP sends prompt arguments as strings, so each one is parsed and checked here, with
   errors in the same CODE / Hint format as tool errors.

Completions: as the user types an argument, the host can ask for suggestions. We offer
category IDs (matching ID or name) and months for the promotion calendar.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.prompts.base import Message, UserMessage
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)
from pydantic import Field

from merch_platform.domain.commands import TaxonomyNode
from merch_platform.domain.errors import DomainError
from merch_platform.domain.models import Category, plain_number
from merch_platform.server.results import protocol_error
from merch_platform.services.catalogue import unknown_category
from merch_platform.services.container import Services

READ_ONLY_NOTICE = (
    "Do not try to change any data: this server is read-only, and changes need a "
    "person's approval. Give recommendations only."
)

MAX_COMPLETIONS = 100


# ---------------------------------------------------------------- argument parsing
def parse_decimal(name: str, raw: str, low: Decimal, high: Decimal) -> Decimal:
    try:
        value = Decimal(raw.strip().rstrip("%"))
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or not low <= value <= high:
        raise protocol_error(
            "INVALID_ARGUMENTS", f"{name} must be a number from {low} to {high}; got {raw!r}."
        )
    return value


def parse_int(name: str, raw: str, low: int, high: int) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        value = low - 1
    if not low <= value <= high:
        raise protocol_error(
            "INVALID_ARGUMENTS", f"{name} must be a whole number from {low} to {high}; got {raw!r}."
        )
    return value


def parse_date(name: str, raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise protocol_error(
            "INVALID_ARGUMENTS", f"{name} must be a date in YYYY-MM-DD form; got {raw!r}."
        ) from None


def parse_bool(name: str, raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "y", "1"}:
        return True
    if lowered in {"false", "no", "n", "0", ""}:
        return False
    raise protocol_error("INVALID_ARGUMENTS", f"{name} must be true or false; got {raw!r}.")


def flatten(nodes: Sequence[TaxonomyNode]) -> list[Category]:
    out: list[Category] = []
    for n in nodes:
        out.append(n.category)
        out.extend(flatten(n.children))
    return out


async def category_label(services: Services, category_id: str) -> str:
    """'C-110' -> 'C-110 (Bath towels)', or a CATEGORY_NOT_FOUND error listing valid IDs."""
    categories = flatten(await services.catalogue.taxonomy())
    by_id = {c.id: c for c in categories}
    if category_id not in by_id:
        err = unknown_category([category_id], categories)
        raise protocol_error(err.code, err.message, err.hint, err.details)
    return f"{category_id} ({by_id[category_id].name})"


CategoryArg = Annotated[
    str, Field(description="Category ID, e.g. C-110 (Bath towels) or C-100 (all of Bath).")
]


# ---------------------------------------------------------------- registration
def register_prompts(mcp: MCPServer, get_services: Callable[[], Services]) -> None:
    @mcp.prompt(title="Weekly margin review")
    async def weekly_margin_review(
        category_id: CategoryArg,
        floor_pct: Annotated[
            str, Field(description="Target minimum margin %, 0 to 90. Default 15.")
        ] = "15",
    ) -> list[Message]:
        """Find products in a category whose margin is below a target, and recommend base
        prices that restore it. Recommendations only."""
        services = get_services()
        label = await category_label(services, category_id)
        floor = plain_number(parse_decimal("floor_pct", floor_pct, Decimal("0"), Decimal("90")))
        return [
            UserMessage(
                f"Run a margin review for category {label} with a target margin of {floor}%. "
                "All money is GBP.\n\n"
                f"1. Call search_products with category_id={category_id} and "
                f"max_margin_pct={floor} (limit 50). If it says more products match, search "
                "again with narrower words until you have them all.\n"
                "2. For each product found, call explain_price to see the price customers "
                "actually pay after price rules. Rules can push the margin even lower.\n"
                "3. For each product, recommend the lowest base price that restores the "
                f"target: cost / (1 - {floor}/100), rounded UP to the penny.\n"
                "4. Reply with a table: SKU | name | base price | selling price | cost | "
                "margin now | recommended base price | margin at that price. Sort by "
                "margin now, lowest first. Then list anything you could not price, and why.\n\n"
                + READ_ONLY_NOTICE
            )
        ]

    @mcp.prompt(title="Clearance plan")
    async def clearance_plan(
        category_id: CategoryArg,
        min_available: Annotated[
            str,
            Field(
                description="Only products with at least this many units available. Default 100."
            ),
        ] = "100",
        max_discount_pct: Annotated[
            str, Field(description="Largest discount to recommend, 1 to 90 %. Default 30.")
        ] = "30",
    ) -> list[Message]:
        """Find overstocked products in a category and recommend clearance discounts that
        never sell below cost. Recommendations only."""
        services = get_services()
        label = await category_label(services, category_id)
        minimum = parse_int("min_available", min_available, 1, 1_000_000)
        cap = parse_decimal("max_discount_pct", max_discount_pct, Decimal("1"), Decimal("90"))
        return [
            UserMessage(
                f"Plan a clearance for category {label}. All money is GBP.\n\n"
                f"1. Call search_products with category_id={category_id} (limit 50). If it "
                "says more products match, search again with narrower words.\n"
                "2. Call get_inventory_position for those SKUs (up to 50 per call).\n"
                f"3. Candidates are products with at least {minimum} units available.\n"
                "4. For each candidate, call explain_price for its current selling price.\n"
                "5. Recommend a discount for each candidate of at most "
                f"{plain_number(cap)}%, and never more than the largest safe discount: "
                "(1 - cost / selling price) x 100, rounded DOWN to 2 decimal places. If the "
                "safe discount is 0 or less, recommend no discount and say why.\n"
                "6. Reply with a table: SKU | name | available | selling price | cost | "
                "recommended discount % | price after discount | margin after. Sort by units "
                "available, highest first.\n\n" + READ_ONLY_NOTICE
            )
        ]

    @mcp.prompt(title="Promotion conflict check")
    async def promo_conflict_check(
        category_id: CategoryArg,
        starts: Annotated[str, Field(description="Planned first day, YYYY-MM-DD.")],
        ends: Annotated[str, Field(description="Planned last day, YYYY-MM-DD.")],
        exclusive: Annotated[
            str,
            Field(
                description="Would the new promotion be exclusive? true or false. Default false."
            ),
        ] = "false",
    ) -> list[Message]:
        """Check a planned promotion on a category against the promotions already scheduled,
        using exact overlap facts computed by the server. Advice only."""
        services = get_services()
        label = await category_label(services, category_id)
        first, last = parse_date("starts", starts), parse_date("ends", ends)
        is_exclusive = parse_bool("exclusive", exclusive)
        try:
            report = await services.promotions.overlaps([category_id], [], first, last)
        except DomainError as err:
            raise protocol_error(err.code, err.message, err.hint, err.details) from err

        if report:
            facts = "\n".join(
                f'- {p.code} "{p.name}": {plain_number(p.discount_pct)}% off, '
                f"{p.starts} to {p.ends}, "
                f"{'EXCLUSIVE' if p.exclusive else 'not exclusive'}, "
                f"shares {shared} SKU(s) with {category_id}"
                for p, shared in report
            )
        else:
            facts = "- none: no promotions run between these dates."
        return [
            UserMessage(
                f"I am planning {'an exclusive' if is_exclusive else 'a non-exclusive'} "
                f"promotion on category {label} from {first} to {last}.\n\n"
                "Facts computed by the server (exact, do not recompute):\n"
                f"Promotions running at any point in those dates:\n{facts}\n\n"
                "Rule: two promotions conflict when their dates overlap, they share at least "
                "one SKU, and at least one of them is exclusive.\n\n"
                "1. Say whether the plan conflicts, naming each conflicting promotion.\n"
                "2. If it conflicts, suggest the nearest dates that avoid every conflict, or "
                "making the new promotion non-exclusive if that alone would fix it.\n"
                "3. Mention any non-conflicting overlaps a merchandiser should still know "
                "about (customers may see two offers at once).\n\n" + READ_ONLY_NOTICE
            )
        ]

    # ---------------------------------------------------------------- completions
    # The SDK's completion() decorator has no type hints, so strict mypy needs this one ignore.
    @mcp.completion()  # type: ignore[no-untyped-call, untyped-decorator]
    async def complete(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion | None:
        services = get_services()
        typed = str(argument.value).strip().lower()
        if isinstance(ref, PromptReference) and argument.name == "category_id":
            categories = flatten(await services.catalogue.taxonomy())
            values = [
                c.id
                for c in categories
                if c.id.lower().startswith(typed) or typed in c.name.lower()
            ]
        elif (
            isinstance(ref, ResourceTemplateReference)
            and ref.uri == "promotions://calendar/{month}"
            and argument.name == "month"
        ):
            today = services.clock.today()
            months = []
            for offset in range(-3, 13):  # from 3 months ago to a year ahead
                index = today.year * 12 + today.month - 1 + offset
                months.append(f"{index // 12:04d}-{index % 12 + 1:02d}")
            values = [m for m in months if m.startswith(typed)]
        else:
            return None
        return Completion(
            values=values[:MAX_COMPLETIONS],
            total=len(values),
            has_more=len(values) > MAX_COMPLETIONS,
        )
