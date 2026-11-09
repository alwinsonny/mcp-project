from datetime import date

from merch_platform.seed.generator import DEFAULT_PRODUCTS, generate

AS_OF = date(2026, 9, 23)


def test_same_inputs_give_identical_data() -> None:
    assert (
        generate(as_of=AS_OF, n_products=300).digest()
        == generate(as_of=AS_OF, n_products=300).digest()
    )


def test_different_seed_gives_different_data() -> None:
    assert (
        generate(seed=1, as_of=AS_OF, n_products=300).digest()
        != generate(seed=2, as_of=AS_OF, n_products=300).digest()
    )


def test_default_size_and_unique_skus() -> None:
    data = generate(as_of=AS_OF)
    skus = [p.sku for p in data.products]
    assert len(skus) == DEFAULT_PRODUCTS + 5  # plus the five traps
    assert len(set(skus)) == len(skus)
    assert {p.sku for p in data.prices} == set(skus) == {s.sku for s in data.stock}


def test_all_documented_traps_exist() -> None:
    data = generate(as_of=AS_OF, n_products=100)
    skus = {p.sku for p in data.products}
    assert {
        "TRAP-NEGMARGIN-001",
        "TRAP-OVERSOLD-001",
        "TRAP-DISABLED-001",
        "TRAP-RULES-001",
        "TRAP-THINMARGIN-001",
    } <= skus
    assert {p.code for p in data.promotions} == {"SPA-EXCL", "TOWEL10"}
    assert any(o.order_id == "ORD-TRAP-001" for o in data.orders)


def test_every_category_reference_is_valid() -> None:
    data = generate(as_of=AS_OF, n_products=500)
    ids = {c.id for c in data.categories}
    assert all(p.category_id in ids for p in data.products)
    assert all(c.parent_id is None or c.parent_id in ids for c in data.categories)
