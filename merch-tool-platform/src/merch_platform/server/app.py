"""Phase 2: the MCP server, exposing the four READ tools over stdio.

This module is deliberately thin. Every tool does three things only:
1. let the SDK validate the arguments (from the type hints below),
2. call a Phase 1 service,
3. return the service's Pydantic result, which the SDK sends back twice:
   as JSON text for the model, and as `structured_content` for programs.

All business rules stay in the services. If you find yourself writing an `if` about
prices or stock in this file, it belongs in a service instead.

Write tools (set_price, create_promotion, ...) are NOT exposed yet. They arrive in
Phase 8, once login, permissions and dry runs exist to make them safe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
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
from merch_platform.domain.errors import DomainError
from merch_platform.domain.models import DomainModel
from merch_platform.services.container import Services, build_services

SERVER_NAME = "merch-tool-platform"
SERVER_INSTRUCTIONS = (
    "Read-only tools for a spa, bath and homewares shop's catalogue. Prices are in GBP. "
    "Use search_products to find SKUs before asking about a specific one. "
    "Margin % = (price - cost) / price. Nothing in this server changes data yet."
)

# Hints for the client. They are NOT security: a client is free to ignore them.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


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


async def _run[T](call: Callable[[], Awaitable[T]]) -> T:
    """Run a service call, turning business errors into errors the model can read.

    A ToolError comes back to the client as a normal result with is_error=True, so the
    model sees the message and hint and can correct itself (for example, search for a
    valid SKU and try again). Any other exception would be an unexplained failure.
    """
    try:
        return await call()
    except DomainError as err:
        raise ToolError(str(err)) from err  # "CODE: message Hint: what would work"
    except ValidationError as err:
        problems = "; ".join(e["msg"] for e in err.errors())
        raise ToolError(f"INVALID_ARGUMENTS: {problems}") from err


# ---------------------------------------------------------------- result wrappers
class InventoryPositions(DomainModel):
    """Wraps the list so the tool returns an object (structured output must be an object)."""

    positions: tuple[InventoryPosition, ...]


# ---------------------------------------------------------------- the server
def create_server(services_factory: ServicesFactory = postgres_services) -> MCPServer:
    """Build the server. Tests pass a factory that returns in-memory services."""

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        async with services_factory() as services:
            yield AppContext(services=services)

    mcp = MCPServer(SERVER_NAME, instructions=SERVER_INSTRUCTIONS, lifespan=lifespan)

    def services(ctx: Context[AppContext]) -> Services:
        return ctx.request_context.lifespan_context.services

    # -------------------------------------------------------------- search_products
    @mcp.tool(title="Search products", annotations=READ_ONLY)
    async def search_products(
        ctx: Context[AppContext],
        query: Annotated[
            str,
            Field(
                max_length=100,
                description="Words to find in the product name or SKU. Every word must match. "
                "Empty matches everything.",
            ),
        ] = "",
        category_id: Annotated[
            str | None,
            Field(
                description="Limit to a category and its sub-categories, e.g. C-110 (bath towels)."
            ),
        ] = None,
        min_margin_pct: Annotated[
            float | None,
            Field(ge=-1000, le=100, description="Only products at or above this margin %."),
        ] = None,
        max_margin_pct: Annotated[
            float | None,
            Field(ge=-1000, le=100, description="Only products at or below this margin %."),
        ] = None,
        include_disabled: Annotated[
            bool, Field(description="Also return disabled products. Default: active only.")
        ] = False,
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results, 1 to 50.")] = 20,
    ) -> ProductSearchResult:
        """Search the catalogue. Returns SKU, name, category, base price, cost and margin % for each
        product, sorted by SKU. If `truncated` is true there are more results: narrow the search."""

        async def call() -> ProductSearchResult:
            # Built INSIDE _run on purpose: ProductQuery can reject the request (for example
            # min_margin_pct above max_margin_pct), and _run turns that into a readable error.
            # Built outside, the model would only see "Error executing tool".
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

        return await _run(call)

    # -------------------------------------------------------------- get_product
    @mcp.tool(title="Get product", annotations=READ_ONLY)
    async def get_product(
        ctx: Context[AppContext],
        sku: Annotated[
            str, Field(min_length=3, max_length=40, description="Exact SKU, e.g. TW-00012.")
        ],
    ) -> ProductDetail:
        """Full details for one SKU: name, category, status, attributes (size, colour, scent...),
        base price, cost, margin % and stock (on hand and reserved)."""
        return await _run(lambda: services(ctx).catalogue.get_product(sku))

    # -------------------------------------------------------------- get_inventory_position
    @mcp.tool(title="Get inventory position", annotations=READ_ONLY)
    async def get_inventory_position(
        ctx: Context[AppContext],
        skus: Annotated[
            list[str], Field(min_length=1, max_length=50, description="1 to 50 exact SKUs.")
        ],
    ) -> InventoryPositions:
        """Stock for up to 50 SKUs: units on hand, reserved for open orders, and available to sell.
        `oversold` is true when more units are reserved than are physically on hand."""
        positions = await _run(lambda: services(ctx).inventory.get_positions(skus))
        return InventoryPositions(positions=tuple(positions))

    # -------------------------------------------------------------- explain_price
    @mcp.tool(title="Explain price", annotations=READ_ONLY)
    async def explain_price(
        ctx: Context[AppContext],
        sku: Annotated[str, Field(min_length=3, max_length=40, description="Exact SKU.")],
        on: Annotated[
            date | None, Field(description="Date to price on, YYYY-MM-DD. Default: today.")
        ] = None,
    ) -> PriceExplanation:
        """Explain how a SKU's selling price is calculated: the base price, each catalogue
        price rule that applied (in order, with the price before and after), every rule that
        was skipped and why, and the final price and margin. Rules run lowest priority number
        first; a rule marked 'stop further rules' ends the calculation."""
        return await _run(lambda: services(ctx).pricing.explain_price(sku, on))

    return mcp


# The object the MCP Inspector and `python -m merch_platform.server` run.
mcp = create_server()
