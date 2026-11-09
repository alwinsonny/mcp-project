"""Database schema, defined once with SQLAlchemy Core.

The first migration (db/migrations/versions/0001_initial.py) creates exactly these
tables. change_sets, idempotency_keys and audit_log stay empty until Phases 8 and 9,
but exist from day one so the schema is designed for auditing and undo up front.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

MONEY = Numeric(12, 2)

categories = Table(
    "categories",
    metadata,
    Column("id", String(20), primary_key=True),
    Column("name", String(100), nullable=False),
    Column("parent_id", String(20), ForeignKey("categories.id"), nullable=True),
)

products = Table(
    "products",
    metadata,
    Column("sku", String(40), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("category_id", String(20), ForeignKey("categories.id"), nullable=False, index=True),
    Column("status", String(10), nullable=False, server_default="active"),
    Column("attributes", JSONB, nullable=False, server_default="{}"),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_products_status"),
)

prices = Table(
    "prices",
    metadata,
    Column("sku", String(40), ForeignKey("products.sku", ondelete="CASCADE"), primary_key=True),
    Column("base_price", MONEY, nullable=False),
    Column("cost", MONEY, nullable=False),
    Column("currency", String(3), nullable=False, server_default="GBP"),
    CheckConstraint("base_price >= 0 AND cost >= 0", name="ck_prices_non_negative"),
)

price_rules = Table(
    "price_rules",
    metadata,
    Column("id", String(20), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("stop_further", Boolean, nullable=False, server_default="false"),
    Column("active_from", Date, nullable=False),
    Column("active_to", Date, nullable=True),
    Column("condition", JSONB, nullable=False, server_default="{}"),
    Column("action_kind", String(20), nullable=False),
    Column("action_amount", MONEY, nullable=False),
    CheckConstraint(
        "action_kind IN ('percent_off', 'fixed_off', 'set_price')", name="ck_price_rules_kind"
    ),
)

stock = Table(
    "stock",
    metadata,
    Column("sku", String(40), ForeignKey("products.sku", ondelete="CASCADE"), primary_key=True),
    Column("on_hand", Integer, nullable=False),
    Column("reserved", Integer, nullable=False, server_default="0"),
    CheckConstraint("on_hand >= 0 AND reserved >= 0", name="ck_stock_non_negative"),
)

promotions = Table(
    "promotions",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("code", String(20), nullable=False, unique=True),
    Column("name", String(100), nullable=False),
    Column("discount_pct", Numeric(5, 2), nullable=False),
    Column("category_ids", JSONB, nullable=False, server_default="[]"),
    Column("skus", JSONB, nullable=False, server_default="[]"),
    Column("starts", Date, nullable=False),
    Column("ends", Date, nullable=False),
    Column("exclusive", Boolean, nullable=False, server_default="false"),
    CheckConstraint("ends >= starts", name="ck_promotions_dates"),
    CheckConstraint("discount_pct > 0 AND discount_pct <= 90", name="ck_promotions_discount"),
)

orders = Table(
    "orders",
    metadata,
    Column("order_id", String(40), primary_key=True),
    Column("placed_on", Date, nullable=False),
    Column("adjustments_total", MONEY, nullable=False, server_default="0"),
)

order_lines = Table(
    "order_lines",
    metadata,
    Column(
        "order_id", String(40), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    ),
    Column("line_no", Integer, nullable=False),
    Column("sku", String(40), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("unit_price_paid", MONEY, nullable=False),
    PrimaryKeyConstraint("order_id", "line_no"),
)

order_adjustments = Table(
    "order_adjustments",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("order_id", String(40), ForeignKey("orders.order_id"), nullable=False, index=True),
    Column("amount", MONEY, nullable=False),
    Column("reason_code", String(30), nullable=False),
    Column("note", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# ---------------------------------------------------------------- used from Phase 8 / 9
change_sets = Table(
    "change_sets",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("principal", String(100), nullable=False),
    Column("tool", String(60), nullable=False),
    Column("contract_version", String(10), nullable=False),
    Column("plan", JSONB, nullable=False),
    Column("rows_hash", String(64), nullable=False),
    Column("status", String(12), nullable=False, server_default="previewed"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("applied_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "status IN ('previewed', 'applied', 'rolled_back', 'expired')", name="ck_change_sets_status"
    ),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("principal", String(100), nullable=False),
    Column("key", String(100), nullable=False),
    Column("tool", String(60), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("result", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("principal", "key"),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("correlation_id", String(64), nullable=False),
    Column("principal", String(100), nullable=False),
    Column("tool", String(60), nullable=False),
    Column("contract_version", String(10), nullable=False),
    Column("args_hash", String(64), nullable=False),
    Column("outcome", String(20), nullable=False),
    Column("reason", String(60), nullable=True),
    Column("prev_hash", String(64), nullable=False),
    Column("row_hash", String(64), nullable=False),
)
