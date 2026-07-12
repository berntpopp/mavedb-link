"""Build-hardening regression: the Docker builder bootstrap must be reproducible.

Finding F-19: the builder stage bootstrapped uv via a floating ``pip install
--upgrade pip uv``. Pin the exact uv artifact by digest (copied from the
official image) so the toolchain the image builds with is deterministic.
"""

from __future__ import annotations

from pathlib import Path

_DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "Dockerfile"

#: The fleet-shared uv pin (matches the router's own docker/Dockerfile anchor).
_UV_PIN = (
    "ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab"
)


def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade() -> None:
    text = _DOCKERFILE.read_text()
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert _UV_PIN in text, "uv must be COPY-pinned by digest from the official image"
