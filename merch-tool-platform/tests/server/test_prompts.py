"""Phase 4: prompts (user-chosen workflows) and argument autocomplete."""

from __future__ import annotations

import re

import pytest
from mcp import Client, MCPError
from mcp.types import PromptReference, ResourceTemplateReference

from merch_platform.server.prompts import READ_ONLY_NOTICE

pytestmark = pytest.mark.anyio

PROMPTS = {
    "weekly_margin_review": {"category_id": True, "floor_pct": False},
    "clearance_plan": {"category_id": True, "min_available": False, "max_discount_pct": False},
    "promo_conflict_check": {"category_id": True, "starts": True, "ends": True, "exclusive": False},
}
VALID = {
    "weekly_margin_review": {"category_id": "C-110"},
    "clearance_plan": {"category_id": "C-110"},
    "promo_conflict_check": {"category_id": "C-340", "starts": "2026-10-30", "ends": "2026-11-05"},
}


async def prompt_text(client: Client, name: str, args: dict[str, str]) -> str:
    result = await client.get_prompt(name, args)
    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.role == "user"
    return str(message.content.text)  # type: ignore[union-attr]


async def error_message(client: Client, name: str, args: dict[str, str]) -> str:
    with pytest.raises(MCPError) as caught:
        await client.get_prompt(name, args)
    return caught.value.error.message


# ------------------------------------------------------------------ listing
async def test_prompts_are_listed_with_their_arguments(client: Client) -> None:
    listed = (await client.list_prompts()).prompts
    found = {p.name: {a.name: bool(a.required) for a in p.arguments or []} for p in listed}
    assert found == PROMPTS
    for p in listed:
        assert p.title and p.description, p.name
        assert all(a.description for a in p.arguments or []), p.name
    assert ((await client.list_prompts()).ttl_ms, (await client.list_prompts()).cache_scope) == (
        300_000,
        "private",
    )


# ------------------------------------------------------------------ content
@pytest.mark.parametrize("name", sorted(PROMPTS))
async def test_every_prompt_is_read_only_and_names_real_tools(client: Client, name: str) -> None:
    text = await prompt_text(client, name, VALID[name])
    assert READ_ONLY_NOTICE in text
    tools = {t.name for t in (await client.list_tools()).tools}
    mentioned = set(re.findall(r"\b(?:search|get|explain)_[a-z_]+\b", text))
    assert mentioned <= tools, mentioned - tools


async def test_margin_review_uses_the_arguments_and_gives_the_formula(client: Client) -> None:
    text = await prompt_text(
        client, "weekly_margin_review", {"category_id": "C-110", "floor_pct": "20%"}
    )
    assert "C-110 (Bath towels)" in text
    assert "max_margin_pct=20" in text
    assert "cost / (1 - 20/100)" in text


async def test_clearance_plan_uses_defaults(client: Client) -> None:
    text = await prompt_text(client, "clearance_plan", {"category_id": "C-110"})
    assert "at least 100 units available" in text
    assert "at most 30%" in text


async def test_conflict_check_embeds_exact_facts(client: Client) -> None:
    text = await prompt_text(
        client,
        "promo_conflict_check",
        {"category_id": "C-340", "starts": "2026-10-30", "ends": "2026-11-05", "exclusive": "yes"},
    )
    assert text.startswith("I am planning an exclusive promotion on category C-340 (Diffusers)")
    assert '- SPA-EXCL "Diffuser event (exclusive)": 15% off, 2026-10-23 to 2026-11-06, ' in text
    assert re.search(r"EXCLUSIVE, shares [1-9]\d* SKU\(s\) with C-340", text)
    assert "TOWEL10" not in text  # ended on 2026-10-13, before the planned dates


async def test_conflict_check_with_nothing_scheduled(client: Client) -> None:
    text = await prompt_text(
        client,
        "promo_conflict_check",
        {"category_id": "C-110", "starts": "2027-03-01", "ends": "2027-03-07"},
    )
    assert "a non-exclusive promotion" in text
    assert "- none: no promotions run between these dates." in text


# ------------------------------------------------------------------ bad arguments
async def test_unknown_category_lists_the_valid_ones(client: Client) -> None:
    message = await error_message(client, "weekly_margin_review", {"category_id": "C-999"})
    assert message.startswith("CATEGORY_NOT_FOUND: Unknown category ID(s): C-999.")
    assert '"C-110":"Bath towels"' in message


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("weekly_margin_review", {"category_id": "C-110", "floor_pct": "lots"}, "floor_pct"),
        ("weekly_margin_review", {"category_id": "C-110", "floor_pct": "NaN"}, "floor_pct"),
        ("clearance_plan", {"category_id": "C-110", "max_discount_pct": "95"}, "max_discount_pct"),
        ("clearance_plan", {"category_id": "C-110", "min_available": "1.5"}, "min_available"),
        (
            "promo_conflict_check",
            {"category_id": "C-110", "starts": "31/10/2026", "ends": "2026-11-05"},
            "starts",
        ),
        (
            "promo_conflict_check",
            {**VALID["promo_conflict_check"], "exclusive": "maybe"},
            "exclusive",
        ),
    ],
)
async def test_bad_arguments_are_named(
    client: Client, name: str, args: dict[str, str], expected: str
) -> None:
    message = await error_message(client, name, args)
    assert message.startswith(f"INVALID_ARGUMENTS: {expected} must be ")


async def test_backwards_dates_are_refused(client: Client) -> None:
    message = await error_message(
        client,
        "promo_conflict_check",
        {"category_id": "C-110", "starts": "2026-11-05", "ends": "2026-10-30"},
    )
    assert message.startswith("INVALID_DATE_RANGE: ")
    assert "Hint: Swap the two dates." in message


async def test_missing_required_argument_is_refused(client: Client) -> None:
    with pytest.raises(MCPError):
        await client.get_prompt("weekly_margin_review", {})


# ------------------------------------------------------------------ completions
async def complete(client: Client, ref: object, name: str, value: str) -> list[str]:
    result = await client.complete(ref, {"name": name, "value": value})  # type: ignore[arg-type]
    return list(result.completion.values)


async def test_category_completion_matches_ids_and_names(client: Client) -> None:
    ref = PromptReference(type="ref/prompt", name="clearance_plan")
    assert await complete(client, ref, "category_id", "C-2") == [
        "C-200",
        "C-210",
        "C-220",
        "C-230",
        "C-240",
    ]
    assert await complete(client, ref, "category_id", "TOW") == ["C-110", "C-140", "C-630"]
    assert await complete(client, ref, "category_id", "zzz") == []


async def test_month_completion_offers_months_around_today(client: Client) -> None:
    ref = ResourceTemplateReference(type="ref/resource", uri="promotions://calendar/{month}")
    months = await complete(client, ref, "month", "")
    assert months[0] == "2026-06" and months[-1] == "2027-09" and len(months) == 16
    assert await complete(client, ref, "month", "2026-1") == ["2026-10", "2026-11", "2026-12"]


async def test_no_completion_for_free_text_arguments(client: Client) -> None:
    ref = PromptReference(type="ref/prompt", name="weekly_margin_review")
    assert await complete(client, ref, "floor_pct", "1") == []
