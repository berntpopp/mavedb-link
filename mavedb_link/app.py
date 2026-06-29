"""FastAPI host for mavedb-link (thin: health + service info)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mavedb_link import __version__
from mavedb_link.buildinfo import build_info
from mavedb_link.config import settings
from mavedb_link.logging_config import configure_logging
from mavedb_link.mcp.service_adapters import close_mavedb_service

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Start up, then close the shared MaveDB HTTP client on shutdown."""
    logger = configure_logging()
    logger.info("mavedb-link starting", host=settings.host, port=settings.port)
    try:
        yield
    finally:
        await close_mavedb_service()
        logger.info("mavedb-link shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="mavedb-link",
        description="MCP/API server grounding variant-effect work in MaveDB.",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Container Hardening Standard v1: never combine wildcard origins with
    # credentials. Browsers reject "*" + credentials and it is a security
    # footgun, so disable credentials whenever a wildcard origin is configured.
    allow_credentials = "*" not in settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Liveness probe (reports build provenance for deploy checks)."""
        return {
            "status": "ok",
            "service": "mavedb-link",
            "transport": "streamable-http-stateless",
            **build_info(),
        }

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Service information."""
        return {
            "name": "mavedb-link",
            "version": __version__,
            "data_source": "MaveDB public REST API (api.mavedb.org)",
            "mcp_endpoint": settings.mcp_path,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
