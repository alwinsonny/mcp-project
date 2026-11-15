"""Precondition checks shared by the services.

Each check raises a DomainError whose hint says what WOULD succeed. Lists of SKUs
in messages are capped so an error for 500 SKUs does not flood the model's context.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, date, datetime

from merch_platform.domain.commands import WriteLimits
from merch_platform.domain.errors import BlastRadiusExceeded, SkuInactive, SkuNotFound
from merch_platform.domain.models import Product

MAX_LISTED = 10


def preview(items: Collection[str]) -> str:
    ordered = sorted(items)
    shown = ", ".join(ordered[:MAX_LISTED])
    extra = len(ordered) - MAX_LISTED
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def require_skus(found: Mapping[str, object], requested: Collection[str]) -> None:
    missing = set(requested) - set(found)
    if missing:
        raise SkuNotFound(
            f"{len(missing)} SKU(s) not found: {preview(missing)}.",
            hint="Use search_products to find valid SKUs; SKUs are case-sensitive.",
            details={"missing": sorted(missing)},
        )


def require_active(products: Mapping[str, Product]) -> None:
    inactive = {sku for sku, p in products.items() if p.status != "active"}
    if inactive:
        raise SkuInactive(
            f"{len(inactive)} SKU(s) are disabled: {preview(inactive)}.",
            hint="Remove disabled SKUs from the request; they cannot be changed.",
            details={"inactive": sorted(inactive)},
        )


def check_blast_radius(count: int, limits: WriteLimits) -> None:
    if count > limits.max_skus:
        raise BlastRadiusExceeded(
            f"This change affects {count} SKUs; your limit is {limits.max_skus} per call.",
            hint=f"Split the change into batches of at most {limits.max_skus} SKUs.",
            details={"affected": count, "limit": limits.max_skus},
        )


class SystemClock:
    """Real clock. Uses UTC so the date never depends on the server's time zone."""

    def today(self) -> date:
        return datetime.now(UTC).date()


class FixedClock:
    """Test clock: always returns the same day."""

    def __init__(self, day: date) -> None:
        self._day = day

    def today(self) -> date:
        return self._day
