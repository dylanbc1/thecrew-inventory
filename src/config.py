from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = "change-me"
    port: int = 8001
    database_url: str = "postgresql://localhost:5432/inventory"
    scrape_url: str = "https://www.thecrewautos.com/inventory/"
    cache_ttl_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
