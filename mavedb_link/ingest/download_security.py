"""Redirect validation and bounded atomic streaming for ingest artifacts."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from mavedb_link.exceptions import DataUnavailableError

_SAFE_REDIRECT_HEADERS = frozenset({"accept", "user-agent", "if-none-match", "if-modified-since"})


@dataclass(frozen=True)
class DownloadPolicy:
    """Allowed redirect destinations and resource bounds."""

    allowed_hosts: frozenset[str]
    max_redirects: int = 5
    max_bytes: int = 128 * 1024 * 1024
    max_seconds: float | None = None


def validate_https_url(url: httpx.URL, policy: DownloadPolicy) -> None:
    """Reject unsafe schemes, credentials, ports, and exact-host mismatches."""
    host = (url.host or "").lower()
    if url.scheme != "https":
        raise DataUnavailableError(f"download URL must use HTTPS: {url}")
    if url.userinfo:
        raise DataUnavailableError("download URL must not contain user information")
    if url.port not in (None, 443):
        raise DataUnavailableError(f"download URL port {url.port} is not allowed")
    if host not in policy.allowed_hosts:
        raise DataUnavailableError(f"download host {host} is not allowed")


@contextmanager
def open_validated_stream(
    client: httpx.Client,
    url: str,
    *,
    headers: Mapping[str, str],
    policy: DownloadPolicy,
) -> Iterator[httpx.Response]:
    """Open a stream after validating the initial URL and every redirect hop."""
    current = httpx.URL(url)
    safe_headers = {
        name: value for name, value in headers.items() if name.lower() in _SAFE_REDIRECT_HEADERS
    }
    for hop in range(policy.max_redirects + 1):
        validate_https_url(current, policy)
        request = client.build_request("GET", current, headers=safe_headers)
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in {301, 302, 303, 307, 308}:
            try:
                yield response
            finally:
                response.close()
            return
        location = response.headers.get("Location")
        response.close()
        if location is None:
            raise DataUnavailableError("redirect response is missing Location")
        if hop == policy.max_redirects:
            raise DataUnavailableError(f"download exceeded {policy.max_redirects} redirects")
        current = current.join(location)
    raise AssertionError("redirect loop exhausted unexpectedly")


def stream_atomic(
    response: httpx.Response,
    destination: Path,
    *,
    max_bytes: int,
    expected_size: int | None = None,
    hasher: Any | None = None,
    max_seconds: float | None = None,
    chunk_size: int = 1 << 20,
) -> int:
    """Count and optionally hash a response into an atomic destination swap."""
    raw_length = response.headers.get("Content-Length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        content_length = None
    if content_length is not None and content_length > max_bytes:
        raise DataUnavailableError(
            f"download Content-Length {content_length} exceeds {max_bytes} bytes"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=destination.parent, suffix=".download.tmp")
    tmp_path = Path(tmp_name)
    started = time.monotonic()
    written = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_bytes(chunk_size):
                written += len(chunk)
                if written > max_bytes:
                    raise DataUnavailableError(f"download exceeded {max_bytes} bytes")
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    raise DataUnavailableError(f"download exceeded {max_seconds:g} seconds")
                handle.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)
        if expected_size is not None and written != expected_size:
            raise DataUnavailableError(
                f"download size mismatch: expected {expected_size}, received {written}"
            )
        os.replace(tmp_path, destination)
        return written
    finally:
        tmp_path.unlink(missing_ok=True)


def copy_bounded(source: BinaryIO, destination: BinaryIO, *, max_bytes: int) -> int:
    """Copy a decompressed stream without trusting declared expansion sizes."""
    written = 0
    while chunk := source.read(min(1 << 20, max_bytes - written + 1)):
        written += len(chunk)
        if written > max_bytes:
            raise DataUnavailableError(f"expanded artifact exceeded {max_bytes} bytes")
        destination.write(chunk)
    return written


def read_bounded(
    response: httpx.Response, *, max_bytes: int, label: str, chunk_size: int = 1 << 20
) -> bytes:
    """Read a small response body without buffering beyond its configured limit."""
    raw_length = response.headers.get("Content-Length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        content_length = None
    if content_length is not None and content_length > max_bytes:
        raise DataUnavailableError(
            f"{label} Content-Length {content_length} exceeds {max_bytes} bytes"
        )
    body = bytearray()
    for chunk in response.iter_bytes(min(chunk_size, max_bytes + 1)):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise DataUnavailableError(f"{label} exceeded {max_bytes} bytes")
    return bytes(body)
