"""Phase 0 exit check: the migration builds every table, and the seed loads into it.

Skipped unless MERCH_TEST_DATABASE_URL is set (make test reads it from .env).
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

from merch_platform.db import tables as t
from merch_platform.seed.generator import generate
from merch_platform.seed.load import load_seed

pytestmark = pytest.mark.integration
URL = os.environ.get("MERCH_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/merch_platform/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="module")
def db_url() -> str:
    if not URL:
        pytest.skip("MERCH_TEST_DATABASE_URL not set")
    cfg = alembic_config(URL)
    command.downgrade(cfg, "base")  # start from nothing...
    command.upgrade(cfg, "head")  # ...and build every table
    return URL


def test_migration_creates_every_table(db_url: str) -> None:
    async def table_names() -> set[str]:
        engine = create_async_engine(db_url)
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        await engine.dispose()
        return names

    assert set(t.metadata.tables) <= asyncio.run(table_names())


def test_seed_loads_and_reloading_is_repeatable(db_url: str) -> None:
    data = generate(as_of=date(2026, 9, 23), n_products=300)

    async def load_twice_then_count() -> dict[str, int]:
        engine = create_async_engine(db_url)
        await load_seed(engine, data)
        await load_seed(engine, data)  # a second load must not duplicate anything
        async with engine.connect() as conn:
            counts = {
                name: (await conn.execute(select(func.count()).select_from(table))).scalar_one()
                for name, table in [
                    ("products", t.products),
                    ("prices", t.prices),
                    ("stock", t.stock),
                    ("orders", t.orders),
                ]
            }
        await engine.dispose()
        return counts

    counts = asyncio.run(load_twice_then_count())
    assert counts == {
        "products": len(data.products),
        "prices": len(data.prices),
        "stock": len(data.stock),
        "orders": len(data.orders),
    }
