"""Prebuilt-artifact packaging: zstd-compress the mirror, publish/pull via GitHub.

CI (or a maintainer) builds the SQLite mirror, ``pack``s it into
``mavedb.sqlite.zst`` + a ``.sha256`` sidecar, and uploads both to a GitHub
Release. A deploy ``pull``s the newest release asset, verifies the checksum, and
decompresses it into place atomically -- far faster than rebuilding from the
1.8 GB dump on every container start.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
import zstandard

from mavedb_link.exceptions import DataUnavailableError, ServiceUnavailableError

_CHUNK = 1 << 20
_GITHUB_API = "https://api.github.com"


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


def resolve_release_asset(
    github_repo: str,
    asset_name: str,
    bundle_url: str,
    *,
    client: httpx.Client | None = None,
) -> str:
    """Resolve the download URL for the prebuilt asset ('latest' or explicit)."""
    if bundle_url and bundle_url != "latest":
        return bundle_url
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = http.get(f"{_GITHUB_API}/repos/{github_repo}/releases/latest")
        resp.raise_for_status()
        assets = resp.json().get("assets", [])
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Could not query GitHub releases: {exc}") from exc
    finally:
        if client is None:
            http.close()
    for asset in assets:
        if asset.get("name") == asset_name:
            url: str = asset["browser_download_url"]
            return url
    raise DataUnavailableError(f"No asset '{asset_name}' in the latest release of {github_repo}.")


def pull(
    github_repo: str,
    asset_name: str,
    bundle_url: str,
    dest_db_path: Path,
    *,
    client: httpx.Client | None = None,
) -> None:
    """Download, checksum-verify, and decompress the prebuilt mirror into place."""
    url = resolve_release_asset(github_repo, asset_name, bundle_url, client=client)
    http = client or httpx.Client(timeout=120.0, follow_redirects=True)
    dest_db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_zst = tempfile.mkstemp(dir=dest_db_path.parent, suffix=".zst.tmp")
    os.close(fd)
    zst_path = Path(tmp_zst)
    try:
        _download(http, url, zst_path)
        expected = _fetch_expected_sha(http, url)
        if expected and _sha256_file(zst_path) != expected:
            raise DataUnavailableError(f"Checksum mismatch for {asset_name}.")
        _decompress_replace(zst_path, dest_db_path)
    finally:
        zst_path.unlink(missing_ok=True)
        if client is None:
            http.close()


def _download(http: httpx.Client, url: str, dest: Path) -> None:
    try:
        with http.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(f"Bundle download failed: {exc}") from exc


def _fetch_expected_sha(http: httpx.Client, url: str) -> str | None:
    """Best-effort fetch of the ``.sha256`` sidecar (returns the bare digest)."""
    try:
        resp = http.get(f"{url}.sha256")
        if resp.status_code != 200:
            return None
        return resp.text.strip().split()[0]
    except httpx.HTTPError:
        return None


def _decompress_replace(zst_path: Path, dest_db_path: Path) -> None:
    """Decompress a ``.zst`` into a temp file, then atomically swap it into place."""
    fd, tmp_db = tempfile.mkstemp(dir=dest_db_path.parent, suffix=".sqlite.tmp")
    os.close(fd)
    tmp_db_path = Path(tmp_db)
    try:
        decompressor = zstandard.ZstdDecompressor()
        with open(zst_path, "rb") as src, open(tmp_db_path, "wb") as dst:
            decompressor.copy_stream(src, dst, read_size=_CHUNK, write_size=_CHUNK)
        os.replace(tmp_db_path, dest_db_path)
    except BaseException:
        tmp_db_path.unlink(missing_ok=True)
        raise


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
