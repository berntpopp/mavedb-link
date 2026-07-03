"""MCP envelope boundary: success/_meta injection and structured errors.

Tools return a plain dict; :func:`run_mcp_tool` injects ``success`` and ``_meta``
on success, and converts any exception into a structured error dict (returned,
never raised) so the LLM sees a typed failure rather than an opaque masked
message.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from mavedb_link.constants import RESPONSE_TOKEN_BUDGET, TOKEN_ESTIMATE_CHARS_PER_TOKEN
from mavedb_link.data import provenance
from mavedb_link.exceptions import (
    AmbiguousQueryError,
    DataUnavailableError,
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from mavedb_link.mcp import metrics
from mavedb_link.mcp.next_commands import cmd, default_error_next_commands
from mavedb_link.services.shaping import DEFAULT_RESPONSE_MODE

logger = logging.getLogger(__name__)

_RETRYABLE = {"rate_limited", "upstream_unavailable", "data_unavailable"}
#: response_modes whose richer enrichment can fail where a leaner one succeeds.
_RICH_MODES = ("standard", "full")


@dataclass
class McpErrorContext:
    """Per-call context so envelopes can name the failing tool and recovery."""

    tool_name: str
    fallback: dict[str, Any] | None = field(default=None)
    arguments: dict[str, Any] = field(default_factory=dict)
    #: The caller's verbosity, used to tier _meta (see :func:`_shape_meta`).
    response_mode: str = DEFAULT_RESPONSE_MODE


class McpToolError(Exception):
    """Raised inside a tool body to emit a specific error code/message."""

    def __init__(self, *, error_code: str, message: str) -> None:
        """Store an error code and client-safe message."""
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def _capabilities_version() -> str | None:
    """Cached discovery-contract hash for the ``_meta`` echo (never raises)."""
    try:
        from mavedb_link.mcp.capabilities import capabilities_version

        return capabilities_version()
    except Exception:  # pragma: no cover - the _meta echo must never break a tool
        return None


def _safe_message(exc: BaseException) -> str:
    return (str(exc) or exc.__class__.__name__)[:280]


def _classify(exc: BaseException) -> tuple[str, str]:
    """Return ``(error_code, client_safe_message)`` for an exception."""
    if isinstance(exc, McpToolError):
        return exc.error_code, exc.message
    if isinstance(exc, NotFoundError):
        return "not_found", _safe_message(exc)
    if isinstance(exc, AmbiguousQueryError):
        return "ambiguous_query", _safe_message(exc)
    if isinstance(exc, InvalidInputError):
        return "invalid_input", _safe_message(exc)
    if isinstance(exc, DataUnavailableError):
        return "data_unavailable", _safe_message(exc)
    if isinstance(exc, RateLimitError):
        return "rate_limited", "Upstream rate limit hit. Retry shortly."
    if isinstance(exc, ServiceUnavailableError):
        return "upstream_unavailable", "The MaveDB API is temporarily unavailable."
    if isinstance(exc, PydanticValidationError):
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "input"
        return "invalid_input", f"Invalid input -- `{loc}`: {first['msg']}"
    return (
        "internal_error",
        "An unexpected internal error occurred and the request was not completed. "
        "Retry; if it persists, lower response_mode or call get_diagnostics.",
    )


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Public per-item classifier: ``(error_code, client-safe message)``."""
    return _classify(exc)


def _recovery_action(error_code: str) -> str:
    if error_code in _RETRYABLE:
        return "retry_backoff"
    if error_code in {"invalid_input", "not_found", "ambiguous_query"}:
        return "reformulate_input"
    return "switch_tool"


def _lower_mode_step(context: McpErrorContext) -> dict[str, Any]:
    """A ready-to-run re-call of the same tool at a lower verbosity (GAP-2)."""
    args = {k: v for k, v in context.arguments.items() if k != "response_mode"}
    return cmd(context.tool_name, **args, response_mode="compact")


