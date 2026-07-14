# Deployment

`mavedb-link` follows the GeneFoundry Container & Deployment Hardening Standard v1: non-root,
read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, resource limits, and **expose-only
behind a reverse proxy**.

> [!IMPORTANT]
> The backend is **unauthenticated by design** — the router owns edge auth at the trust
> boundary. It MUST be reachable only through the router or a reverse proxy, and MUST NOT be
> published directly on a public interface.

## Compose stacks

Three overlays under [`../docker/`](../docker):

| File | Role |
|------|------|
| `docker-compose.yml` | Dev/local. Publishes on **loopback only** (`127.0.0.1:${MAVEDB_LINK_HOST_PORT:-8023}:8000`) so copying it to a server never exposes the backend on the public IP. |
| `docker-compose.prod.yml` | Production. `ports: !reset []` + `expose: 8000` — no published port; the proxy reaches it over the Docker network. |
| `docker-compose.npm.yml` | Production behind Nginx Proxy Manager; joins the external NPM network (`NPM_SHARED_NETWORK_NAME`, default `npm_default`). |

```bash
make docker-build
make docker-up      # starts the stack
make docker-url     # prints the published MCP + health URLs
make docker-logs
make docker-down
```

## Two services, one image

Both compose stacks run an **init container** and the application from the same image:

1. `mavedb-data-init` — one-shot. Materializes the verified MaveDB mirror bundle into
   `/data/reference` on the `mavedb-data` named volume, then exits.
2. `mavedb-link` — waits for the init to complete successfully
   (`condition: service_completed_successfully`), then serves from that mirror.

The image ships **no data**. In dev the init runs `mavedb-link-data bootstrap` with
`BUNDLE_URL=latest` (reuse → pull the newest prebuilt artifact → build, else degrade to
live-only). In production it runs `mavedb-link-data pull` against an **exact, pinned** release.

The application entrypoint ([`../docker/entrypoint.sh`](../docker/entrypoint.sh)) itself runs
`bootstrap` when `MAVEDB_LINK_MIRROR__ENABLED=true`, and **exits 0 even when no mirror is
available** — the server always starts, because the live MaveDB API is the backup.

## Pinning the production bundle

The NPM/prod overlay refuses to start unless the data bundle is pinned — these are required
(`:?` in compose), not optional:

| Variable | Meaning |
|----------|---------|
| `MAVEDB_DATA_BUNDLE_URL` | Exact release-asset URL |
| `MAVEDB_DATA_RELEASE_TAG` | `data-YYYY-MM-DD` |
| `MAVEDB_DATA_SHA256` | SHA-256 of the compressed bundle |
| `MAVEDB_DATA_EXPANDED_SHA256` | SHA-256 of the expanded database |
| `MAVEDB_DATA_SCHEMA_VERSION` | Mirror schema version (default `4.0.0`) |

Bundles are published to GitHub Releases by `.github/workflows/data.yml` (monthly + manual).
Build one locally with `make data-build && make data-pack`. See [data.md](data.md).

## Reverse proxy

The public hostname **must** be added to the Host allowlist or the proxy's requests are
rejected by the request guard:

```bash
MAVEDB_LINK_ALLOWED_HOSTS='["localhost","127.0.0.1","::1","mavedb-link.genefoundry.org"]'
```

Copy [`../.env.docker.example`](../.env.docker.example) to `.env.docker` for the NPM
deployment. TLS terminates at the proxy. Full guard semantics — including why
`ALLOWED_ORIGINS` and `CORS_ORIGINS` are separate knobs — are in
[configuration.md](configuration.md).

The container serves `unified` transport (REST + MCP at `/mcp`). Do not set
`MAVEDB_LINK_TRANSPORT=http` in a deployment the router talks to: that mode exposes no MCP
endpoint.

## Hardening baseline

Both services run with `read_only: true`, a `noexec,nosuid` tmpfs for `/tmp`,
`no-new-privileges:true`, `cap_drop: ALL`, `init: true`, per-service memory/CPU/pids limits,
and capped json-file logging. The healthcheck sends an explicit `Host` header so it passes the
request guard:

```yaml
test: ["CMD", "curl", "-f", "-H", "Host: localhost", "http://127.0.0.1:8000/health"]
```
