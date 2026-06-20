"""Resolve + download the CC0 MaveDB bulk dump from Zenodo (sync, for the CLI).

The Zenodo concept record (DOI 10.5281/zenodo.11201736) versions the dump; we
resolve the highest version, then stream its zip to disk verifying the published
md5. The download is large (~1.8 GB) so it streams in chunks and never buffers
the whole file in memory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

from mavedb_link.exceptions import DataUnavailableError, ServiceUnavailableError

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
    md5: str | None
    size: int | None


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Return an httpx client and whether the caller owns closing it."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=60.0, follow_redirects=True), True


def resolve_latest_dump(concept_id: str, *, client: httpx.Client | None = None) -> DumpRef:
    """Resolve the newest dump version for a Zenodo concept record id."""
    http, owned = _client(client)
    try:
        resp = http.get(
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
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Could not query Zenodo: {exc}") from exc
    finally:
        if owned:
            http.close()
    if not hits:
        raise DataUnavailableError(f"No Zenodo versions for concept {concept_id}.")
    best = max(hits, key=_version_key)
    files = best.get("files") or []
    if not files:
        raise DataUnavailableError(f"Zenodo record {best.get('id')} has no files.")
    file = files[0]
    checksum = str(file.get("checksum") or "")
    md5 = checksum.split(":", 1)[1] if ":" in checksum else (checksum or None)
    links = file.get("links") or {}
    url = links.get("self") or links.get("download")
    if not url:
        raise DataUnavailableError("Zenodo file has no download link.")
    return DumpRef(
        record_id=str(best.get("id")),
        version=str((best.get("metadata") or {}).get("version") or ""),
        published=str((best.get("metadata") or {}).get("publication_date") or ""),
        url=url,
        filename=str(file.get("key") or "mavedb-dump.zip"),
        md5=md5,
        size=file.get("size"),
    )


def _version_key(hit: dict[str, object]) -> int:
    meta = hit.get("metadata") or {}
    try:
        return int(str((meta if isinstance(meta, dict) else {}).get("version") or 0))
    except (TypeError, ValueError):
        return 0


def download_file(
    url: str,
    dest: Path,
    *,
    expected_md5: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Stream ``url`` to ``dest``, returning the md5 (and verifying it if given)."""
    http, owned = _client(client)
    digest = hashlib.md5(usedforsecurity=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with http.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    fh.write(chunk)
                    digest.update(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise ServiceUnavailableError(f"Download failed: {exc}") from exc
    finally:
        if owned:
            http.close()
    got = digest.hexdigest()
    if expected_md5 and got != expected_md5:
        dest.unlink(missing_ok=True)
        raise DataUnavailableError(
            f"Checksum mismatch for {dest.name}: expected {expected_md5}, got {got}."
        )
    return got