def _error_envelope(exc: BaseException, context: McpErrorContext) -> dict[str, Any]:
    error_code, message = _classify(exc)
    retryable = error_code in _RETRYABLE
    recovery = _recovery_action(error_code)
    lower_mode = False
    # GAP-2/0.3: an internal error while assembling a richer view often clears at a
    # lower verbosity, so make it honest -- retryable, with a concrete remedy --
    # rather than a terminal opaque failure.
    if error_code == "internal_error" and context.response_mode in _RICH_MODES:
        retryable = True
        recovery = "lower_response_mode"
        lower_mode = True
        message = (
            f"An internal error occurred while assembling the '{context.response_mode}' "
            f"response for {context.tool_name}. This often clears at a lower verbosity "
            "-- retry with response_mode='compact'."
        )
    envelope: dict[str, Any] = {
        "success": False,
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
        "recovery_action": recovery,
        "_meta": {
            "tool": context.tool_name,
            "request_id": _request_id(),
            # Fleet Response-Envelope Standard v1: the research-use disclaimer is a
            # per-call _meta flag, not a one-time capability, so every error carries
            # it too (see _MANDATORY_META_KEYS for how it survives minimal-mode tiering).
            "unsafe_for_clinical_use": True,
        },
    }
    if lower_mode:
        envelope["_meta"]["next_commands"] = [_lower_mode_step(context)]
        return envelope
    if isinstance(exc, InvalidInputError):
        if exc.field is not None:
            envelope["field"] = exc.field
        if exc.allowed is not None:
            envelope["allowed_values"] = exc.allowed
        if exc.hint is not None:
            envelope["hint"] = exc.hint
    if isinstance(exc, AmbiguousQueryError) and exc.candidates:
        envelope["candidates"] = exc.candidates
        envelope["_meta"]["next_commands"] = [
            cmd("get_score_set", urn=c["urn"]) for c in exc.candidates[:3] if c.get("urn")
        ] or [cmd("get_server_capabilities")]
        return envelope
    if isinstance(exc, NotFoundError) and exc.suggestions:
        envelope["candidates"] = exc.suggestions
        steps = [cmd("get_score_set", urn=s["urn"]) for s in exc.suggestions[:3] if s.get("urn")]
        envelope["_meta"]["next_commands"] = steps or [cmd("get_server_capabilities")]
        return envelope
    if context.fallback is not None:
        envelope["_meta"]["next_commands"] = [context.fallback]
    else:
        envelope["_meta"]["next_commands"] = default_error_next_commands(
            context.tool_name, error_code, context.arguments
        )
    return envelope


def build_arg_error_envelope(
    *,
    tool_name: str,
    loc: str,
    error_type: str,
    valid_params: list[str],
    signature: str,
    suggestion: str | None,
    constraints: tuple[list[str], str] | None = None,
) -> dict[str, Any]:
    """Standard invalid-input envelope for an argument-binding failure."""
    if constraints is not None:
        allowed, human = constraints
        message = f"Invalid value for argument `{loc}` of {tool_name}: {human}."
        return {
            "success": False,
            "error_code": "invalid_input",
            "message": message[:280],
            "retryable": False,
            "recovery_action": "reformulate_input",
            "field": loc,
            "allowed_values": allowed,
            "hint": signature,
            "_meta": {
                "tool": tool_name,
                "request_id": _request_id(),
                "next_commands": [cmd("get_server_capabilities")],
                "unsafe_for_clinical_use": True,
            },
        }
    if error_type == "missing_argument":
        head = f"Missing required argument `{loc}` for {tool_name}."
    elif error_type == "unexpected_keyword_argument":
        head = f"Unknown argument `{loc}` for {tool_name}."
    else:
        head = f"Invalid value for argument `{loc}` of {tool_name}."
    dym = f" Did you mean `{suggestion}`?" if suggestion else ""
    message = f"{head}{dym} Valid argument names are listed in allowed_values."
    return {
        "success": False,
        "error_code": "invalid_input",
        "message": message[:280],
        "retryable": False,
        "recovery_action": "reformulate_input",
        "field": loc,
        "allowed_values": valid_params,
        "hint": signature,
        "_meta": {
            "tool": tool_name,
            "request_id": _request_id(),
            "next_commands": [cmd("get_server_capabilities")],
            "unsafe_for_clinical_use": True,
        },
    }


def _stamp_capabilities_version(meta: dict[str, Any]) -> None:
    """Add the cached capabilities_version to a ``_meta`` block when available."""
    version = _capabilities_version()
    if version:
        meta["capabilities_version"] = version


