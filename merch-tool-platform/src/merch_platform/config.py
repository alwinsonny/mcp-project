"""Typed configuration from environment variables (prefix MERCH_) or a .env file."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERCH_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://merch:merch@localhost:5432/merch"
    margin_floor_pct: Decimal = Field(default=Decimal("10"), ge=0, lt=100)
    seed: int = 42
    seed_products: int = Field(default=5000, ge=100, le=50000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
