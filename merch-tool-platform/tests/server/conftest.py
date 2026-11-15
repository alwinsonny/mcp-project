"""Fixtures for MCP server tests.

`client` connects a real MCP client to the server object in memory: no subprocess,
no network, no database. It is the fastest way to test the protocol layer, and it
runs the same request handling a real host like Claude Desktop would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from mcp import Client

from merch_platform.adapters.memory import InMemoryStore
from merch_platform.seed.generator import SeedData
from merch_platform.server.app import create_server
from merch_platform.services.checks import FixedClock
from merch_platform.services.container import Services, build_services
from tests.conftest import AS_OF


@pytest.fixture
async def client(seed_data: SeedData) -> AsyncIterator[Client]:
    @asynccontextmanager
    async def in_memory_services() -> AsyncIterator[Services]:
        yield build_services(InMemoryStore.from_seed(seed_data), FixedClock(AS_OF))

    async with Client(create_server(in_memory_services)) as connected:
        yield connected
