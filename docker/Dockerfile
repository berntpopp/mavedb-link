# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for mavedb-link.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /home/app/web
COPY uv.lock pyproject.toml README.md ./

RUN pip install --upgrade pip uv && \
    uv sync --frozen --no-dev --active --no-install-project


FROM python:3.12-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    MAVEDB_LINK_HOST=0.0.0.0 \
    MAVEDB_LINK_PORT=8000 \
    MAVEDB_LINK_TRANSPORT=unified \
    MAVEDB_LINK_MCP_PATH=/mcp \
    TMPDIR=/tmp/mavedb-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/mavedb-link && \
    chown -R app:app /tmp/mavedb-link /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./mavedb_link ./mavedb_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./
COPY --chown=app:app ./docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN /opt/venv/bin/pip install . --no-deps && chmod +x /usr/local/bin/entrypoint.sh

# Stamp build provenance so get_server_capabilities/_meta can report which build
# answered. Pass at build time, e.g.:
#   docker build --build-arg MAVEDB_LINK_GIT_SHA=$(git rev-parse --short HEAD) \
#                --build-arg MAVEDB_LINK_BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
ARG MAVEDB_LINK_GIT_SHA=unknown
ARG MAVEDB_LINK_BUILT_AT
ENV MAVEDB_LINK_GIT_SHA=${MAVEDB_LINK_GIT_SHA} \
    MAVEDB_LINK_BUILT_AT=${MAVEDB_LINK_BUILT_AT}

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
