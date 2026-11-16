"""End-to-end: launch `python -m merch_platform.server` exactly as Claude Desktop does.

This proves three things the in-memory tests cannot:
* the module starts as a separate process and speaks MCP over stdin/stdout,
* nothing pollutes stdout (a stray print would break the protocol here),
* the real Postgres store answers through the server.
"""

from __future__ import annotations

import os
import sys

import pytest
from mcp import Client, StdioServerParameters

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_server_runs_over_stdio_against_postgres(seeded_test_db: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "merch_platform.server"],
        env={**os.environ, "MERCH_DATABASE_URL": seeded_test_db},
    )
    async with Client(params) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {
            "search_products",
            "get_product",
            "get_inventory_position",
            "explain_price",
        }

        result = await client.call_tool("explain_price", {"sku": "TRAP-RULES-001"})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["final_price"] == "13.00"

        # Phase 4: resources and prompts work in a real process against Postgres too.
        taxonomy = await client.read_resource("catalog://taxonomy")
        assert '"id":"C-110","name":"Bath towels"' in taxonomy.contents[0].text  # type: ignore[union-attr]
        prompt = await client.get_prompt("weekly_margin_review", {"category_id": "C-110"})
        assert "C-110 (Bath towels)" in prompt.messages[0].content.text  # type: ignore[union-attr]
