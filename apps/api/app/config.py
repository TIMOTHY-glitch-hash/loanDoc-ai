"""Application settings.

Everything configurable lives here so no module reads ``os.environ`` directly -
that makes the settings trivially overridable in tests via ``get_settings``.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_env: Literal["development", "staging", "production"] = "development"
    api_version: str = "0.1.0"

    # Stored as a list; accepts the comma-separated form used in .env files.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    #: Per-request provider timeout. Long enough for a dense page, short enough
    #: that one stuck call does not hold an upload open.
    openai_timeout_seconds: float = Field(default=60.0, gt=0)
    openai_max_retries: int = Field(default=2, ge=0)

    #: Pages are extracted concurrently; the cap exists for provider rate limits.
    extraction_page_concurrency: int = Field(default=5, ge=1, le=50)
    extraction_max_pages: int = Field(default=40, ge=1)

    review_confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def agent_enabled(self) -> bool:
        """The UI degrades gracefully when no provider key is configured."""
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process."""
    return Settings()
