"""Phase 2 exit checks: the four read tools, seen through a real MCP client."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from mcp import Client

from merch_platform.seed.generator import SeedData
from tests.services.helpers import active_skus

pytestmark = pytest.mark.anyio

READ_TOOLS = {"search_products", "get_product", "get_inventory_position", "explain_price"}


def text(result: Any) -> str:
    return str(result.content[0].text)


# ------------------------------------------------------------------ the catalogue
async def test_exactly_the_four_read_tools_are_listed(client: Client) -> None:
    names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == READ_TOOLS
    # Safety: no write tool may be reachable before Phase 8 adds permissions and dry runs.
    assert not names & {"set_price", "create_promotion", "apply_order_adjustment"}


async def test_every_tool_is_marked_read_only_and_has_an_output_schema(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.output_schema is not None, f"{tool.name} has no output schema"


async def test_descriptions_are_written_for_the_model(client: Client) -> None:
    """The model chooses tools from these words alone, so none may be missing."""
    for tool in (await client.list_tools()).tools:
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.title, tool.name
        for name, schema in tool.input_schema["properties"].items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


async def test_context_parameter_is_hidden_from_the_model(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        assert "ctx" not in tool.input_schema["properties"]


# ------------------------------------------------------------------ search_products
async def test_search_returns_structured_results(client: Client) -> None:
    result = await client.call_tool("search_products", {"query": "trap mug"})
    assert not result.is_error
    assert result.structured_content is not None
    [item] = result.structured_content["items"]
    assert item["sku"] == "TRAP-RULES-001"
    assert item["base_price"] == "20.00"  # money stays exact: a string, never a float


async def test_search_margin_filter_finds_negative_margin(client: Client) -> None:
    result = await client.call_tool("search_products", {"max_margin_pct": 0})
    skus = [i["sku"] for i in result.structured_content["items"]]
    assert "TRAP-NEGMARGIN-001" in skus


async def test_disabled_products_need_explicit_opt_in(client: Client) -> None:
    hidden = await client.call_tool("search_products", {"query": "trap pillow"})
    shown = await client.call_tool(
        "search_products", {"query": "trap pillow", "include_disabled": True}
    )
    assert hidden.structured_content["count"] == 0
    assert shown.structured_content["count"] == 1


async def test_schema_rejects_bad_arguments_before_the_tool_runs(client: Client) -> None:
    result = await client.call_tool("search_products", {"limit": 999})
    assert result.is_error
    assert "less than or equal to 50" in text(result)


async def test_back_to_front_margin_range_explains_itself(client: Client) -> None:
    """Regression: this used to surface only as 'Error executing tool search_products'."""
    result = await client.call_tool("search_products", {"min_margin_pct": 50, "max_margin_pct": 10})
    assert result.is_error
    assert "INVALID_ARGUMENTS" in text(result)
    assert "min_margin_pct cannot be greater than max_margin_pct" in text(result)


async def test_unknown_category_gives_code_and_hint(client: Client) -> None:
    result = await client.call_tool("search_products", {"category_id": "C-999"})
    assert result.is_error
    assert "CATEGORY_NOT_FOUND" in text(result) and "Hint:" in text(result)


# ------------------------------------------------------------------ get_product
async def test_get_product_combines_price_and_stock(client: Client) -> None:
    result = await client.call_tool("get_product", {"sku": "TRAP-OVERSOLD-001"})
    data = result.structured_content
    assert data is not None
    assert data["product"]["sku"] == "TRAP-OVERSOLD-001"
    assert data["stock"] == {"sku": "TRAP-OVERSOLD-001", "on_hand": 3, "reserved": 12}
    assert data["margin_pct"] == "66.67"


async def test_unknown_sku_tells_the_model_what_to_do(client: Client) -> None:
    result = await client.call_tool("get_product", {"sku": "NOPE-001"})
    assert result.is_error
    assert "SKU_NOT_FOUND" in text(result)
    assert "search_products" in text(result)  # the hint names the tool to use instead


# ------------------------------------------------------------------ get_inventory_position
async def test_inventory_flags_oversold(client: Client) -> None:
    result = await client.call_tool("get_inventory_position", {"skus": ["TRAP-OVERSOLD-001"]})
    [position] = result.structured_content["positions"]
    assert position == {
        "sku": "TRAP-OVERSOLD-001",
        "on_hand": 3,
        "reserved": 12,
        "available": 0,
        "oversold": True,
    }


async def test_inventory_rejects_more_than_fifty_skus(client: Client) -> None:
    result = await client.call_tool(
        "get_inventory_position", {"skus": [f"TW-{i:05d}" for i in range(51)]}
    )
    assert result.is_error


# ------------------------------------------------------------------ explain_price
async def test_explain_price_shows_every_step(client: Client) -> None:
    result = await client.call_tool("explain_price", {"sku": "TRAP-RULES-001"})
    data = result.structured_content
    assert data is not None
    assert data["final_price"] == "13.00"
    assert [s["rule_id"] for s in data["applied"]] == ["R-T01", "R-T02"]
    skipped = {s["rule_id"]: s["reason"] for s in data["skipped"]}
    assert "stopped by R-T02" in skipped["R-T03"]


async def test_explain_price_on_a_future_date(client: Client, seed_data: SeedData) -> None:
    towel = active_skus(seed_data, "C-110", 1)[0]
    result = await client.call_tool("explain_price", {"sku": towel, "on": "2026-11-14"})
    applied = [s["rule_id"] for s in result.structured_content["applied"]]
    assert applied == ["R-006", "R-001"]  # Black Friday rule first, then the towel rule


async def test_explain_price_rejects_a_bad_date(client: Client) -> None:
    result = await client.call_tool("explain_price", {"sku": "TRAP-RULES-001", "on": "14/11/2026"})
    assert result.is_error


# ------------------------------------------------------------------ portability
LOOKAROUNDS = ("(?=", "(?!", "(?<=", "(?<!")


async def test_schemas_contain_no_regex_lookarounds(client: Client) -> None:
    """Regression: the MCP Inspector rejected explain_price because Pydantic's Decimal
    regex uses a lookahead its validator cannot evaluate. Every schema must be portable."""
    for tool in (await client.list_tools()).tools:
        for schema in (tool.input_schema, tool.output_schema):
            dumped = json.dumps(schema)
            assert not any(token in dumped for token in LOOKAROUNDS), tool.name


async def test_money_values_match_the_published_pattern(client: Client) -> None:
    result = await client.call_tool("explain_price", {"sku": "TRAP-NEGMARGIN-001"})
    data = result.structured_content
    assert data is not None
    pattern = re.compile(r"^-?\d+(\.\d+)?$")
    for field in ("base_price", "final_price", "cost", "final_margin_pct"):
        assert pattern.fullmatch(data[field]), (field, data[field])
    assert data["final_margin_pct"].startswith("-")  # negative margin still matches
