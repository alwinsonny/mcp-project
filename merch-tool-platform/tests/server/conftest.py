"""Fixtures for MCP server tests.

`client` connects a real MCP client to the server object in memory: no subprocess,
no network, no database. It is the fastest way to test the protocol layer, and it
runs the same request handling a real host like Claude Desktop would.

`seeded_test_db` loads the test shop into the Postgres test database, for the tests
that start the real server as a separate process (stdio and HTTP).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from mcp import Client
from sqlalchemy.ext.asyncio import create_async_engine

from merch_platform.adapters.memory import InMemoryStore
from merch_platform.seed.generator import SeedData
from merch_platform.seed.load import load_seed
from merch_platform.server.app import ServicesFactory, create_server
from merch_platform.services.checks import FixedClock
from merch_platform.services.container import Services, build_services
from tests.conftest import AS_OF


def in_memory_factory(seed_data: SeedData) -> ServicesFactory:
    """A services factory over a fresh in-memory copy of the test shop."""

    @asynccontextmanager
    async def in_memory_services() -> AsyncIterator[Services]:
        yield build_services(InMemoryStore.from_seed(seed_data), FixedClock(AS_OF))

    return in_memory_services


@pytest.fixture
async def client(seed_data: SeedData) -> AsyncIterator[Client]:
    async with Client(create_server(in_memory_factory(seed_data))) as connected:
        yield connected


@pytest.fixture
def seeded_test_db(migrated_test_db: str, seed_data: SeedData) -> str:
    async def load() -> None:
        engine = create_async_engine(migrated_test_db)
        await load_seed(engine, seed_data)
        await engine.dispose()

    asyncio.run(load())
    return migrated_test_db
