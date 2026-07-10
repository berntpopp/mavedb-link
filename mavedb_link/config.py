"""Configuration management for mavedb-link.

Settings load from environment variables with the ``MAVEDB_LINK_`` prefix (nested
models use ``__``, e.g. ``MAVEDB_LINK_API__BASE_URL=https://api.mavedb.org/api/v1``)
and an optional ``.env`` file. The only data source is the live MaveDB REST API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mavedb_link import __version__
from mavedb_link.constants import (
    DEFAULT_API_BASE_URL,
    MAPPED_CACHE_LRU_SETS,
    ZENODO_CONCEPT_ID,
)


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


class MirrorConfig(BaseModel):
    """Local SQLite mirror (primary source; the live API is the backup).

    When ``enabled`` and a built database exists at ``db_path``, reads are served
    from the mirror first and fall through to the live API on a miss. The ingest
    keys configure how the database is acquired (Zenodo bulk dump or a prebuilt
    GitHub Release artifact) and refreshed.
    """

    enabled: bool = Field(default=True, description="Use the mirror when a built DB exists.")
    data_dir: Path = Field(default=Path("data"), description="Directory holding the mirror DB.")
    db_filename: str = Field(default="mavedb.sqlite", description="Mirror SQLite filename.")
    zenodo_concept_id: str = Field(
        default=ZENODO_CONCEPT_ID,
        description="Zenodo concept record id for the MaveDB bulk dump (resolves 'latest').",
    )
    source_url: str | None = Field(
        default=None, description="Explicit dump-zip URL override (else resolved from Zenodo)."
    )
    refresh_ttl_days: int = Field(
        default=30, ge=0, description="Age beyond which the mirror is considered stale."
    )
    github_repo: str = Field(
        default="berntpopp/mavedb-link", description="Repo hosting prebuilt artifact releases."
    )
    bundle_url: str = Field(
        default="latest",
        description="Prebuilt artifact: 'latest', an explicit URL, or '' (disabled).",
    )
    bundle_asset_name: str = Field(
        default="mavedb.sqlite.zst", description="Release asset name for the prebuilt mirror."
    )
    bundle_expected_sha256: str | None = Field(
        default=None,
        description="Required SHA-256 for an explicit bundle URL when no valid sidecar exists.",
    )
    max_dump_bytes: int = Field(
        default=4 * 1024**3,
        gt=0,
        description="Maximum Zenodo dump bytes; 4 GiB is over twice the 1.8 GB measured 2026-07-10; override with MAVEDB_LINK_MIRROR__MAX_DUMP_BYTES.",
    )
    max_bundle_bytes: int = Field(
        default=2 * 1024**3,
        gt=0,
        description="Maximum compressed bundle bytes; 2 GiB exceeds twice the largest measured 2026-07-10; override with MAVEDB_LINK_MIRROR__MAX_BUNDLE_BYTES.",
    )
    max_database_bytes: int = Field(
        default=8 * 1024**3,
        gt=0,
        description="Maximum expanded database bytes; 8 GiB exceeds twice the largest measured 2026-07-10; override with MAVEDB_LINK_MIRROR__MAX_DATABASE_BYTES.",
    )
    max_archive_entries: int = Field(
        default=10_000,
        gt=0,
        description="Maximum dump archive entries; override with MAVEDB_LINK_MIRROR__MAX_ARCHIVE_ENTRIES.",
    )
    max_archive_member_bytes: int = Field(
        default=2 * 1024**3,
        gt=0,
        description="Maximum expanded archive member bytes; 2 GiB exceeds twice the largest measured 2026-07-10; override with MAVEDB_LINK_MIRROR__MAX_ARCHIVE_MEMBER_BYTES.",
    )
    max_archive_expanded_bytes: int = Field(
        default=16 * 1024**3,
        gt=0,
        description="Maximum total expanded archive bytes; 16 GiB exceeds twice the measured dump 2026-07-10; override with MAVEDB_LINK_MIRROR__MAX_ARCHIVE_EXPANDED_BYTES.",
    )
    max_metadata_bytes: int = Field(
        default=1024**2,
        gt=0,
        description="Maximum release metadata or checksum bytes; override with MAVEDB_LINK_MIRROR__MAX_METADATA_BYTES.",
    )
    max_download_seconds: float = Field(
        default=7200,
        gt=0,
        description="Maximum total artifact download seconds; override with MAVEDB_LINK_MIRROR__MAX_DOWNLOAD_SECONDS.",
    )
    build_local: bool = Field(
        default=False, description="Fall back to a local build if the prebuilt pull fails."
    )

    @property
    def db_path(self) -> Path:
        """Full path to the mirror SQLite file."""
        return self.data_dir / self.db_filename


class CacheSettings(BaseModel):
    """On-disk mapped-variant cache (lazy live-API backfill of the VRS layer).

    The Zenodo dump omits the per-set annotations CSVs, so the VRS/ClinGen layer
    is backfilled on demand: the first tool call that touches a score set fetches
    its mapped variants from the live API and writes them here, then repeats serve
    locally. Follows the fleet ``metadome-link`` ResultCache convention (SQLite +
    in-memory LRU front). Disabling it falls back to the live API on every call.
    """

    enabled: bool = Field(default=True, description="Persist lazy mapped-variant enrichment.")
    db_path: Path = Field(
        default=Path("data/mavedb_cache.sqlite"),
        description="Path to the on-disk cache SQLite (parent dir auto-created).",
    )
    lru_sets: int = Field(
        default=MAPPED_CACHE_LRU_SETS,
        ge=0,
        description="In-memory LRU size (score sets) in front of the on-disk cache.",
    )


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
    allowed_hosts: list[str] = Field(
        default=["localhost", "127.0.0.1", "::1"],
        description="Exact Host header values accepted by the request guard.",
    )
    allowed_origins: list[str] = Field(
        default=[],
        description="Browser Origin values accepted by the request guard.",
    )

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

    mirror: MirrorConfig = Field(
        default_factory=MirrorConfig,
        description="Local SQLite mirror configuration (primary source; live API backup).",
    )

    cache: CacheSettings = Field(
        default_factory=CacheSettings,
        description="On-disk mapped-variant cache (lazy live-API VRS backfill).",
    )

    @field_validator("mcp_path")
    @classmethod
    def validate_mcp_path(cls, v: str) -> str:
        """Ensure the MCP path starts with a forward slash."""
        return v if v.startswith("/") else f"/{v}"

    @field_validator("allowed_hosts", "allowed_origins", "cors_origins", mode="before")
    @classmethod
    def parse_string_list(cls, v: Any) -> list[str]:
        """Parse string lists from a comma-separated value or list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return list(v) if v else []

    @field_validator("allowed_hosts")
    @classmethod
    def reject_wildcard_host(cls, v: list[str]) -> list[str]:
        """Require exact hosts; pattern syntax makes the boundary ambiguous."""
        if any(any(marker in host for marker in "*?[]") for host in v):
            raise ValueError("wildcard patterns are not allowed in allowed_hosts")
        return v


settings = ServerSettings()
