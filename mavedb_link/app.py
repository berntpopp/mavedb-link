"""FastAPI host for mavedb-link (thin: health + service info)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mavedb_link import __version__
from mavedb_link.buildinfo import build_info
from mavedb_link.config import settings
from mavedb_link.logging_config import configure_logging
from mavedb_link.mcp.service_adapters import close_mavedb_service
from mavedb_link.runtime_data_identity import (
    RuntimeDataIdentityError,
    runtime_identity_envelope,
    verify_settings_runtime_identity,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Verify a pinned mirror, then close the shared client on shutdown."""
    logger = configure_logging()
    logger.info("mavedb-link starting", host=settings.host, port=settings.port)
    _app.state.runtime_data_identity = None
    _app.state.runtime_data_identity_error = None
    try:
        _app.state.runtime_data_identity = verify_settings_runtime_identity(settings)
    except RuntimeDataIdentityError as exc:
        _app.state.runtime_data_identity_error = str(exc)
        logger.error("MaveDB runtime data identity verification failed", error=str(exc))
        raise
    except OSError as exc:
        logger.error("MaveDB runtime data identity verification failed", error=str(exc))
        raise RuntimeDataIdentityError("pinned MaveDB data identity is unavailable") from exc
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
    async def health() -> Any:
        """Report process liveness and, when pinned, the verified mirror identity."""
        result: dict[str, Any] = {
            "status": "ok",
            "service": "mavedb-link",
            "transport": "streamable-http-stateless",
            **build_info(),
        }
        expected_tag = settings.mirror.bundle_release_tag
        expected_digest = settings.mirror.bundle_expected_expanded_sha256
        if settings.mirror.enabled and expected_tag and expected_digest:
            actual = getattr(app.state, "runtime_data_identity", None)
            if actual is None:
                return JSONResponse(
                    {
                        **result,
                        "status": "degraded",
                        "data_available": False,
                        "reason": "pinned MaveDB data identity is unavailable",
                    },
                    status_code=503,
                )
            result.update(runtime_identity_envelope(expected_tag, expected_digest, actual))
        return result

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
