#!/usr/bin/env bash
# mavedb-link has no local data to build (the MaveDB API is the live source), so
# the entrypoint simply starts the server.
set -euo pipefail

exec python server.py \
    --transport "${MAVEDB_LINK_TRANSPORT:-unified}" \
    --host "${MAVEDB_LINK_HOST:-0.0.0.0}" \
    --port "${MAVEDB_LINK_PORT:-8000}"
