"""Prebuilt-artifact packaging: zstd-compress the mirror, publish/pull via GitHub.

CI (or a maintainer) builds the SQLite mirror, ``pack``s it into
``mavedb.sqlite.zst`` + a ``.sha256`` sidecar, and uploads both to a GitHub
Release. A deploy ``pull``s the newest release asset, verifies the checksum, and
decompresses it into place atomically -- far faster than rebuilding from the
1.8 GB dump on every container start.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import zstandard

from mavedb_link.exceptions import DataUnavailableError, ServiceUnavailableError
from mavedb_link.ingest.download_security import (
    DownloadPolicy,
    copy_bounded,
    open_validated_stream,
    read_bounded,
    stream_atomic,
)

_CHUNK = 1 << 20
_GITHUB_API = "https://api.github.com"
_GITHUB_ASSET_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com"})


@dataclass(frozen=True)
class ReleaseAsset:
    """Validated metadata needed to acquire a GitHub release asset."""

    url: str
    sha256: str | None = None
    size: int | None = None


def _sha256_file(path: Path) -> str:
    """Streaming sha256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pack(db_path: Path, out_path: Path | None = None, *, level: int = 19) -> tuple[Path, Path]:
    """Compress ``db_path`` to ``.zst`` + write a ``.sha256`` sidecar; return both."""
    out = out_path or db_path.with_name(db_path.name + ".zst")
    # threads=-1 uses all cores -- packing a ~1-2 GB mirror single-threaded at high
    # levels is the slow step locally and in CI.
    compressor = zstandard.ZstdCompressor(level=level, threads=-1)
    with open(db_path, "rb") as src, open(out, "wb") as dst:
        compressor.copy_stream(src, dst, read_size=_CHUNK, write_size=_CHUNK)
    sha = _sha256_file(out)
    sha_path = Path(f"{out}.sha256")
    sha_path.write_text(f"{sha}  {out.name}\n", encoding="utf-8")
    return out, sha_path


def _as_release_asset(asset: dict[str, Any]) -> ReleaseAsset:
    """Build a validated ReleaseAsset from one GitHub asset entry."""
    url: str = asset["browser_download_url"]
    raw_digest = str(asset.get("digest") or "")
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", raw_digest)
    size = asset.get("size")
    return ReleaseAsset(
        url=url,
        sha256=match.group(1).lower() if match else None,
        size=size if isinstance(size, int) and size > 0 else None,
    )


def resolve_release_asset(
    github_repo: str,
    asset_name: str,
    bundle_url: str,
    *,
    client: httpx.Client | None = None,
    max_metadata_bytes: int | None = None,
) -> ReleaseAsset:
    """Resolve the download URL for the prebuilt asset ('latest' or explicit).

    'latest' means the newest release that actually CARRIES the asset -- not
    ``/releases/latest``. The mirror bundle ships in its own dated ``data-*`` release, so
    ``/releases/latest`` is normally a code release with no assets at all, and cutting any
    code release silently broke the pull: bootstrap fell back to "live-only" while the
    container still reported healthy, quietly inverting the documented mirror-primary /
    live-backup posture into live-only.
    """
    if bundle_url and bundle_url != "latest":
        return ReleaseAsset(bundle_url)
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    try:
        metadata_limit = _mirror_limit("max_metadata_bytes", max_metadata_bytes)
        with http.stream(
            "GET", f"{_GITHUB_API}/repos/{github_repo}/releases", params={"per_page": 30}
        ) as resp:
            if 300 <= resp.status_code < 400:
                raise ServiceUnavailableError("GitHub release metadata redirect was rejected")
            resp.raise_for_status()
            body = read_bounded(
                resp,
                max_bytes=metadata_limit,
                label="GitHub release metadata",
                chunk_size=_CHUNK,
            )
        releases = json.loads(body)
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Could not query GitHub releases: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataUnavailableError("GitHub returned invalid release metadata JSON") from exc
    finally:
        if client is None:
            http.close()

    if not isinstance(releases, list):
        raise DataUnavailableError("GitHub returned invalid release metadata JSON")

    # Newest first, as GitHub returns them. Drafts are never published to anonymous
    # callers, but skip them defensively so a half-cut release is never selected.
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        for asset in release.get("assets") or []:
            if isinstance(asset, dict) and asset.get("name") == asset_name:
                return _as_release_asset(asset)
    raise DataUnavailableError(f"No release of {github_repo} publishes an asset '{asset_name}'.")


