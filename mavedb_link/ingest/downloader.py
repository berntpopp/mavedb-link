"""Resolve + download the CC0 MaveDB bulk dump from Zenodo (sync, for the CLI).

The Zenodo concept record (DOI 10.5281/zenodo.11201736) versions the dump; we
resolve the highest version, then stream the dump archive to disk verifying the
published md5. The container format is whatever Zenodo published -- a ``.zip``
through v4, a ``.tar.gz`` from the 2026-06-24 dump onward -- and the filename is
taken from the Zenodo record, so the builder auto-detects it. The download is
large (~1.8 GB) so it streams in chunks and never buffers the whole file in memory.

Some dumps omit the per-set annotations CSVs; mapped VRS/ClinGen data is then
backfilled lazily from the live API into the on-disk mapped-variant cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from mavedb_link.exceptions import DataUnavailableError, ServiceUnavailableError
from mavedb_link.ingest.download_security import (
    DownloadPolicy,
    open_validated_stream,
    read_bounded,
    stream_atomic,
)

ZENODO_API = "https://zenodo.org/api"
_CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class DumpRef:
    """A resolved Zenodo dump version (the file to download + its provenance)."""

    record_id: str
    version: str
    published: str
    url: str
    filename: str
    md5: str
    size: int


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Return an httpx client and whether the caller owns closing it."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=60.0, follow_redirects=False), True


def resolve_latest_dump(
    concept_id: str,
    *,
    client: httpx.Client | None = None,
    max_dump_bytes: int | None = None,
    max_metadata_bytes: int | None = None,
) -> DumpRef:
    """Resolve the newest dump version for a Zenodo concept record id.

    "Newest" follows the publication date first (then version, then record id):
    from the 2026-06-24 dump on, MaveDB stopped setting ``metadata.version`` on the
    record, so ranking purely by version number would wrongly stick on the older v4.
    """
    http, owned = _client(client)
    try:
        metadata_limit = _mirror_limit("max_metadata_bytes", max_metadata_bytes)
        with http.stream(
            "GET",
            f"{ZENODO_API}/records",
            # Zenodo (anonymous) rejects this search with HTTP 400 unless a sort is
            # given AND size is small (>=50 is rejected); 25 is ample for a ~yearly
            # dump. We pick the max version ourselves, so the direction is moot.
            params={
                "q": f"conceptrecid:{concept_id}",
                "all_versions": "true",
                "sort": "-version",
                "size": "25",
            },
        ) as resp:
            if 300 <= resp.status_code < 400:
                raise ServiceUnavailableError("Zenodo metadata redirect was rejected")
            resp.raise_for_status()
            body = read_bounded(
                resp, max_bytes=metadata_limit, label="Zenodo metadata", chunk_size=_CHUNK
            )
        hits = json.loads(body).get("hits", {}).get("hits", [])
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Could not query Zenodo: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataUnavailableError("Zenodo returned invalid metadata JSON") from exc
    finally:
        if owned:
            http.close()
    if not hits:
        raise DataUnavailableError(f"No Zenodo versions for concept {concept_id}.")
    best = max(hits, key=_recency_key)
    files = best.get("files") or []
    if not files:
        raise DataUnavailableError(f"Zenodo record {best.get('id')} has no files.")
    file = files[0]
    checksum = str(file.get("checksum") or "")
    match = re.fullmatch(r"md5:([0-9a-fA-F]{32})", checksum)
    if match is None:
        raise DataUnavailableError("Zenodo dump is missing a valid md5 checksum")
    md5 = match.group(1).lower()
    size = file.get("size")
    if not isinstance(size, int) or size <= 0:
        raise DataUnavailableError("Zenodo dump is missing a valid positive size")
    dump_limit = _mirror_limit("max_dump_bytes", max_dump_bytes)
    if size > dump_limit:
        raise DataUnavailableError(f"Zenodo dump size {size} exceeds {dump_limit} bytes")
    links = file.get("links") or {}
    url = links.get("self") or links.get("download")
    if not url:
        raise DataUnavailableError("Zenodo file has no download link.")
    return DumpRef(
        record_id=str(best.get("id")),
        version=str((best.get("metadata") or {}).get("version") or ""),
        published=str((best.get("metadata") or {}).get("publication_date") or ""),
        url=url,
        filename=str(file.get("key") or "mavedb-dump.tar.gz"),
        md5=md5,
        size=size,
    )


def _recency_key(hit: dict[str, object]) -> tuple[str, int, int]:
    """Sort key for 'newest': (publication_date, version, record_id), all desc-friendly.

    Publication date leads because MaveDB no longer stamps ``metadata.version`` on
    the newest dump; version and record id break same-day ties.
    """
    meta = hit.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    published = str(meta.get("publication_date") or "")
    try:
        version = int(str(meta.get("version") or 0))
    except (TypeError, ValueError):
        version = 0
    try:
        record_id = int(str(hit.get("id") or 0))
    except (TypeError, ValueError):
        record_id = 0
    return (published, version, record_id)


def download_file(
    url: str,
    dest: Path,
    *,
    expected_md5: str,
    expected_size: int | None = None,
    max_bytes: int | None = None,
    max_seconds: float | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Atomically stream and verify a Zenodo artifact, returning local SHA-256."""
    http, owned = _client(client)
    if re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5) is None:
        raise DataUnavailableError("Zenodo dump is missing a valid md5 checksum")
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    hashers = _HashFanout(md5, sha256)
    policy = DownloadPolicy(
        allowed_hosts=frozenset({"zenodo.org"}),
        max_bytes=_mirror_limit("max_dump_bytes", max_bytes),
        max_seconds=max_seconds,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, staging_name = tempfile.mkstemp(dir=dest.parent, suffix=".download.staging")
    os.close(fd)
    staging_path = Path(staging_name)
    try:
        try:
            with open_validated_stream(http, url, headers={}, policy=policy) as resp:
                resp.raise_for_status()
                stream_atomic(
                    resp,
                    staging_path,
                    max_bytes=policy.max_bytes,
                    expected_size=expected_size,
                    hasher=hashers,
                    max_seconds=max_seconds,
                    chunk_size=_CHUNK,
                )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"Download failed: {exc}") from exc
        got = md5.hexdigest()
        if got.lower() != expected_md5.lower():
            raise DataUnavailableError(
                f"Checksum mismatch for {dest.name}: expected {expected_md5}, got {got}."
            )
        os.replace(staging_path, dest)
        return sha256.hexdigest()
    finally:
        staging_path.unlink(missing_ok=True)
        if owned:
            http.close()


class _HashFanout:
    """Update multiple digest algorithms during the same streamed pass."""

    def __init__(self, *hashers: Any) -> None:
        self._hashers = hashers

    def update(self, chunk: bytes) -> None:
        for hasher in self._hashers:
            hasher.update(chunk)


def _mirror_limit(name: str, override: int | None) -> int:
    if override is not None:
        return override
    from mavedb_link.config import settings

    return int(getattr(settings.mirror, name))
