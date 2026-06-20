"""Phase E gates: lock this round's token/enforcement fixes from regressing.

Facade-level (full stack: client -> service -> shaping -> envelope), so the gates
exercise the real token estimate + budget enforcement an MCP host would see.
These run inside `make ci-local` via test-fast, alongside the eval regression gate.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mavedb_link.constants import RESPONSE_TOKEN_BUDGET
from mavedb_link.services.shaping import RESPONSE_MODES
from tests import fixtures


def _heavy_score_set(i: int) -> dict[str, Any]:
    """A realistically heavy BRCA1 search record (big free text drives full mode)."""
    return {
        "urn": f"urn:mavedb:{i:08d}-a-1",
        "title": f"BRCA1 saturation genome editing assay {i}",
        "shortDescription": "Functional scores from an SGE screen. " + "x" * 200,
        "numVariants": 1000 + i,
        "license": {"shortName": "CC0"},
        "targetGenes": [
            {
                "name": "BRCA1",
                "category": "protein_coding",
                "targetSequence": {"taxonomy": {"organismName": "Homo sapiens"}},
            }
        ],
        "primaryPublicationIdentifiers": [
            {
                "dbName": "PubMed",
                "identifier": str(20000000 + i),
                "publicationYear": 2020,
                "doi": f"10.1000/mave.{i}",
            }
        ],
        "experiment": {"urn": f"urn:mavedb:{i:08d}-a"},
        "abstractText": "A" * 2200,
        "methodText": "M" * 2200,
        "datasetColumns": {"score_columns": ["score", "sd", "se"]},
        "scoreRanges": {"wt": [0.0, 1.0]},
    }


@pytest.mark.parametrize("mode", RESPONSE_MODES)
async def test_search_score_sets_under_cap_at_every_tier(
    mode: str, facade: Any, structured: Any, respx_router: Any
) -> None:
    # E.1 (GAP-A.2 lock): the front-door search must never breach the host's 25k cap
    # at ANY tier -- the envelope trims the page deterministically and stays honest.
    big = [_heavy_score_set(i) for i in range(100)]
    respx_router.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": big, "numScoreSets": 100})
    )
    res = await facade.call_tool(
        "search_score_sets", {"text": "BRCA1", "limit": 100, "response_mode": mode}
    )
    payload = structured(res)
    meta = payload["_meta"]
    assert meta["token_estimate"] <= RESPONSE_TOKEN_BUDGET, f"{mode} breached the cap"
    assert payload["returned"] == len(payload["results"])
    if meta.get("budget_exceeded"):  # if it had to trim, it stays honest + re-pageable
        assert payload["truncated"] is True
        assert payload["returned"] < 100
        assert payload["next_offset"] == payload["returned"]


async def test_variant_scores_forward_page_drops_duplicated_ladder(
    facade: Any, structured: Any, respx_router: Any
) -> None:
    # E.3 (GAP-D lock): the calibration ladder ships once (page 0); a forward page is
    # strictly leaner and carries no duplicated ladder, only the per-row class.
    respx_router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_router.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )

    async def page(start: int) -> dict[str, Any]:
        res = await facade.call_tool(
            "get_variant_scores", {"urn": fixtures.SCORE_SET_URN, "start": start, "limit": 2}
        )
        return structured(res)

    first, later = await page(0), await page(1)
    assert "calibrations" in first
    assert "calibrations" not in later
    assert later["_meta"]["token_estimate"] < first["_meta"]["token_estimate"]
