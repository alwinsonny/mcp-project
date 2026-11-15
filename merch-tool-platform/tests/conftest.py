"""Shared fixtures.

`store` is parametrised: every test that uses it runs twice, once against the
in-memory adapter and once against real Postgres. That proves both adapters behave
identically. The Postgres run is skipped unless MERCH_TEST_DATABASE_URL is set, e.g.

    export MERCH_TEST_DATABASE_URL=postgresql+asyncpg://merch:merch@localhost:5432/merch_test
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from merch_platform.adapters.memory import InMemoryStore
from merch_platform.adapters.postgres import PostgresStore
from merch_platform.domain.commands import WriteLimits
from merch_platform.seed.generator import SeedData, generate
from merch_platform.seed.load import load_seed
from merch_platform.services.checks import FixedClock
from merch_platform.services.container import Services, Store, build_services

AS_OF = date(2026, 9, 23)
TEST_PRODUCTS = 400
TEST_DB_URL = os.environ.get("MERCH_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def seed_data() -> SeedData:
    return generate(seed=42, as_of=AS_OF, n_products=TEST_PRODUCTS)


@pytest.fixture(scope="session")
def migrated_test_db() -> str:
    """Run Alembic migrations once per test session against the test database."""
    if not TEST_DB_URL:
        pytest.skip("MERCH_TEST_DATABASE_URL not set")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/merch_platform/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_DB_URL


@pytest.fixture(
    params=["memory", pytest.param("postgres", marks=pytest.mark.integration)],
)
def backend(request: pytest.FixtureRequest) -> str | None:
    """None for the in-memory store, else the migrated Postgres URL.

    Kept synchronous on purpose: Alembic's env.py calls asyncio.run(), which must not
    happen inside the event loop that async fixtures run in.
    """
    if request.param == "memory":
        return None
    url: str = request.getfixturevalue("migrated_test_db")
    return url


@pytest.fixture
async def store(backend: str | None, seed_data: SeedData) -> AsyncIterator[Store]:
    if backend is None:
        yield InMemoryStore.from_seed(seed_data)
        return
    engine = create_async_engine(backend)
    await load_seed(engine, seed_data)
    try:
        yield PostgresStore(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def services(store: Store) -> Services:
    return build_services(store, FixedClock(AS_OF))


@pytest.fixture
def merchandiser() -> WriteLimits:
    return WriteLimits(max_skus=50, margin_floor_pct=Decimal("10"))


@pytest.fixture
def senior() -> WriteLimits:
    return WriteLimits(max_skus=500, margin_floor_pct=Decimal("10"))
