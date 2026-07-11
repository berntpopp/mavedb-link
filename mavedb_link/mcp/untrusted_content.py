"""Typed structural fencing for externally sourced prose at the MCP boundary."""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FORBIDDEN_CODEPOINTS = frozenset(
    {
        *range(0x0000, 0x0009),
        *range(0x000B, 0x000D),
        *range(0x000E, 0x0020),
        *range(0x007F, 0x00A0),
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0xFEFF,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)


class UntrustedTextProvenance(BaseModel):
    """Source identity for one fenced external text object."""

    source: str
    record_id: str
    retrieved_at: datetime


class UntrustedText(BaseModel):
    """External prose represented as typed data with digest and provenance."""

    kind: Literal["untrusted_text"] = "untrusted_text"
    text: str
    provenance: UntrustedTextProvenance
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def fence_untrusted_text(raw: str, *, source: str, record_id: str) -> UntrustedText:
    """Normalize external prose and remove only the ratified control characters."""
    normalized = unicodedata.normalize("NFC", raw)
    clean = "".join(char for char in normalized if ord(char) not in FORBIDDEN_CODEPOINTS)
    return UntrustedText(
        text=clean,
        provenance=UntrustedTextProvenance(
            source=source,
            record_id=record_id,
            retrieved_at=datetime.now(UTC),
        ),
        raw_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def fence_prose(value: Any, *, source: str, record_id: str) -> dict[str, Any] | None:
    """Fence one OPTIONAL free-text field at a shaping boundary, or return None.

    Convenience wrapper over :func:`fence_untrusted_text`: returns ``None`` for a
    non-string or empty value (so a shaper's drop-empty / null-passthrough behaviour
    is preserved) and otherwise the JSON-serialised typed object. Shared by every
    MaveDB shaping module so the reshape is identical everywhere.
    """
    if not isinstance(value, str) or not value:
        return None
    return fence_untrusted_text(value, source=source, record_id=record_id).model_dump(mode="json")


DEFAULT_MAX_TEXT_BYTES = 2_097_152
DEFAULT_MAX_OBJECTS = 128
DEFAULT_MAX_TOTAL_TEXT_BYTES = 8_388_608


class UntrustedTextLimitError(ValueError):
    """A fenced object or response exceeded a Response-Envelope v1.1 ceiling.

    Raised as an explicit, typed execution error — the standard forbids silent
    omission when a limit is exceeded.
    """


def enforce_untrusted_text_limits(
    objects: list[UntrustedText],
    *,
    max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES,
    max_objects: int = DEFAULT_MAX_OBJECTS,
    max_total_text_bytes: int = DEFAULT_MAX_TOTAL_TEXT_BYTES,
) -> None:
    """Raise UntrustedTextLimitError if the fenced objects exceed any v1.1 ceiling.

    Depth is satisfied structurally: a fenced `text` is a leaf string, so the
    untrusted subtree never nests. Callers pass every UntrustedText they emit in
    one response.
    """
    if len(objects) > max_objects:
        raise UntrustedTextLimitError(
            f"untrusted object count {len(objects)} exceeds ceiling {max_objects}"
        )
    total = 0
    for obj in objects:
        n = len(obj.text.encode("utf-8"))
        if n > max_text_bytes:
            raise UntrustedTextLimitError(
                f"untrusted text {n} bytes exceeds per-object ceiling {max_text_bytes}"
            )
        total += n
    if total > max_total_text_bytes:
        raise UntrustedTextLimitError(
            f"untrusted total {total} bytes exceeds ceiling {max_total_text_bytes}"
        )


def _walk_untrusted(value: Any, out: list[UntrustedText]) -> None:
    if isinstance(value, dict):
        if value.get("kind") == "untrusted_text":
            out.append(UntrustedText.model_validate(value))
            return  # a fenced text is a leaf; do not descend into its provenance
        for child in value.values():
            _walk_untrusted(child, out)
    elif isinstance(value, list):
        for child in value:
            _walk_untrusted(child, out)


def collect_untrusted_texts(value: Any) -> list[UntrustedText]:
    """Collect every fenced untrusted_text object in a serialized response tree.

    Walks the whole payload (dicts + lists) and reconstructs each
    ``kind == "untrusted_text"`` subtree as an :class:`UntrustedText`, so one
    response-level :func:`enforce_untrusted_text_limits` sweep can bound the
    object-count and total-byte ceilings across all rows a response emits.
    """
    out: list[UntrustedText] = []
    _walk_untrusted(value, out)
    return out


#: Length cap for caller-visible free-text message/error strings.
MAX_MESSAGE_CHARS = 280


def sanitize_message(text: str) -> str:
    """Strip the fence's forbidden control/zero-width/bidi code points + length-cap.

    A defensive belt-and-suspenders applied to EVERY caller-visible message/error
    string. A hostile upstream (or a caller-influenced 4xx/5xx body) must never
    smuggle control, zero-width, bidirectional, or NUL code points into an error
    frame, diagnostics, or any status message. Caller-visible messages are
    server-authored guidance data, never instructions and never fenced objects;
    upstream response bodies are additionally kept out of them at the source.
    """
    clean = "".join(char for char in text if ord(char) not in FORBIDDEN_CODEPOINTS)
    return clean[:MAX_MESSAGE_CHARS]
