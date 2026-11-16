"""The MCP server: four READ tools, three resources and three prompts.

Phase 2 built the server. Phase 3 made each tool a professional contract:
* results carry a compact summary for the model AND full structured data (results.py),
* errors start with a stable code and say what would work instead (results.py),
* descriptions follow one checklist (below) and are pinned by a snapshot test,
* every tool carries `contract_version` and `risk_class` in its _meta,
* tools/list carries a cache hint so clients stop re-fetching the catalogue.
Phase 4 added resources (resources.py) and prompts with autocomplete (prompts.py).

This module stays thin: each tool lets the SDK validate the arguments, calls a Phase 1
service, and passes the result to `respond`. All business rules stay in the services.

Description checklist (every tool and argument description follows it):
1. First sentence: what the tool does, in plain words.
2. When to use it, and which tool to use instead when it is the wrong one.
3. What comes back, with units (GBP, %) stated.
4. How to recover or continue (for example, "narrow the search").

Write tools (set_price, create_promotion, ...) are NOT exposed yet. They arrive in
Phase 8, once login, permissions and dry runs exist to make them safe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.caching import CacheableMethod, CacheHint
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import create_async_engine

from merch_platform.adapters.postgres import PostgresStore
from merch_platform.config import get_settings
from merch_platform.domain.commands import (
    InventoryPosition,
    PriceExplanation,
    ProductDetail,
    ProductQuery,
    ProductSearchResult,
)
from merch_platform.domain.models import DomainModel
from merch_platform.server import summaries
from merch_platform.server.prompts import register_prompts
from merch_platform.server.resources import register_resources
from merch_platform.server.results import protocol_error, respond
from merch_platform.services.container import Services, build_services

SERVER_NAME = "merch-tool-platform"
SERVER_VERSION = "0.4.0"  # project phase 4; clients see this in serverInfo
SERVER_INSTRUCTIONS = (
    "Read-only tools for a UK spa, bath and homewares shop's catalogue. All money is GBP. "
    "Start with search_products to find SKUs; the other tools need exact SKUs. "
    "Base price is before price rules; explain_price gives the price customers pay. "
    "Margin % = (price - cost) / price x 100. Reference data is available as resources "
    "(catalog://taxonomy, pricing://rules/active, promotions://calendar/YYYY-MM). "
    "Nothing in this server changes data yet."
)

# Hints for the client. They are NOT security: a client is free to ignore them.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def contract(version: str, risk_class: str) -> dict[str, Any]:
    """Tool _meta: which contract version this is (see docs/adr/0003) and its risk class."""
    return {"contract_version": version, "risk_class": risk_class}


# Lists may be cached by clients for 5 minutes; resource CONTENT for 1 minute, because
# prices and promotions change. All "private", not "public": from Phase 7 each user sees
# different things, so one user's cached result must never be served to another.
CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=300_000, scope="private"),
    "prompts/list": CacheHint(ttl_ms=300_000, scope="private"),
    "resources/list": CacheHint(ttl_ms=300_000, scope="private"),
    "resources/templates/list": CacheHint(ttl_ms=300_000, scope="private"),
    "resources/read": CacheHint(ttl_ms=60_000, scope="private"),
}


# ---------------------------------------------------------------- start-up and shut-down
@dataclass(frozen=True)
class AppContext:
    """Created once when the server starts; every tool call can reach it."""

    services: Services


ServicesFactory = Callable[[], AbstractAsyncContextManager[Services]]


@asynccontextmanager
async def postgres_services() -> AsyncIterator[Services]:
    """Default: real services backed by the Postgres database in your settings."""
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        yield build_services(PostgresStore(engine))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------- result wrappers
class InventoryPositions(DomainModel):
    """Wraps the list so the tool returns an object (structured output must be an object)."""

    positions: tuple[InventoryPosition, ...]


Sku = Annotated[
    str,
    Field(
        min_length=3,
        max_length=40,
        description="Exact SKU, case-sensitive, e.g. TW-00012. Find SKUs with search_products.",
    ),
]


# ---------------------------------------------------------------- the server
def create_server(services_factory: ServicesFactory = postgres_services) -> MCPServer:
    """Build the server. Tests pass a factory that returns in-memory services."""

    # Static resources and completions cannot receive Context (an SDK rule), so the
    # services are also kept here, filled in at start-up and cleared at shut-down.
    running: dict[str, Services] = {}

    def current_services() -> Services:
        if "services" not in running:
            raise protocol_error("NOT_READY", "The server is still starting.", "Try again.")
        return running["services"]

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        async with services_factory() as services:
            running["services"] = services
            try:
                yield AppContext(services=services)
            finally:
                running.pop("services", None)

    mcp = MCPServer(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
        cache_hints=CACHE_HINTS,
    )

    def services(ctx: Context[AppContext]) -> Services:
        return ctx.request_context.lifespan_context.services

    # -------------------------------------------------------------- search_products
    @mcp.tool(title="Search products", annotations=READ_ONLY, meta=contract("1", "read"))
    async def search_products(
        ctx: Context[AppContext],
        query: Annotated[
            str,
            Field(
                max_length=100,
                description="Words to find in the product name or SKU, e.g. 'sage towel'. "
                "Every word must appear. Leave empty to match all products.",
            ),
        ] = "",
        category_id: Annotated[
            str | None,
            Field(
                description="Only this category and its sub-categories, e.g. C-100 (Bath) "
                "or C-110 (Bath towels).",
            ),
        ] = None,
        min_margin_pct: Annotated[
            float | None,
            Field(ge=-1000, le=100, description="Only products with margin % at or above this."),
        ] = None,
        max_margin_pct: Annotated[
            float | None,
            Field(ge=-1000, le=100, description="Only products with margin % at or below this."),
        ] = None,
        include_disabled: Annotated[
            bool,
            Field(description="true to include disabled products. Default: active products only."),
        ] = False,
        limit: Annotated[
            int, Field(ge=1, le=50, description="Maximum products to return, 1 to 50.")
        ] = 20,
    ) -> Annotated[CallToolResult, ProductSearchResult]:
        """Find products by words in their name or SKU, optionally within a category and a
        margin % range. Use this first whenever you need SKUs: the other tools need exact
        SKUs. Returns products sorted by SKU with base price and cost in GBP and margin %.
        If more products match than were returned, narrow the search instead of raising
        the limit."""

        async def call() -> ProductSearchResult:
            # Built inside `respond`, so a rejected query (for example min_margin_pct above
            # max_margin_pct) becomes a readable INVALID_ARGUMENTS error.
            query_model = ProductQuery.model_validate(
                {
                    "query": query,
                    "category_id": category_id,
                    # via str() so 12.3 becomes Decimal("12.3"), not 12.300000000000000710...
                    "min_margin_pct": None if min_margin_pct is None else str(min_margin_pct),
                    "max_margin_pct": None if max_margin_pct is None else str(max_margin_pct),
                    "status": "any" if include_disabled else "active",
                    "limit": limit,
                }
            )
            return await services(ctx).catalogue.search(query_model)

        return await respond(call, summaries.search)

    # -------------------------------------------------------------- get_product
    @mcp.tool(title="Get product", annotations=READ_ONLY, meta=contract("1", "read"))
    async def get_product(
        ctx: Context[AppContext], sku: Sku
    ) -> Annotated[CallToolResult, ProductDetail]:
        """Get everything about one product by exact SKU: name, category, status, attributes
        (such as size, colour or scent), base price and cost in GBP, margin %, and stock on
        hand and reserved. The base price is before price rules: use explain_price for the
        price customers actually pay. Don't know the SKU? Use search_products first."""
        return await respond(lambda: services(ctx).catalogue.get_product(sku), summaries.product)

    # -------------------------------------------------------------- get_inventory_position
    @mcp.tool(title="Get inventory position", annotations=READ_ONLY, meta=contract("1", "read"))
    async def get_inventory_position(
        ctx: Context[AppContext],
        skus: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=50,
                description='1 to 50 exact SKUs, e.g. ["TW-00012", "CD-00031"].',
            ),
        ],
    ) -> Annotated[CallToolResult, InventoryPositions]:
        """Check stock for 1 to 50 SKUs in one call: units on hand, units reserved for
        customers' open orders, and units available to sell (on hand minus reserved, never
        below zero). Oversold means more units are reserved than are on hand, so some open
        orders cannot be fulfilled. A SKU with no stock record reports zeros."""

        async def call() -> InventoryPositions:
            positions = await services(ctx).inventory.get_positions(skus)
            return InventoryPositions(positions=tuple(positions))

        return await respond(call, lambda result: summaries.inventory(result.positions))

    # -------------------------------------------------------------- explain_price
    @mcp.tool(title="Explain price", annotations=READ_ONLY, meta=contract("1", "read"))
    async def explain_price(
        ctx: Context[AppContext],
        sku: Sku,
        on: Annotated[
            date | None,
            Field(description="Date to price on, YYYY-MM-DD, e.g. 2026-11-27. Default: today."),
        ] = None,
    ) -> Annotated[CallToolResult, PriceExplanation]:
        """Explain the price customers pay for a SKU on a date (default today): the base
        price, each catalogue price rule that applied in order with the price before and
        after, every rule that was skipped and why, and the final price and margin %. Use
        it when asked why something costs what it does, or what it will cost on a future
        date. Rules run lowest priority number first; one marked 'stop further rules' ends
        the calculation. Money in GBP."""
        return await respond(
            lambda: services(ctx).pricing.explain_price(sku, on), summaries.price_explanation
        )

    register_resources(mcp, current_services)
    register_prompts(mcp, current_services)
    return mcp


# The object the MCP Inspector and `python -m merch_platform.server` run.
mcp = create_server()
