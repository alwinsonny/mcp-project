"""Phase 4: resources, read-only reference data a host app can attach to a conversation."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client, MCPError

pytestmark = pytest.mark.anyio

STATIC = {"catalog://taxonomy", "pricing://rules/active"}
TEMPLATE = "promotions://calendar/{month}"


async def read_json(client: Client, uri: str) -> Any:
    result = await client.read_resource(uri)
    assert len(result.contents) == 1
    content = result.contents[0]
    assert content.mime_type == "application/json"
    return json.loads(content.text)  # type: ignore[union-attr]


async def test_resources_and_template_are_listed(client: Client) -> None:
    resources = (await client.list_resources()).resources
    assert {str(r.uri) for r in resources} == STATIC
    templates = (await client.list_resource_templates()).resource_templates
    assert [t.uri_template for t in templates] == [TEMPLATE]
    for item in resources:
        assert item.title and item.description, item.name
        assert item.meta == {"contract_version": "1", "risk_class": "read"}, item.name
    for template in templates:
        assert template.title and template.description, template.name
        assert template.meta == {"contract_version": "1", "risk_class": "read"}, template.name


async def test_taxonomy_is_a_tree_of_every_category(client: Client, seed_data: Any) -> None:
    data = await read_json(client, "catalog://taxonomy")

    def ids(nodes: list[dict[str, Any]]) -> set[str]:
        return {n["id"] for n in nodes} | {i for n in nodes for i in ids(n.get("children", []))}

    assert ids(data["categories"]) == {c.id for c in seed_data.categories}
    bath = next(n for n in data["categories"] if n["id"] == "C-100")
    assert {"id": "C-110", "name": "Bath towels"} in bath["children"]


async def test_active_rules_are_in_force_today_and_in_order(client: Client) -> None:
    data = await read_json(client, "pricing://rules/active")
    assert data["on"] == "2026-09-23"
    ids = [r["id"] for r in data["rules"]]
    assert "R-005" not in ids and "R-006" not in ids  # expired / not started
    priorities = [r["priority"] for r in data["rules"]]
    assert priorities == sorted(priorities)
    clearance = next(r for r in data["rules"] if r["id"] == "R-004")
    assert clearance["action"] == "set to £5.00"
    assert clearance["applies_to"]["skus"]


async def test_calendar_template_reads_a_month(client: Client) -> None:
    october = await read_json(client, "promotions://calendar/2026-10")
    assert october["month"] == "2026-10"
    assert [p["code"] for p in october["promotions"]] == ["TOWEL10", "SPA-EXCL"]
    excl = october["promotions"][1]
    assert excl == {
        "code": "SPA-EXCL",
        "name": "Diffuser event (exclusive)",
        "discount_pct": "15",
        "category_ids": ["C-340"],
        "skus": [],
        "starts": "2026-10-23",
        "ends": "2026-11-06",
        "exclusive": True,
    }
    assert (await read_json(client, "promotions://calendar/2027-03"))["promotions"] == []


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-10", "october"])
async def test_bad_month_is_a_readable_error(client: Client, month: str) -> None:
    with pytest.raises(MCPError) as caught:
        await client.read_resource(f"promotions://calendar/{month}")
    message = caught.value.error.message
    assert message.startswith("INVALID_MONTH: ")
    assert "Hint: Use YYYY-MM" in message


async def test_unknown_resource_is_refused(client: Client) -> None:
    with pytest.raises(MCPError):
        await client.read_resource("catalog://nope")


async def test_resources_are_compact(client: Client) -> None:
    """Resource text goes into the model's context, so no pretty-printing."""
    for uri in [*STATIC, "promotions://calendar/2026-10"]:
        text = (await client.read_resource(uri)).contents[0].text  # type: ignore[union-attr]
        assert "\n" not in text  # not indented
        assert '": ' not in text and '", "' not in text  # no spaces after separators
        assert len(text) < 4_000, (uri, len(text))


async def test_lists_and_reads_carry_private_cache_hints(client: Client) -> None:
    resources = await client.list_resources()
    assert (resources.ttl_ms, resources.cache_scope) == (300_000, "private")
    templates = await client.list_resource_templates()
    assert (templates.ttl_ms, templates.cache_scope) == (300_000, "private")
    read = await client.read_resource("catalog://taxonomy")
    assert (read.ttl_ms, read.cache_scope) == (60_000, "private")
