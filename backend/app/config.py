from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="NOVA AGENT API", min_length=1, max_length=100)
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None


class CamelModel(BaseModel):
    """Base model with camelCase serialization for API boundary."""

    model_config = SettingsConfigDict(
        populate_by_name=True,
        alias_generator=lambda s: "".join(w.capitalize() if i else w for i, w in enumerate(s.split("_"))),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
