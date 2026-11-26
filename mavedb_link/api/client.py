"""Async HTTP client for the MaveDB REST API.

One shared ``httpx.AsyncClient`` opened lazily under a lock and reused across
concurrent tasks. An ``asyncio.Semaphore`` bounds in-flight requests so a burst
fan-out does not hammer the upstream, and a jittered exponential-backoff layer
absorbs 429s and transient transport faults. GET responses are memoised in a
small TTL+LRU cache (MaveDB published records are effectively immutable for the
cache window). Status codes map to the typed exceptions the MCP envelope
classifies:

- 404 -> NotFoundError
- 400 / 422 -> InvalidInputError
- 429 (after retries) -> RateLimitError
- 5xx / timeout / network -> ServiceUnavailableError

We build full URLs explicitly (``base_url + path``) rather than relying on
``httpx`` ``base_url`` merging, which would otherwise drop the ``/api/v1`` prefix
for absolute request paths.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import OrderedDict
from typing import Any

import httpx

from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data import provenance
from mavedb_link.exceptions import (
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)

#: HTTP statuses worth retrying (rate limit + transient upstream faults).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: Jittered exponential backoff parameters.
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0


class _TTLCache:
    """A tiny TTL + LRU cache for idempotent GET responses (process-local)."""

    def __init__(self, *, maxsize: int, ttl: float) -> None:
        """Initialise with a max entry count and per-entry TTL (seconds)."""
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: OrderedDict[str, tuple[float, Any, str]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        """Return a live cached value, or ``None`` on miss/expiry."""
        if self._maxsize <= 0 or self._ttl <= 0:
            return None
        hit = self._store.get(key)
        if hit is None:
            return None
        expires_at, value, fetched_at = hit
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        provenance.fetched(fetched_at)  # a cache hit keeps its original fetch time
        return value

    def set(self, key: str, value: Any) -> None:
        """Insert a value, evicting the least-recently-used entry if full."""
        if self._maxsize <= 0 or self._ttl <= 0:
            return
        self._store[key] = (time.monotonic() + self._ttl, value, provenance.utc())
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()


class MaveDBClient:
    """Async client for the public MaveDB REST API."""

    def __init__(self, config: MaveDBApiConfig | None = None) -> None:
        """Build the client from config (defaults to the global API settings)."""
        if config is None:
            from mavedb_link.config import settings

            config = settings.api
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._connect_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, config.max_concurrency))
        self._cache = _TTLCache(maxsize=config.cache_size, ttl=float(config.cache_ttl))

    @property
    def base_url(self) -> str:
        """The configured upstream base URL (no trailing slash)."""
        return self._base_url

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Open (once) and return the shared AsyncClient."""
        if self._client is None:
            async with self._connect_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._config.request_timeout,
                        headers={
                            "User-Agent": self._config.user_agent,
                            "Accept": "application/json",
                        },
                        follow_redirects=False,
                    )
        return self._client

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map a non-2xx response to a typed exception (2xx returns ``None``).

        The upstream response BODY is deliberately NOT interpolated into the
        exception message: a caller-influenced query can make MaveDB reflect
        hostile prose (incl. control/zero-width/bidi/NUL) into a 4xx/5xx body,
        and echoing it verbatim would smuggle attacker-controlled text into a
        caller-visible message. Fixed, upstream-body-free messages are raised
        instead (the HTTP status is a safe, non-attacker-controlled scalar); the
        body is not logged either, preserving the no-PII-in-logs invariant.
        """
        status = response.status_code
        if 300 <= status < 400:
            raise ServiceUnavailableError(f"MaveDB API redirect (HTTP {status}) was rejected.")
        if status < 400:
            return
        if status == 404:
            raise NotFoundError("MaveDB record not found.")
        if status in (400, 422):
            raise InvalidInputError("MaveDB rejected the request as invalid.")
        if status == 429:
            raise RateLimitError("MaveDB rate limit hit.")
        raise ServiceUnavailableError(f"MaveDB API error (HTTP {status}).")

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        accept: str | None = None,
    ) -> httpx.Response:
        """Send one request (absolute URL) with bounded concurrency + jittered retry."""
        client = await self._ensure_client()
        headers = {"Accept": accept} if accept else None
        delay = _BACKOFF_BASE_SECONDS
        last_exc: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            response: httpx.Response | None = None
            async with self._semaphore:
                try:
                    response = await client.request(
                        method, url, params=params, json=json, headers=headers
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
            if response is not None and response.status_code not in _RETRYABLE_STATUS:
                return response
            if attempt >= self._config.max_retries:
                if response is not None:
                    return response
                raise ServiceUnavailableError(
                    f"MaveDB API unreachable after {attempt + 1} attempts."
                ) from last_exc
            # Full jitter de-synchronises a concurrent burst's retries.
            await asyncio.sleep(random.uniform(0, min(delay, _BACKOFF_MAX_SECONDS)))  # noqa: S311
            delay = min(delay * 2, _BACKOFF_MAX_SECONDS)
        raise ServiceUnavailableError("MaveDB API retry loop exhausted")  # pragma: no cover

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON resource (memoised in the TTL cache)."""
        key = _cache_key("GET", path, params)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        response = await self._send("GET", f"{self._base_url}{path}", params=params)
        self._raise_for_status(response)
        data = response.json()
        self._cache.set(key, data)
        return data

    async def get_text(
        self, path: str, *, params: dict[str, Any] | None = None, accept: str = "text/csv"
    ) -> str:
        """GET a text resource (e.g. a scores CSV); memoised in the TTL cache."""
        key = _cache_key(f"GET:{accept}", path, params)
        cached = self._cache.get(key)
        if cached is not None:
            return str(cached)
        response = await self._send("GET", f"{self._base_url}{path}", params=params, accept=accept)
        self._raise_for_status(response)
        text = response.text
        self._cache.set(key, text)
        return text

    async def post_json(
        self,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """POST a JSON body and return the parsed JSON response (not cached)."""
        response = await self._send("POST", f"{self._base_url}{path}", params=params, json=json)
        self._raise_for_status(response)
        return response.json()

    async def get_version(self) -> Any:
        """GET the upstream version endpoint (``{base}/api/version``).

        MaveDB registers ``/api/version`` *under* the ``/api/v1`` router, so the
        full path is ``.../api/v1/api/version``.
        """
        response = await self._send("GET", f"{self._base_url}/api/version")
        self._raise_for_status(response)
        return response.json()

    async def ensure_mapped_variants(self, score_set_urn: str) -> list[dict[str, Any]]:
        """Return a score set's mapped-variant rows from the live API.

        The contract :class:`~mavedb_link.data.hybrid.HybridClient` overrides to
        serve/persist via the on-disk cache; the base client always fetches live.
        Reads the NDJSON ``GET /score-sets/{urn}/variant-details`` export (the
        retired ``/mapped-variants`` now answers 410) line by line, keeping only
        each record's mapping: a full record is ~13 KB, a set can hold 100k+.
        """
        path = f"/score-sets/{score_set_urn}/variant-details"
        client = await self._ensure_client()
        rows: list[dict[str, Any]] = []
        total: str | None = None
        for attempt in range(self._config.max_retries + 1):
            last = attempt == self._config.max_retries
            try:
                async with (
                    self._semaphore,
                    client.stream(
                        "GET", f"{self._base_url}{path}", headers={"Accept": "application/x-ndjson"}
                    ) as response,
                ):
                    if last or response.status_code not in _RETRYABLE_STATUS:
                        rows, total = await _read_mapped_rows(response)
                        break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if last:
                    raise ServiceUnavailableError("MaveDB variant-details export failed.") from exc
            await asyncio.sleep(random.uniform(0, _BACKOFF_BASE_SECONDS))  # noqa: S311
        # A mid-stream upstream drop still ends in a 200; never cache a short list.
        if total is not None and total.isdigit() and int(total) != len(rows):
            raise ServiceUnavailableError("MaveDB variant-details export was truncated.")
        return rows

    def clear_cache(self) -> None:
        """Drop the in-process response cache (test/maintenance helper)."""
        self._cache.clear()

    async def aclose(self) -> None:
        """Close the shared client (idempotent)."""
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()


async def _read_mapped_rows(response: httpx.Response) -> tuple[list[dict[str, Any]], str | None]:
    """Parse an NDJSON export into mapping rows (+ its ``X-Total-Count``)."""
    MaveDBClient._raise_for_status(response)
    rows: list[dict[str, Any]] = []
    async for line in response.aiter_lines():
        if line.strip():
            try:
                parsed = json.loads(line)  # a legacy JSON-array body is one line
            except ValueError as exc:  # a cut-off last line: the export was truncated
                raise ServiceUnavailableError(
                    "MaveDB variant-details export was truncated."
                ) from exc
            for item in parsed if isinstance(parsed, list) else [parsed]:
                if isinstance(item, dict):
                    rows.append(mapped_row(item))
    return rows, response.headers.get("x-total-count")


def mapped_row(record: dict[str, Any]) -> dict[str, Any]:
    """A variant record's own VRS pair as one mapped-variant row.

    ``GET /variants/{urn}`` and the ``variant-details`` export carry the variant's
    one current mapping (``postMapped``); ``isCurrent`` there is score-set
    currency, not mapping currency. A legacy mapped-variant row (``variantUrn``)
    passes through unchanged.
    """
    if "variantUrn" in record:
        return record
    return {
        "variantUrn": record.get("urn"),
        "preMapped": record.get("preMapped"),
        "postMapped": record.get("postMapped"),
        "clingenAlleleId": record.get("clingenAlleleId"),
        "current": True,
    }


def _cache_key(prefix: str, path: str, params: dict[str, Any] | None) -> str:
    """Stable cache key from method-prefix + path + sorted params."""
    if not params:
        return f"{prefix} {path}"
    parts = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{prefix} {path}?{parts}"
