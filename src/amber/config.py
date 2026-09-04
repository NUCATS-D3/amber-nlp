"""Application settings resolved at process boundaries."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Amber configuration loaded from ``AMBER_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AMBER_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["critical", "error", "warning", "info", "debug", "trace"] = "info"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
