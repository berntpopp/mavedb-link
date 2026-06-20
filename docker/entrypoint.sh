#!/usr/bin/env bash
# Ensure the local SQLite mirror is present, then start the server.
#
# `mavedb-link-data bootstrap` reuses an existing mirror, else pulls a prebuilt
# artifact, else (when MAVEDB_LINK_MIRROR__BUILD_LOCAL=true) builds from the
# Zenodo dump. It exits 0 even when no mirror is available, so the server always
# starts -- the live MaveDB API is the backup (a mirror-miss falls through to it).
set -euo pipefail

if [ "${MAVEDB_LINK_MIRROR__ENABLED:-true}" = "true" ]; then
    mavedb-link-data bootstrap \
        || echo "[entrypoint] mirror bootstrap failed; serving live-only" >&2
fi

exec python server.py \
    --transport "${MAVEDB_LINK_TRANSPORT:-unified}" \
    --host "${MAVEDB_LINK_HOST:-0.0.0.0}" \
    --port "${MAVEDB_LINK_PORT:-8000}"
