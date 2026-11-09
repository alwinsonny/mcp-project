"""Deterministic seed data: a realistic spa, bath and homewares shop.

Same (seed, as_of, n_products) always produces byte-identical data; tests check this
with digest(). Dates are relative to `as_of` so promotions stay "next month" whenever
you reseed.

The data is deliberately awkward. The fixed TRAP-* records are documented in
docs/data-traps.md and each one backs at least one test.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from merch_platform.domain.models import (
    AttributeValue,
    Category,
    Order,
    OrderLine,
    PriceRecord,
    PriceRule,
    Product,
    Promotion,
    RuleAction,
    RuleCondition,
    StockLevel,
    money,
)

DEFAULT_SEED = 42
DEFAULT_PRODUCTS = 5000

TOP_CATEGORIES = [
    ("C-100", "Bath"),
    ("C-200", "Bedding"),
    ("C-300", "Spa & Wellness"),
    ("C-400", "Skincare"),
    ("C-500", "Home Fragrance"),
    ("C-600", "Kitchen & Dining"),
]

# leaf id, parent id, name, SKU prefix, product noun, price range (GBP), attribute family
LEAVES = [
    ("C-110", "C-100", "Bath towels", "TW", "Bath Towel", (12, 45), "textile"),
    ("C-120", "C-100", "Bathrobes", "BR", "Bathrobe", (35, 120), "textile"),
    ("C-130", "C-100", "Bath mats", "BM", "Bath Mat", (15, 40), "textile"),
    ("C-140", "C-100", "Hand towels", "HT", "Hand Towel", (6, 18), "textile"),
    ("C-210", "C-200", "Duvet covers", "DC", "Duvet Cover", (30, 140), "textile"),
    ("C-220", "C-200", "Pillows", "PL", "Pillow", (15, 70), "textile"),
    ("C-230", "C-200", "Fitted sheets", "FS", "Fitted Sheet", (18, 60), "textile"),
    ("C-240", "C-200", "Throws", "TH", "Throw", (25, 90), "textile"),
    ("C-310", "C-300", "Massage oils", "MO", "Massage Oil", (9, 35), "cosmetic"),
    ("C-320", "C-300", "Bath salts", "BS", "Bath Salts", (7, 25), "cosmetic"),
    ("C-330", "C-300", "Candles", "CD", "Candle", (8, 48), "candle"),
    ("C-340", "C-300", "Diffusers", "DF", "Aroma Diffuser", (25, 85), "kitchen"),
    ("C-410", "C-400", "Cleansers", "CL", "Cleanser", (10, 38), "cosmetic"),
    ("C-420", "C-400", "Moisturisers", "MS", "Moisturiser", (14, 55), "cosmetic"),
    ("C-430", "C-400", "Face masks", "FM", "Face Mask", (8, 30), "cosmetic"),
    ("C-440", "C-400", "Body lotions", "BL", "Body Lotion", (9, 32), "cosmetic"),
    ("C-510", "C-500", "Reed diffusers", "RD", "Reed Diffuser", (15, 55), "candle"),
    ("C-520", "C-500", "Room sprays", "RS", "Room Spray", (8, 28), "cosmetic"),
    ("C-530", "C-500", "Wax melts", "WM", "Wax Melts", (4, 14), "candle"),
    ("C-610", "C-600", "Mugs", "MG", "Mug", (6, 22), "kitchen"),
    ("C-620", "C-600", "Teapots", "TP", "Teapot", (18, 65), "kitchen"),
    ("C-630", "C-600", "Tea towels", "TT", "Tea Towel", (5, 15), "textile"),
]

ADJECTIVES = [
    "Organic",
    "Luxury",
    "Classic",
    "Signature",
    "Essential",
    "Heritage",
    "Pure",
    "Coastal",
]
COLOURS = ["White", "Charcoal", "Sage", "Oat", "Navy", "Blush", "Stone", "Ochre"]
SIZES = ["Small", "Medium", "Large", "XL"]
MATERIALS = ["cotton", "linen", "bamboo", "waffle cotton", "Turkish cotton"]
SCENTS = ["Lavender", "Eucalyptus", "Sandalwood", "Neroli", "Fig", "Sea Salt", "Jasmine", "Vetiver"]


@dataclass
class SeedData:
    as_of: date
    categories: list[Category] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    prices: list[PriceRecord] = field(default_factory=list)
    rules: list[PriceRule] = field(default_factory=list)
    stock: list[StockLevel] = field(default_factory=list)
    promotions: list[Promotion] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)

    def digest(self) -> str:
        """SHA-256 over every record, in order. Equal digests mean identical data."""
        payload = {
            name: [item.model_dump(mode="json") for item in getattr(self, name)]
            for name in (
                "categories",
                "products",
                "prices",
                "rules",
                "stock",
                "promotions",
                "orders",
            )
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


def _price(rng: random.Random, low: int, high: int) -> Decimal:
    pounds = rng.randint(low, high)
    return Decimal(f"{pounds}.{rng.choice(['00', '50', '95', '99'])}")


def _attributes(rng: random.Random, family: str) -> tuple[str, dict[str, AttributeValue]]:
    """Returns (name suffix, attributes)."""
    if family == "textile":
        colour, size, material = rng.choice(COLOURS), rng.choice(SIZES), rng.choice(MATERIALS)
        return f"{colour}, {size}", {
            "colour": colour,
            "size": size,
            "material": material,
            "gsm": rng.choice([400, 500, 600, 700]),
        }
    if family == "cosmetic":
        scent = rng.choice(SCENTS)
        volume = rng.choice([50, 100, 200, 250, 500])
        return f"{scent}, {volume}ml", {
            "scent": scent,
            "volume_ml": volume,
            "vegan": rng.random() < 0.6,
        }
    if family == "candle":
        scent = rng.choice(SCENTS)
        return scent, {"scent": scent, "burn_time_hours": rng.choice([20, 35, 45, 60])}
    colour = rng.choice(COLOURS)
    return colour, {
        "colour": colour,
        "capacity_ml": rng.choice([250, 350, 500, 1000]),
        "dishwasher_safe": rng.random() < 0.7,
    }


def _add_traps(data: SeedData) -> None:
    """Fixed, hand-designed awkward cases. See docs/data-traps.md."""
    d = data.as_of
    traps = [
        # sku, category, name, price, cost, status, on_hand, reserved
        (
            "TRAP-NEGMARGIN-001",
            "C-110",
            "Trap Towel (cost above price)",
            "8.00",
            "9.50",
            "active",
            50,
            0,
        ),
        ("TRAP-OVERSOLD-001", "C-330", "Trap Candle (oversold)", "18.00", "6.00", "active", 3, 12),
        (
            "TRAP-DISABLED-001",
            "C-220",
            "Trap Pillow (disabled)",
            "25.00",
            "10.00",
            "disabled",
            10,
            0,
        ),
        (
            "TRAP-RULES-001",
            "C-610",
            "Trap Mug (three overlapping rules)",
            "20.00",
            "8.00",
            "active",
            40,
            2,
        ),
        (
            "TRAP-THINMARGIN-001",
            "C-240",
            "Trap Throw (10.5% margin)",
            "50.00",
            "44.75",
            "active",
            20,
            1,
        ),
    ]
    for sku, cat, name, price, cost, status, on_hand, reserved in traps:
        data.products.append(
            Product(sku=sku, name=name, category_id=cat, status=status, attributes={"trap": True})
        )
        data.prices.append(PriceRecord(sku=sku, base_price=Decimal(price), cost=Decimal(cost)))
        data.stock.append(StockLevel(sku=sku, on_hand=on_hand, reserved=reserved))

    only = ("TRAP-RULES-001",)
    data.rules += [
        PriceRule(
            id="R-T01",
            name="Trap: 20% off",
            priority=5,
            active_from=d - timedelta(days=5),
            condition=RuleCondition(skus=only),
            action=RuleAction(kind="percent_off", amount=Decimal("20")),
        ),
        PriceRule(
            id="R-T02",
            name="Trap: £3 off, stop further rules",
            priority=10,
            stop_further=True,
            active_from=d - timedelta(days=5),
            condition=RuleCondition(skus=only),
            action=RuleAction(kind="fixed_off", amount=Decimal("3")),
        ),
        PriceRule(
            id="R-T03",
            name="Trap: 50% off (never reached)",
            priority=20,
            active_from=d - timedelta(days=5),
            condition=RuleCondition(skus=only),
            action=RuleAction(kind="percent_off", amount=Decimal("50")),
        ),
    ]
    data.promotions += [
        Promotion(
            id="PROMO-SPA-EXCL",
            code="SPA-EXCL",
            name="Diffuser event (exclusive)",
            discount_pct=Decimal("15"),
            category_ids=("C-340",),
            starts=d + timedelta(days=30),
            ends=d + timedelta(days=44),
            exclusive=True,
        ),
        Promotion(
            id="PROMO-TOWEL10",
            code="TOWEL10",
            name="Towel week",
            discount_pct=Decimal("10"),
            category_ids=("C-110",),
            starts=d + timedelta(days=5),
            ends=d + timedelta(days=20),
        ),
    ]
    data.orders.append(
        Order(
            order_id="ORD-TRAP-001",
            placed_on=d - timedelta(days=3),
            lines=(OrderLine(sku="TRAP-RULES-001", qty=2, unit_price_paid=Decimal("15.00")),),
            adjustments_total=Decimal("25.00"),
        )
    )


def generate(
    seed: int = DEFAULT_SEED, as_of: date | None = None, n_products: int = DEFAULT_PRODUCTS
) -> SeedData:
    as_of = as_of or date.today()
    rng = random.Random(seed)
    data = SeedData(as_of=as_of)

    data.categories = [Category(id=cid, name=name) for cid, name in TOP_CATEGORIES]
    data.categories += [
        Category(id=cid, name=name, parent_id=parent) for cid, parent, name, *_ in LEAVES
    ]

    # ---------------------------------------------------------------- products, prices, stock
    per_leaf: dict[str, list[str]] = {leaf[0]: [] for leaf in LEAVES}
    for i in range(1, n_products + 1):
        cid, _parent, _cname, prefix, noun, (low, high), family = rng.choice(LEAVES)
        sku = f"{prefix}-{i:05d}"
        suffix, attributes = _attributes(rng, family)
        name = f"{rng.choice(ADJECTIVES)} {noun} - {suffix}"
        status = "disabled" if rng.random() < 0.02 else "active"
        price = _price(rng, low, high)
        cost = money(price * Decimal(str(round(rng.uniform(0.35, 0.72), 3))))

        data.products.append(
            Product(sku=sku, name=name, category_id=cid, status=status, attributes=attributes)
        )
        data.prices.append(PriceRecord(sku=sku, base_price=price, cost=cost))

        on_hand = 0 if rng.random() < 0.08 else rng.randint(1, 400)
        reserved = rng.randint(0, min(on_hand, 40))
        if rng.random() < 0.01:
            reserved = on_hand + rng.randint(1, 5)  # a few naturally oversold SKUs
        data.stock.append(StockLevel(sku=sku, on_hand=on_hand, reserved=reserved))
        if status == "active":
            per_leaf[cid].append(sku)

    # ---------------------------------------------------------------- price rules
    d = as_of
    skincare_pool = per_leaf["C-420"] or per_leaf["C-410"]
    clearance_skus = tuple(sorted(rng.sample(skincare_pool, k=min(3, len(skincare_pool)))))
    data.rules = [
        PriceRule(
            id="R-001",
            name="Bath textiles 10% off",
            priority=10,
            active_from=d - timedelta(days=30),
            active_to=d + timedelta(days=60),
            condition=RuleCondition(category_ids=("C-110", "C-140")),
            action=RuleAction(kind="percent_off", amount=Decimal("10")),
        ),
        PriceRule(
            id="R-002",
            name="Candles £2 off",
            priority=20,
            active_from=d - timedelta(days=10),
            condition=RuleCondition(category_ids=("C-330",)),
            action=RuleAction(kind="fixed_off", amount=Decimal("2")),
        ),
        PriceRule(
            id="R-003",
            name="Premium duvet covers 15% off",
            priority=15,
            active_from=d - timedelta(days=7),
            active_to=d + timedelta(days=21),
            condition=RuleCondition(category_ids=("C-210",), min_base_price=Decimal("60")),
            action=RuleAction(kind="percent_off", amount=Decimal("15")),
        ),
        PriceRule(
            id="R-004",
            name="Moisturiser clearance at £5",
            priority=5,
            active_from=d - timedelta(days=2),
            active_to=d + timedelta(days=12),
            condition=RuleCondition(skus=clearance_skus),
            action=RuleAction(kind="set_price", amount=Decimal("5.00")),
        ),
        PriceRule(
            id="R-005",
            name="Expired summer oils sale",
            priority=10,
            active_from=d - timedelta(days=120),
            active_to=d - timedelta(days=60),
            condition=RuleCondition(category_ids=("C-310",)),
            action=RuleAction(kind="percent_off", amount=Decimal("25")),
        ),
        PriceRule(
            id="R-006",
            name="Future Black Friday bath",
            priority=1,
            active_from=d + timedelta(days=50),
            active_to=d + timedelta(days=55),
            condition=RuleCondition(category_ids=("C-110", "C-120", "C-130", "C-140")),
            action=RuleAction(kind="percent_off", amount=Decimal("30")),
        ),
        PriceRule(
            id="R-007",
            name="Mugs 5% off, stop further rules",
            priority=30,
            stop_further=True,
            active_from=d - timedelta(days=14),
            condition=RuleCondition(category_ids=("C-610",)),
            action=RuleAction(kind="percent_off", amount=Decimal("5")),
        ),
        PriceRule(
            id="R-008",
            name="Mugs extra 10% (blocked by R-007)",
            priority=40,
            active_from=d - timedelta(days=14),
            condition=RuleCondition(category_ids=("C-610",)),
            action=RuleAction(kind="percent_off", amount=Decimal("10")),
        ),
    ]

    # ---------------------------------------------------------------- orders
    active_skus = [p.sku for p in data.products if p.status == "active"]
    price_of = {p.sku: p.base_price for p in data.prices}
    for n in range(1, 401):
        lines = tuple(
            OrderLine(sku=sku, qty=rng.randint(1, 3), unit_price_paid=price_of[sku])
            for sku in sorted(rng.sample(active_skus, k=rng.randint(1, 4)))
        )
        order = Order(
            order_id=f"ORD-{n:06d}", placed_on=d - timedelta(days=rng.randint(1, 90)), lines=lines
        )
        if rng.random() < 0.1:
            order = order.model_copy(
                update={
                    "adjustments_total": money(
                        order.total_paid * Decimal(str(rng.choice([0.05, 0.1, 0.2])))
                    )
                }
            )
        data.orders.append(order)

    _add_traps(data)
    return data
