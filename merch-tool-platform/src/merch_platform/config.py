"""Typed configuration from environment variables (prefix MERCH_) or a .env file."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERCH_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://merch:merch@localhost:5432/merch"
    margin_floor_pct: Decimal = Field(default=Decimal("10"), ge=0, lt=100)
    seed: int = 42
    seed_products: int = Field(default=5000, ge=100, le=50000)

    # Phase 6: running over HTTP (python -m merch_platform.server.http).
    # 127.0.0.1 means "this computer only": nothing else on your network can connect.
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)
    # A name for this copy of the server, shown in logs and the X-Merch-Instance header.
    instance_name: str = ""
    # Comma-separated secret keys that seal confirmation round trips. EVERY copy of the
    # server must have the same keys, or a confirmation started on one copy fails on
    # another. The first key seals; all keys unseal (so keys can be rotated).
    # Make one with: make keygen. Empty = a random key per process (one copy only).
    request_state_keys: SecretStr = SecretStr("")

    def request_state_key_list(self) -> list[str]:
        raw = self.request_state_keys.get_secret_value()
        return [key.strip() for key in raw.split(",") if key.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
