"""CLI:  uv run python -m merch_platform.seed [--as-of YYYY-MM-DD] [--products N] [--seed N]"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine

from merch_platform.config import get_settings
from merch_platform.seed.generator import generate
from merch_platform.seed.load import load_seed


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Wipe and reseed the commerce database.")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=None, help="reference date (default: today)"
    )
    parser.add_argument("--products", type=int, default=settings.seed_products)
    parser.add_argument("--seed", type=int, default=settings.seed)
    args = parser.parse_args()

    data = generate(seed=args.seed, as_of=args.as_of, n_products=args.products)
    engine = create_async_engine(settings.database_url)
    try:
        counts = await load_seed(engine, data)
    finally:
        await engine.dispose()
    print(f"Seeded as of {data.as_of} (seed={args.seed}); digest {data.digest()[:16]}")
    for table, n in counts.items():
        print(f"  {table:<12} {n:>6}")


if __name__ == "__main__":
    asyncio.run(main())