#: Observability scalars present in EVERY response's _meta, at every tier (GAP-5).
#: ``data_source``/``mirror_as_of`` are present only when the mirror is active.
_OBSERVABILITY_KEYS = (
    "tool",
    "request_id",
    "elapsed_ms",
    "truncated",
    "data_source",
    "mirror_as_of",
)

#: Keys that must survive ``minimal``-mode tiering even though they are not
#: observability scalars. Fleet Response-Envelope Standard v1: the research-use
#: disclaimer is a per-call safety flag, not guidance, so it is never subject to
#: the ``minimal`` opt-out that drops ``next_commands``/``capabilities_version``.
_MANDATORY_META_KEYS = ("unsafe_for_clinical_use",)


def _shape_meta(meta: dict[str, Any], response_mode: str) -> dict[str, Any]:
    """Tier ``_meta`` verbosity by ``response_mode`` while keeping observability uniform.

    Every tier carries the observability scalars (``tool``, ``request_id``,
    ``elapsed_ms``, ``truncated``; ``token_estimate`` is appended afterwards) so a
    caller always has a reliable completeness + latency signal (GAP-5, G7).
    ``minimal`` is the guidance opt-out: it drops ``next_commands`` and
    ``capabilities_version``; the richer tiers keep the full block. The
    ``_MANDATORY_META_KEYS`` (currently just ``unsafe_for_clinical_use``) are
    special-cased to survive the ``minimal`` filter too -- the safety disclaimer is
    never guidance a caller can opt out of.
    """
    if response_mode == "minimal":
        keys = (*_OBSERVABILITY_KEYS, *_MANDATORY_META_KEYS)
        return {k: meta[k] for k in keys if k in meta}
    return meta


def _estimate_tokens(payload: dict[str, Any]) -> int:
    """Rough token estimate of a payload (chars/4); never raises on odd values."""
    try:
        chars = len(json.dumps(payload, default=str))
    except Exception:  # pragma: no cover - estimate must never break a tool
        return 0
    return chars // TOKEN_ESTIMATE_CHARS_PER_TOKEN


#: Token headroom reserved for the _meta the budget guard appends AFTER trimming
#: (steer text + a prepended next_command), so the final envelope stays under cap.
_BUDGET_META_MARGIN = 800


def _page_list_key(result: dict[str, Any], returned: int) -> str | None:
    """The top-level list field that IS the page (``len == returned``), largest on tie.

    Identifies the page generically from the pagination contract -- no domain field
    names -- so the envelope can trim ANY list tool without coupling to its shape.
    """
    candidates = [k for k, v in result.items() if isinstance(v, list) and len(v) == returned]
    if not candidates:
        return None
    return max(candidates, key=lambda k: len(json.dumps(result[k], default=str)))


