"""Resolution failures never reflect caller HGVS values (R-04)."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from mavedb_link.exceptions import AmbiguousQueryError, InvalidInputError, NotFoundError
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.services import resolvers
from mavedb_link.services.variant_lookup import get_variant_score

_HOSTILE_HGVS = "NM_012345.6:c.4242G>C"
_SCORE_SET_URN = "urn:mavedb:00000001-a-1"


class _ScoresClient:
    async def get_text(self, _path: str, *, params: Any = None) -> str:
        return "accession,hgvs_nt,score\nurn:mavedb:00000001-a-1#1,c.1A>T,0.2\n"


class _MirrorClient:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    def vrs_for_hgvs(
        self, _core: str, _full: str | None = None, *, gene: str | None = None
    ) -> list[dict[str, str]]:
        return self._rows


class _LiveProbeClient(_MirrorClient):
    async def get_json(self, _path: str, *, params: Any = None) -> dict[str, Any]:
        return {"scoreSets": [{"urn": _SCORE_SET_URN}]}

    async def get_text(self, _path: str, *, params: Any = None) -> str:
        return "accession,hgvs_nt,score\nurn:mavedb:00000001-a-1#1,c.1A>T,0.2\n"


async def _error_envelope(
    tool_name: str, call: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    return await run_mcp_tool(
        tool_name,
        call,
        context=McpErrorContext(tool_name, arguments={"hgvs": _HOSTILE_HGVS}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_exception", "expected_code", "call"),
    [
        (
            NotFoundError,
            "not_found",
            lambda: get_variant_score(_ScoresClient(), _SCORE_SET_URN, hgvs=_HOSTILE_HGVS),
        ),
        (
            AmbiguousQueryError,
            "ambiguous_query",
            lambda: resolvers._vrs_from_hgvs(
                _MirrorClient(
                    [
                        {"vrs_id": "ga4gh:VA.one"},
                        {"vrs_id": "ga4gh:VA.two"},
                    ]
                ),
                _HOSTILE_HGVS,
                None,
            ),
        ),
        (
            InvalidInputError,
            "invalid_input",
            lambda: resolvers._vrs_from_hgvs(_MirrorClient([]), _HOSTILE_HGVS, None),
        ),
    ],
)
async def test_hgvs_resolution_failures_do_not_reflect_input(
    caplog: pytest.LogCaptureFixture,
    expected_exception: type[Exception],
    expected_code: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
) -> None:
    """Not-found, ambiguous, and live-resolution failures retain typed recovery only."""
    with pytest.raises(expected_exception) as exc:
        await call()
    assert _HOSTILE_HGVS not in str(exc.value)

    with caplog.at_level(logging.WARNING):
        envelope = await _error_envelope("find_variant", call)

    assert envelope["error_code"] == expected_code
    assert envelope["retryable"] is False
    assert envelope["recovery_action"] == "reformulate_input"
    assert _HOSTILE_HGVS not in json.dumps(envelope)
    assert _HOSTILE_HGVS not in caplog.text


@pytest.mark.asyncio
async def test_live_hgvs_probe_miss_does_not_reflect_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The changed live-probe no-match path keeps the HGVS private too."""
    client = _LiveProbeClient([])

    async def call() -> dict[str, Any]:
        await resolvers._vrs_from_hgvs(client, _HOSTILE_HGVS, "BRCA1")
        raise AssertionError("the no-match probe must raise")

    with pytest.raises(NotFoundError) as exc:
        await call()
    assert _HOSTILE_HGVS not in str(exc.value)

    with caplog.at_level(logging.WARNING):
        envelope = await _error_envelope("find_variant", call)

    assert envelope["error_code"] == "not_found"
    assert envelope["retryable"] is False
    assert envelope["recovery_action"] == "reformulate_input"
    assert _HOSTILE_HGVS not in json.dumps(envelope)
    assert _HOSTILE_HGVS not in caplog.text
