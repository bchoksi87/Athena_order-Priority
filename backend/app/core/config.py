"""Application settings (pydantic-settings, ``PPSE_`` environment prefix).

Settings are read from the process environment and an optional ``.env`` file.
Business rules never live here; they belong to :mod:`app.domain.config` and the
versioned configuration stored in the database. This module only carries
*deployment* concerns (URLs, secrets, feature switches, intervals).
"""

from __future__ import annotations

import json
import warnings
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.domain.enums import WritebackMode

Environment = Literal["dev", "test", "prod"]
SyntheticScale = Literal["small", "medium", "large"]

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ppse"
DEFAULT_DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"
API_PREFIX = "/api/v1"


def _split_csv(value: Any) -> list[str]:
    """Accept a JSON list, a comma-separated string or a list and return a clean list."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            parsed = json.loads(text)
            return [str(v).strip() for v in parsed if str(v).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(v).strip() for v in value if str(v).strip()]
    raise TypeError(f"cannot parse list value from {type(value).__name__}")


class Settings(BaseSettings):
    """Runtime configuration. Every field can be set as ``PPSE_<FIELD>``."""

    model_config = SettingsConfigDict(
        env_prefix="PPSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Production Priority & Scheduling Engine"
    app_version: str = "0.1.0"
    environment: Environment = "dev"

    database_url: str = DEFAULT_DATABASE_URL
    test_database_url: str | None = None
    database_echo: bool = False

    jwt_secret: str = DEFAULT_DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=8 * 60, ge=1)

    log_level: str = "INFO"
    log_json: bool = False

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    erp_connector: str = "mock"
    synthetic_seed: int = 42
    synthetic_scale: SyntheticScale = "small"
    writeback_mode: WritebackMode = WritebackMode.READ_ONLY

    background_jobs_enabled: bool = False
    sync_interval_minutes: int = Field(default=15, ge=1)
    replan_interval_minutes: int = Field(default=30, ge=1)

    seed_on_startup: bool = True
    currency: str = "INR"
    api_prefix: str = API_PREFIX

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, value: Any) -> list[str]:
        return _split_csv(value)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: Any) -> str:
        return str(value).upper()

    @field_validator("api_prefix")
    @classmethod
    def _prefix_shape(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _guard_prod_secret(self) -> Settings:
        if self.environment == "prod" and self.jwt_secret == DEFAULT_DEV_JWT_SECRET:
            warnings.warn(
                "PPSE_JWT_SECRET is the insecure development default in a prod environment",
                RuntimeWarning,
                stacklevel=2,
            )
        return self

    # ------------------------------------------------------------- helpers
    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"

    @property
    def uses_default_secret(self) -> bool:
        return self.jwt_secret == DEFAULT_DEV_JWT_SECRET

    @property
    def effective_database_url(self) -> str:
        """The URL the app should connect to: test DB in the test environment when set."""
        if self.environment == "test" and self.test_database_url:
            return self.test_database_url
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings. Tests call :func:`reset_settings_cache`."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached :class:`Settings` so the next call re-reads the environment."""
    get_settings.cache_clear()


__all__ = [
    "API_PREFIX",
    "DEFAULT_DATABASE_URL",
    "DEFAULT_DEV_JWT_SECRET",
    "Environment",
    "Settings",
    "SyntheticScale",
    "get_settings",
    "reset_settings_cache",
]