def _trim_list_to_budget(result: dict[str, Any], target: int) -> bool:
    """Drop trailing rows from a list page until it fits ``target`` tokens (A.2).

    Returns True if it trimmed. Acts ONLY on a payload carrying the pagination
    contract (an int ``returned`` + a list field of that length) -- a record has no
    such field and is left intact (trimming it would corrupt the structured output).
    Re-pages honestly: updates ``returned``, ``truncated``, and the ``next_offset``/
    ``next_start`` continuation cursors so the dropped rows stay reachable.
    """
    returned = result.get("returned")
    if not isinstance(returned, int) or returned <= 0:
        return False
    key = _page_list_key(result, returned)
    if key is None:
        return False
    page = result[key]
    offset = result.get("offset")
    if not isinstance(offset, int):
        offset = result["start"] if isinstance(result.get("start"), int) else 0
    # Per-row cost = the page's share of the estimate; size the keep from the fixed
    # overhead (everything but the page), then tighten for uneven rows.
    result[key] = []
    base = _estimate_tokens(result)
    result[key] = page
    per_item = max(1, (_estimate_tokens(result) - base) // len(page))
    keep = max(0, min((target - base) // per_item, len(page) - 1))
    result[key] = page[:keep]
    while keep > 0 and _estimate_tokens(result) > target:
        keep = max(0, keep - max(1, keep // 10))
        result[key] = page[:keep]
    result["returned"] = keep
    result["truncated"] = True
    if "next_offset" in result or "offset" in result:
        result["next_offset"] = offset + keep
    if "next_start" in result or "start" in result:
        start = result["start"] if isinstance(result.get("start"), int) else offset
        result["next_start"] = start + keep
    return True


def _apply_budget_guard(result: dict[str, Any], context: McpErrorContext) -> None:
    """Enforce the token budget (1.3 / A.2): trim a list page, else flag + steer.

    A list page over the cap is deterministically trimmed (rows dropped, ``returned``
    reduced, response kept re-pageable) so the front door returns data, never a
    client rejection. A record payload has no page contract, so it is left intact
    and only flagged/steered. Either way ``_meta`` carries ``truncated`` +
    ``budget_exceeded`` and a concrete steer; rich modes also get a leaner re-call.
    """
    meta = result["_meta"]
    if meta.get("token_estimate", 0) <= RESPONSE_TOKEN_BUDGET:
        return
    meta["truncated"] = True
    meta["budget_exceeded"] = True
    trimmed = _trim_list_to_budget(result, RESPONSE_TOKEN_BUDGET - _BUDGET_META_MARGIN)
    if trimmed:
        meta["steer"] = (
            f"Response trimmed to {result.get('returned')} of {result.get('total')} rows "
            f"to fit the {RESPONSE_TOKEN_BUDGET}-token budget. Page forward via "
            "offset=/start=, or re-call with response_mode='minimal' for more rows per page."
        )
    else:
        meta["steer"] = (
            f"Response is ~{meta['token_estimate']} tokens (> the {RESPONSE_TOKEN_BUDGET}-token "
            "budget). Re-call with a smaller limit=, response_mode='compact'/'minimal', or page "
            "via offset=/start=."
        )
    if context.response_mode in _RICH_MODES:
        steps = meta.get("next_commands")
        if isinstance(steps, list):
            meta["next_commands"] = [_lower_mode_step(context), *steps]
    if trimmed:  # recompute over the trimmed payload incl. the steer/next_commands
        meta["token_estimate"] = _estimate_tokens(result)


async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
) -> dict[str, Any]:
    """Execute a tool body, returning the result dict or a structured error dict."""
    ctx = context or McpErrorContext(tool_name=tool_name)
    provenance.begin()
    start = time.perf_counter()
    try:
        result = await call()
        elapsed = int((time.perf_counter() - start) * 1000)
        if isinstance(result, dict):
            existing_meta: dict[str, Any] = result.get("_meta") or {}
            success = bool(result.setdefault("success", True))
            meta = {
                **existing_meta,
                "tool": tool_name,
                "request_id": _request_id(),
                "elapsed_ms": elapsed,
                # Hoist a uniform completeness signal from the body (GAP-5/G7).
                "truncated": bool(result.get("truncated")),
                # Honest mirror-vs-live provenance for this call (empty if no mirror).
                **provenance.snapshot(),
                # Fleet Response-Envelope Standard v1: per-call research-use
                # disclaimer, stamped last so no tool body can accidentally
                # override it via existing_meta.
                "unsafe_for_clinical_use": True,
            }
            _stamp_capabilities_version(meta)
            result["_meta"] = _shape_meta(meta, ctx.response_mode)
            # token_estimate is computed over the (near-final) payload, then the
            # budget guard may flag/steer an over-budget response (1.3/1.4).
            result["_meta"]["token_estimate"] = _estimate_tokens(result)
            _apply_budget_guard(result, ctx)
            metrics.record(tool_name, elapsed, ok=success)
        return result
    except Exception as exc:  # broad catch is the error-boundary contract
        elapsed = int((time.perf_counter() - start) * 1000)
        envelope = _error_envelope(exc, ctx)
        envelope["_meta"]["elapsed_ms"] = elapsed
        envelope["_meta"]["truncated"] = False
        envelope["_meta"].update(provenance.snapshot())
        _stamp_capabilities_version(envelope["_meta"])
        envelope["_meta"] = _shape_meta(envelope["_meta"], ctx.response_mode)
        envelope["_meta"]["token_estimate"] = _estimate_tokens(envelope)
        metrics.record(tool_name, elapsed, ok=False)
        logger.warning(
            "mcp_tool_error tool=%s code=%s exc=%s",
            tool_name,
            envelope["error_code"],
            exc.__class__.__name__,
        )
        return envelope
