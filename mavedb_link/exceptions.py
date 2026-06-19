"""Custom exceptions for mavedb-link.

These typed errors flow from the API client / services into the MCP envelope,
which classifies each into a stable ``error_code`` (see
``mavedb_link.mcp.envelope``). The data plane RAISES these; the MCP plane
RETURNS a structured error dict — they are never raised to the client.
"""

from __future__ import annotations

from typing import Any


class MaveDBError(Exception):
    """Base exception for all mavedb-link data/client errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Store a human-readable message and optional HTTP status code."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        """Return the message (with status code when present)."""
        if self.status_code is not None:
            return f"[{self.status_code}] {self.message}"
        return self.message


class InvalidInputError(MaveDBError):
    """A tool/service argument failed validation before any lookup ran."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        *,
        allowed: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        """Initialise with the offending field and optional recovery data.

        ``allowed`` and ``hint`` are surfaced as structured top-level keys on the
        error envelope (``allowed_values``/``hint``) so a consumer never has to
        parse them out of a (length-capped) message.
        """
        super().__init__(message, status_code=422)
        self.field = field
        self.allowed = allowed
        self.hint = hint


class NotFoundError(MaveDBError):
    """A lookup returned no record for an otherwise valid identifier.

    For a free-text miss the service may attach ``suggestions`` (the closest
    search hits) so the envelope can chain straight to the answer instead of
    merely routing the client back to the search tool.
    """

    def __init__(
        self,
        message: str = "No matching MaveDB record found.",
        *,
        suggestions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialise with a 404 status code and optional close-match suggestions."""
        super().__init__(message, status_code=404)
        self.suggestions = suggestions or []


class AmbiguousQueryError(MaveDBError):
    """A query matched several records and cannot be resolved unambiguously."""

    def __init__(self, message: str, *, candidates: list[dict[str, str]] | None = None) -> None:
        """Store the ambiguous candidates so the envelope can surface them."""
        super().__init__(message)
        self.candidates = candidates or []


class DataUnavailableError(MaveDBError):
    """Required local/derived data is missing or unreadable."""

    def __init__(self, message: str = "The requested data is not available.") -> None:
        """Initialise with a 503 status code."""
        super().__init__(message, status_code=503)


class RateLimitError(MaveDBError):
    """An upstream endpoint signalled rate limiting (HTTP 429)."""

    def __init__(self, message: str = "Upstream rate limit hit.") -> None:
        """Initialise with a 429 status code."""
        super().__init__(message, status_code=429)


class ServiceUnavailableError(MaveDBError):
    """The upstream MaveDB API is temporarily unavailable (5xx / network error)."""

    def __init__(self, message: str = "The MaveDB API is temporarily unavailable.") -> None:
        """Initialise with a 503 status code."""
        super().__init__(message, status_code=503)
