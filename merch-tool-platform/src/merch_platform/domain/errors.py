"""Structured domain errors.

Error design is prompt design: in Phase 3 these become MCP tool errors that the
model reads. Every error therefore carries three things:

* code    - stable and machine-readable (tests, audit log and metrics use it)
* message - what went wrong, in one sentence
* hint    - what would succeed instead, so an agent can self-correct
"""

from __future__ import annotations

from typing import Any, ClassVar


class DomainError(Exception):
    code: ClassVar[str] = "DOMAIN_ERROR"

    def __init__(self, message: str, *, hint: str = "", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" Hint: {self.hint}" if self.hint else "")


class SkuNotFound(DomainError):
    code = "SKU_NOT_FOUND"


class SkuInactive(DomainError):
    code = "SKU_INACTIVE"


class CategoryNotFound(DomainError):
    code = "CATEGORY_NOT_FOUND"


class MarginFloorBreached(DomainError):
    code = "MARGIN_FLOOR"


class BlastRadiusExceeded(DomainError):
    code = "BLAST_RADIUS"


class ProtectedAttribute(DomainError):
    code = "PROTECTED_ATTRIBUTE"


class StockBelowReserved(DomainError):
    code = "STOCK_BELOW_RESERVED"


class PromotionConflict(DomainError):
    code = "PROMOTION_CONFLICT"


class PromotionBelowCost(DomainError):
    code = "PROMOTION_BELOW_COST"


class InvalidDateRange(DomainError):
    code = "INVALID_DATE_RANGE"


class OrderNotFound(DomainError):
    code = "ORDER_NOT_FOUND"


class AdjustmentExceedsPaid(DomainError):
    code = "ADJUSTMENT_EXCEEDS_PAID"


class NoChange(DomainError):
    code = "NO_CHANGE"
