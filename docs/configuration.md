# Configuration

All settings use the `MAVEDB_LINK_` prefix. **Nested config uses a double underscore** —
`MAVEDB_LINK_API__BASE_URL`, `MAVEDB_LINK_MIRROR__ENABLED`. Values load from the environment
or a `.env` file; see [`.env.example`](../.env.example) and
[`.env.docker.example`](../.env.docker.example).

## Transport

| Variable | Default | Notes |
|----------|---------|-------|
| `MAVEDB_LINK_TRANSPORT` | `unified` | `unified` \| `http` \| `stdio` |
| `MAVEDB_LINK_HOST` | `127.0.0.1` | `0.0.0.0` in the container |
| `MAVEDB_LINK_PORT` | `8000` | 1024–65535 |
| `MAVEDB_LINK_MCP_PATH` | `/mcp` | A leading `/` is added if omitted |

**Transport footgun.** `--transport unified` serves the REST/health surface **and** MCP at
`/mcp`. `--transport http` is REST/health **only** — it exposes no MCP endpoint, so the router
and every MCP client will fail against it. Router/MCP deployments must use `unified`.
`stdio` is served by a different entrypoint file: `uv run python mcp_server.py`
(console script `mavedb-link-mcp`), used by Claude Desktop.

## Request guard: Host and Origin

HTTP deployments enforce **exact** Host and Origin allowlists on every route. Wildcards are
rejected.

| Variable | Default | Notes |
|----------|---------|-------|
| `MAVEDB_LINK_ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | JSON list of exact `Host` values. **Production must add the public reverse-proxy hostname** (e.g. `mavedb-link.genefoundry.org`) in addition to the loopback defaults, or the proxy's requests are rejected. |
| `MAVEDB_LINK_ALLOWED_ORIGINS` | `[]` | Browser `Origin` admission gate. Empty **permits requests that carry no `Origin` header** (i.e. non-browser clients such as the router), while rejecting any browser origin not listed. |
| `MAVEDB_LINK_CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | CORS *response* headers. |

`ALLOWED_ORIGINS` (request admission) and `CORS_ORIGINS` (response headers) are **separate
knobs**. Adding an origin to CORS does not admit it through the request guard; a browser
origin you intend to serve must appear in both.

## Upstream MaveDB API

Public and read-only — **no credentials, no API key**. The router never forwards a caller's
token upstream.

| Variable | Default |
|----------|---------|
| `MAVEDB_LINK_API__BASE_URL` | `https://api.mavedb.org/api/v1` |
| `MAVEDB_LINK_API__REQUEST_TIMEOUT` | `30.0` seconds (1–300) |
| `MAVEDB_LINK_API__MAX_CONCURRENCY` | `5` in-flight requests (1–64) |
| `MAVEDB_LINK_API__MAX_RETRIES` | `4` — transient 429/5xx/network faults |
| `MAVEDB_LINK_API__CACHE_TTL` | `600` seconds (0 disables) |
| `MAVEDB_LINK_API__CACHE_SIZE` | `512` entries (0 disables) |
| `MAVEDB_LINK_API__USER_AGENT` | `mavedb-link/<version> (+repo url)` |

## Local mirror

Mechanics and build commands: [data.md](data.md).

| Variable | Default | Notes |
|----------|---------|-------|
| `MAVEDB_LINK_MIRROR__ENABLED` | `true` | `false` serves live-only |
| `MAVEDB_LINK_MIRROR__DATA_DIR` | `data` | Directory holding the mirror DB |
| `MAVEDB_LINK_MIRROR__DB_FILENAME` | `mavedb.sqlite` | |
| `MAVEDB_LINK_MIRROR__ZENODO_CONCEPT_ID` | `11201736` | Resolves "latest" |
| `MAVEDB_LINK_MIRROR__SOURCE_URL` | unset | Explicit dump URL override |
| `MAVEDB_LINK_MIRROR__REFRESH_TTL_DAYS` | `30` | Age beyond which the mirror is stale |
| `MAVEDB_LINK_MIRROR__BUILD_LOCAL` | `false` | Build from the Zenodo dump if the prebuilt pull fails |
| `MAVEDB_LINK_MIRROR__BUNDLE_URL` | `""` | `latest`, an explicit URL, or empty (disabled) |
| `MAVEDB_LINK_MIRROR__BUNDLE_ASSET_NAME` | `mavedb.sqlite.zst` | Release asset name |
| `MAVEDB_LINK_MIRROR__BUNDLE_EXPECTED_SHA256` | unset | **Required** for an explicit bundle URL when no valid sidecar exists |
| `MAVEDB_LINK_MIRROR__GITHUB_REPO` | `berntpopp/mavedb-link` | Hosts the prebuilt artifacts |

**Ingest safety caps.** The acquire path is bounded against hostile or corrupt archives; each
is overridable with the matching `MAVEDB_LINK_MIRROR__*` variable.

| Cap | Default |
|-----|---------|
| `MAX_DUMP_BYTES` | 4 GiB |
| `MAX_BUNDLE_BYTES` | 2 GiB |
| `MAX_DATABASE_BYTES` | 8 GiB |
| `MAX_ARCHIVE_ENTRIES` | 10,000 |
| `MAX_ARCHIVE_MEMBER_BYTES` | 2 GiB |
| `MAX_ARCHIVE_EXPANDED_BYTES` | 16 GiB |
| `MAX_METADATA_BYTES` | 1 MiB |
| `MAX_DOWNLOAD_SECONDS` | 7200 |

## Mapped-variant cache

The lazy VRS/ClinGen backfill store (see [data.md](data.md)).

| Variable | Default |
|----------|---------|
| `MAVEDB_LINK_CACHE__ENABLED` | `true` — disabling falls back to the live API on every call |
| `MAVEDB_LINK_CACHE__DB_PATH` | `data/mavedb_cache.sqlite` (parent dir auto-created) |
| `MAVEDB_LINK_CACHE__LRU_SETS` | In-memory LRU (score sets) in front of the on-disk cache |

## Logging & environment

| Variable | Default | Notes |
|----------|---------|-------|
| `MAVEDB_LINK_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `MAVEDB_LINK_LOG_FORMAT` | `console` | `json` in production |
| `MAVEDB_LINK_ENVIRONMENT` | `development` | `development` \| `production` |
| `MAVEDB_LINK_GIT_SHA` / `MAVEDB_LINK_BUILT_AT` | unset | Build provenance, normally injected by the Docker build |
