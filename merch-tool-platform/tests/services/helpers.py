from merch_platform.seed.generator import SeedData


def active_skus(seed: SeedData, category_id: str, n: int) -> list[str]:
    skus = sorted(
        p.sku
        for p in seed.products
        if p.category_id == category_id and p.status == "active" and not p.sku.startswith("TRAP")
    )
    assert len(skus) >= n, f"seed has only {len(skus)} active SKUs in {category_id}"
    return skus[:n]
