"""Advisory build lock so concurrent processes don't rebuild the mirror at once."""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def build_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive flock for the duration of a build (released on exit)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")  # noqa: SIM115 (held for the context's lifetime)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