def pull(
    github_repo: str,
    asset_name: str,
    bundle_url: str,
    dest_db_path: Path,
    *,
    client: httpx.Client | None = None,
    expected_sha256: str | None = None,
    max_compressed_bytes: int | None = None,
    max_expanded_bytes: int | None = None,
    max_metadata_bytes: int | None = None,
    max_seconds: float | None = None,
) -> None:
    """Download, checksum-verify, and decompress the prebuilt mirror into place."""
    asset = resolve_release_asset(
        github_repo,
        asset_name,
        bundle_url,
        client=client,
        max_metadata_bytes=max_metadata_bytes,
    )
    http = client or httpx.Client(timeout=120.0, follow_redirects=False)
    compressed_limit = _mirror_limit("max_bundle_bytes", max_compressed_bytes)
    expanded_limit = _mirror_limit("max_database_bytes", max_expanded_bytes)
    metadata_limit = _mirror_limit("max_metadata_bytes", max_metadata_bytes)
    if asset.size is not None and asset.size > compressed_limit:
        raise DataUnavailableError(
            f"bundle metadata size {asset.size} exceeds {compressed_limit} bytes"
        )
    configured_digest = _valid_sha256(expected_sha256) if expected_sha256 is not None else None
    expected = configured_digest or asset.sha256
    if expected is None:
        expected = _fetch_required_sha(http, asset.url, max_bytes=metadata_limit)
    dest_db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_zst = tempfile.mkstemp(dir=dest_db_path.parent, suffix=".zst.tmp")
    os.close(fd)
    zst_path = Path(tmp_zst)
    try:
        actual = _download(
            http,
            asset.url,
            zst_path,
            max_bytes=compressed_limit,
            expected_size=asset.size,
            max_seconds=max_seconds,
        )
        if actual != expected:
            raise DataUnavailableError(f"Checksum mismatch for {asset_name}.")
        _decompress_replace(zst_path, dest_db_path, max_expanded_bytes=expanded_limit)
    finally:
        zst_path.unlink(missing_ok=True)
        if client is None:
            http.close()


def _download(
    http: httpx.Client,
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    expected_size: int | None,
    max_seconds: float | None,
) -> str:
    digest = hashlib.sha256()
    policy = DownloadPolicy(
        allowed_hosts=_GITHUB_ASSET_HOSTS,
        max_bytes=max_bytes,
        max_seconds=max_seconds,
    )
    try:
        with open_validated_stream(http, url, headers={}, policy=policy) as resp:
            resp.raise_for_status()
            stream_atomic(
                resp,
                dest,
                max_bytes=max_bytes,
                expected_size=expected_size,
                hasher=digest,
                max_seconds=max_seconds,
                chunk_size=_CHUNK,
            )
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Bundle download failed: {exc}") from exc
    return digest.hexdigest()


def _fetch_required_sha(http: httpx.Client, url: str, *, max_bytes: int) -> str:
    """Fetch and strictly parse the mandatory sibling SHA-256 sidecar."""
    policy = DownloadPolicy(allowed_hosts=_GITHUB_ASSET_HOSTS, max_bytes=max_bytes)
    try:
        with open_validated_stream(http, f"{url}.sha256", headers={}, policy=policy) as resp:
            if resp.status_code != 200:
                raise DataUnavailableError(
                    "a valid expected SHA-256 is required for the MaveDB bundle"
                )
            body = bytearray()
            for chunk in resp.iter_bytes(_CHUNK):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise DataUnavailableError(
                        f"bundle checksum metadata exceeded {max_bytes} bytes"
                    )
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Bundle checksum download failed: {exc}") from exc
    first = bytes(body).decode("ascii", errors="replace").strip().split()
    return _valid_sha256(first[0] if first else None)


def _decompress_replace(zst_path: Path, dest_db_path: Path, *, max_expanded_bytes: int) -> None:
    """Decompress a ``.zst`` into a temp file, then atomically swap it into place."""
    fd, tmp_db = tempfile.mkstemp(dir=dest_db_path.parent, suffix=".sqlite.tmp")
    os.close(fd)
    tmp_db_path = Path(tmp_db)
    try:
        decompressor = zstandard.ZstdDecompressor()
        with (
            open(zst_path, "rb") as src,
            decompressor.stream_reader(src) as reader,
            open(tmp_db_path, "wb") as dst,
        ):
            try:
                copy_bounded(reader, dst, max_bytes=max_expanded_bytes)
            except DataUnavailableError as exc:
                raise DataUnavailableError(
                    f"expanded bundle exceeded {max_expanded_bytes} bytes"
                ) from exc
        os.replace(tmp_db_path, dest_db_path)
    except zstandard.ZstdError as exc:
        raise DataUnavailableError(f"Invalid zstd bundle: {exc}") from exc
    finally:
        tmp_db_path.unlink(missing_ok=True)


def _valid_sha256(value: str | None) -> str:
    if value is None or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise DataUnavailableError("a valid expected SHA-256 is required for the MaveDB bundle")
    return value.lower()


def _mirror_limit(name: str, override: int | None) -> int:
    if override is not None:
        return override
    from mavedb_link.config import settings

    return int(getattr(settings.mirror, name))


def publish(db_path: Path, github_repo: str, tag: str, asset_name: str) -> None:
    """Pack ``db_path`` and upload it (+ sidecar) to a GitHub Release via ``gh``.

    Maintainer/CI path: requires the ``gh`` CLI authenticated for ``github_repo``.
    Creates the release if absent and clobbers an existing asset of the same name.
    """
    out, sha_path = pack(db_path, db_path.with_name(asset_name))
    _gh(["release", "view", tag, "--repo", github_repo]) or _gh(
        ["release", "create", tag, "--repo", github_repo, "--title", tag, "--notes", tag]
    )
    _gh(
        [
            "release",
            "upload",
            tag,
            str(out),
            str(sha_path),
            "--clobber",
            "--repo",
            github_repo,
        ],
        check=True,
    )


def _gh(args: list[str], *, check: bool = False) -> bool:
    """Run a ``gh`` subcommand; return True on success (raise on check failure)."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True)  # noqa: S603, S607
    if check and result.returncode != 0:
        raise ServiceUnavailableError(f"gh {args[0]} failed: {result.stderr.strip()}")
    return result.returncode == 0
