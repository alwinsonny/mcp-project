"""Phase 3 exit checks: every tool is a precise, versioned, token-efficient contract."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from mcp import Client

from merch_platform.server.results import ERROR_CODE_META_KEY

pytestmark = pytest.mark.anyio

TOOLS = {"search_products", "get_product", "get_inventory_position", "explain_price"}


def text(result: Any) -> str:
    return str(result.content[0].text)


def gbp(value: str) -> str:
    """'13.00' -> '£13.00', '-1.50' -> '-£1.50' (how summaries show money)."""
    return f"-£{value[1:]}" if value.startswith("-") else f"£{value}"


# ------------------------------------------------------------------ two audiences
async def test_model_reads_a_summary_and_programs_get_full_data(client: Client) -> None:
    result = await client.call_tool("search_products", {"query": "trap"})
    assert not text(result).lstrip().startswith("{"), "content should be a summary, not JSON"
    assert result.structured_content is not None
    assert set(result.structured_content) == {"items", "count", "truncated"}


async def test_search_summary_keeps_every_fact(client: Client) -> None:
    result = await client.call_tool("search_products", {"limit": 50})
    summary, data = text(result), result.structured_content
    assert data is not None and data["count"] == 50
    for item in data["items"]:
        assert item["sku"] in summary
        assert gbp(item["base_price"]) in summary and gbp(item["cost"]) in summary
        assert f"{item['margin_pct']}%" in summary
    assert "Narrow the search" in summary  # truncated=True must tell the model what to do


async def test_explain_summary_keeps_every_rule(client: Client) -> None:
    result = await client.call_tool("explain_price", {"sku": "TRAP-RULES-001"})
    summary, data = text(result), result.structured_content
    assert data is not None
    assert gbp(data["final_price"]) in summary
    for step in data["applied"]:
        assert step["rule_id"] in summary and gbp(step["price_after"]) in summary
    for skipped in data["skipped"]:
        assert f"{skipped['rule_id']}: {skipped['reason']}" in summary


async def test_inventory_and_product_summaries_keep_stock_numbers(client: Client) -> None:
    inv = await client.call_tool("get_inventory_position", {"skus": ["TRAP-OVERSOLD-001"]})
    assert "TRAP-OVERSOLD-001 | 3 | 12 | 0 | OVERSOLD" in text(inv)
    product = await client.call_tool("get_product", {"sku": "TRAP-OVERSOLD-001"})
    assert "3 on hand, 12 reserved, 0 available (OVERSOLD)" in text(product)
    assert "before price rules" in text(product)  # base price is not the selling price


async def test_summaries_are_much_shorter_than_json(client: Client) -> None:
    calls: list[tuple[str, dict[str, Any]]] = [
        ("search_products", {"limit": 20}),
        ("explain_price", {"sku": "TRAP-RULES-001"}),
    ]
    for name, args in calls:
        result = await client.call_tool(name, args)
        as_json = json.dumps(result.structured_content, indent=2)
        assert len(text(result)) < 0.6 * len(as_json), name  # at least 40% smaller


async def test_empty_search_says_what_to_try(client: Client) -> None:
    result = await client.call_tool("search_products", {"query": "zzzz-no-such-thing"})
    assert not result.is_error
    assert text(result).startswith("No products matched")


# ------------------------------------------------------------------ errors
async def test_errors_start_with_a_stable_code_then_a_hint(client: Client) -> None:
    result = await client.call_tool("get_product", {"sku": "NOPE-001"})
    assert result.is_error
    first, second = text(result).splitlines()[:2]
    assert first.startswith("SKU_NOT_FOUND: ")
    assert second.startswith("Hint: ")
    assert result.meta is not None and result.meta[ERROR_CODE_META_KEY] == "SKU_NOT_FOUND"


async def test_invalid_arguments_name_the_problem(client: Client) -> None:
    result = await client.call_tool("search_products", {"min_margin_pct": 50, "max_margin_pct": 10})
    assert result.is_error
    assert text(result).startswith(
        "INVALID_ARGUMENTS: min_margin_pct cannot be greater than max_margin_pct"
    )
    assert result.meta is not None and result.meta[ERROR_CODE_META_KEY] == "INVALID_ARGUMENTS"


async def test_error_details_are_included_compactly(client: Client) -> None:
    result = await client.call_tool("get_inventory_position", {"skus": ["NOPE-001", "NOPE-002"]})
    assert result.is_error
    assert 'Details: {"missing":["NOPE-001","NOPE-002"]}' in text(result)


# ------------------------------------------------------------------ versions and caching
async def test_every_tool_declares_contract_version_and_risk_class(client: Client) -> None:
    for tool in (await client.list_tools()).tools:
        assert tool.meta == {"contract_version": "1", "risk_class": "read"}, tool.name


async def test_tool_list_carries_a_private_cache_hint(client: Client) -> None:
    listed = await client.list_tools()
    assert listed.ttl_ms == 300_000
    assert listed.cache_scope == "private"  # tool lists become per-user in Phase 7


# ------------------------------------------------------------------ description checklist
async def test_descriptions_follow_the_checklist(client: Client) -> None:
    tool_names = {t.name for t in (await client.list_tools()).tools}
    for tool in (await client.list_tools()).tools:
        description = tool.description or ""
        # Short enough to be cheap (it is sent with every model request), long enough to guide.
        assert 150 <= len(description) <= 600, (tool.name, len(description))
        assert "GBP" in description or "units" in description, f"{tool.name}: state units"
        # Every tool name mentioned in a description must exist.
        mentioned = set(re.findall(r"\b(?:search|get|explain)_[a-z_]+\b", description))
        assert mentioned <= tool_names, (tool.name, mentioned - tool_names)
        for name, schema in tool.input_schema["properties"].items():
            arg_description = schema.get("description", "")
            assert 20 <= len(arg_description) <= 200, (tool.name, name)
    assert tool_names == TOOLS
