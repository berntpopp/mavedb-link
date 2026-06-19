"""Configuration management for mavedb-link.

Settings load from environment variables with the ``MAVEDB_LINK_`` prefix (nested
models use ``__``, e.g. ``MAVEDB_LINK_API__BASE_URL=https://api.mavedb.org/api/v1``)
and an optional ``.env`` file. The only data source is the live MaveDB REST API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mavedb_link import __version__
from mavedb_link.constants import DEFAULT_API_BASE_URL


class MaveDBApiConfig(BaseModel):
    """Upstream MaveDB REST API client configuration."""

    base_url: str = Field(
        default=DEFAULT_API_BASE_URL,
        description="MaveDB API base URL (the /api/v1 prefix is included).",
    )
    request_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Per-request HTTP timeout in seconds.",
    )
    max_concurrency: int = Field(
        default=5,
        ge=1,
        le=64,
        description="Max in-flight upstream requests (bounds burst pressure).",
    )
    max_retries: int = Field(
        default=4,
        ge=0,
        le=10,
        description="Retry attempts for transient (429/5xx/network) faults.",
    )
    cache_ttl: int = Field(
        default=600,
        ge=0,
        le=86400,
        description="In-process response cache TTL in seconds (0 disables).",
    )
    cache_size: int = Field(
        default=512,
        ge=0,
        le=65536,
        description="Max entries in the in-process response cache (0 disables).",
    )
    user_agent: str = Field(
        default=f"mavedb-link/{__version__} (+https://github.com/berntpopp/mavedb-link)",
        description="User-Agent sent to the MaveDB API.",
    )

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class ServerSettings(BaseSettings):
    """Top-level server settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="MAVEDB_LINK_",
        env_nested_delimiter="__",
    )

    host: str = Field(default="127.0.0.1", description="Server host.")
    port: int = Field(default=8000, ge=1024, le=65535, description="Server port.")
    reload: bool = Field(default=False, description="Enable auto-reload in development.")

    transport: Literal["unified", "http", "stdio"] = Field(
        default="unified",
        description="Server transport mode.",
    )
    mcp_path: str = Field(default="/mcp", description="MCP endpoint path.")

    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins.",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level.",
    )
    log_format: Literal["json", "console"] = Field(
        default="console",
        description="Log format.",
    )

    api: MaveDBApiConfig = Field(
        default_factory=MaveDBApiConfig,
        description="Upstream MaveDB API configuration.",
    )

    @field_validator("mcp_path")
    @classmethod
    def validate_mcp_path(cls, v: str) -> str:
        """Ensure the MCP path starts with a forward slash."""
        return v if v.startswith("/") else f"/{v}"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        """Parse CORS origins from a comma-separated string or list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return list(v) if v else []


settings = ServerSettings()
